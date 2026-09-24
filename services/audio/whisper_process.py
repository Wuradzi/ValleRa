"""One resident Whisper process, explicitly terminable during shutdown.

Cancelling to_thread cannot stop CTranslate2 inference. Only PCM and STT
settings cross this local pipe; API keys and conversation history do not.
"""

from __future__ import annotations

import multiprocessing
import logging
import signal
import threading
import time
from types import SimpleNamespace

from core.models import RecognitionResult
from core.logging_setup import configure_worker_logging, session_queue
from core.performance import DISABLED_PERFORMANCE
from services.audio.whisper import WhisperRecognizer


def _worker(connection, settings):
    capture = configure_worker_logging(getattr(settings, "session_log_queue", None))
    try:
        # The parent owns Ctrl+C and terminates/reaps this worker in close().
        # Windows delivers console interrupts to child processes as well.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        recognizer = WhisperRecognizer(settings)
        while True:
            operation, args = connection.recv()
            recognizer.last_timings = {}
            operation_started = time.perf_counter()
            if operation == "prepare":
                result = recognizer.prepare()
            elif operation == "transcribe":
                result = recognizer.transcribe(*args)
            else:
                return
            timings = dict(recognizer.last_timings)
            timings[f"whisper.worker_{operation}"] = (time.perf_counter() - operation_started) * 1000
            connection.send((result, recognizer.status(), recognizer.last_duration_seconds, timings))
    except (EOFError, BrokenPipeError, OSError, KeyboardInterrupt):
        pass
    except Exception:
        logging.getLogger(__name__).exception("Whisper worker failed")
    finally:
        connection.close()
        if capture is not None:
            capture.close()


class ProcessWhisperRecognizer:
    def __init__(self, settings, *, worker_target=_worker):
        fields = {
            name: getattr(settings, name)
            for name in dir(settings)
            if name.startswith("stt_whisper_")
        }
        self.settings = SimpleNamespace(
            **fields,
            language=settings.language,
            paths=SimpleNamespace(models_dir=settings.paths.models_dir),
        )
        self.enabled = settings.stt_whisper_enabled
        self._target = worker_target
        self._process = None
        self._connection = None
        self._closed = threading.Event()
        self._request_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._status = WhisperRecognizer(self.settings).status()
        self.last_duration_seconds = 0.0
        self.skipped_silence = 0
        self.skipped_fast_chat = 0
        self._retry_after = 0.0
        self.performance = DISABLED_PERFORMANCE
        self.last_timings = {}

    def status(self):
        return self._status

    def note_skip(self, reason):
        if reason == "silence":
            self.skipped_silence += 1
        elif reason == "fast-chat":
            self.skipped_fast_chat += 1

    def _request(self, operation, *args):
        started = time.perf_counter()
        with self._request_lock:
            self.performance.record("whisper.request_lock_wait", (time.perf_counter() - started) * 1000)
            with self._state_lock:
                if self._closed.is_set():
                    raise RuntimeError("Whisper зупинено")
                if self._process is None:
                    self.settings.measure_performance = self.performance.enabled
                    self.settings.session_log_queue = session_queue()
                    context = multiprocessing.get_context("spawn")
                    parent, child = context.Pipe()
                    self._process = context.Process(
                        target=self._target,
                        args=(child, self.settings),
                        daemon=True,
                        name="valera-whisper",
                    )
                    self._connection = parent
                    try:
                        with self.performance.span("whisper.spawn"):
                            self._process.start()
                    finally:
                        child.close()
                        if self.settings.session_log_queue is not None:
                            self.settings.session_log_queue.close()
                connection = self._connection
            connection.send((operation, args))
            deadline = time.monotonic() + 120
            while not self._closed.is_set():
                if connection.poll(0.1):
                    payload = connection.recv()
                    result, self._status, self.last_duration_seconds = payload[:3]
                    self.last_timings = payload[3] if len(payload) > 3 else {}
                    for stage, duration in self.last_timings.items():
                        self.performance.record(stage, duration, status="ok" if self._status[0] else "unavailable")
                    self.performance.record(f"whisper.roundtrip_{operation}", (time.perf_counter() - started) * 1000)
                    return result
                if time.monotonic() >= deadline:
                    raise TimeoutError("Whisper перевищив 120 секунд")
            raise RuntimeError("Whisper зупинено")

    def prepare(self):
        if not self.enabled:
            return self.status()
        try:
            return self._request("prepare")
        except Exception as exc:
            self._failed(exc)
            return self.status()

    def transcribe(self, pcm, sample_rate):
        if not self.enabled or self._closed.is_set() or time.monotonic() < self._retry_after:
            return RecognitionResult("", 0.0, "whisper")
        try:
            return self._request("transcribe", pcm, sample_rate)
        except Exception as exc:
            self._failed(exc)
            return RecognitionResult("", 0.0, "whisper")

    def _failed(self, exc):
        self._status = (False, str(exc) or type(exc).__name__)
        self._retry_after = time.monotonic() + 60
        self._terminate()

    def _terminate(self):
        with self._state_lock:
            process, connection = self._process, self._connection
            self._process = self._connection = None
            if process is not None:
                if process.is_alive():
                    process.terminate()
                if process.pid is not None:
                    process.join(timeout=2)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=1)
                if not process.is_alive():
                    process.close()
            if connection is not None:
                connection.close()

    def close(self):
        self._closed.set()
        self._terminate()
