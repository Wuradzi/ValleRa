import asyncio
import multiprocessing
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from config import ProjectPaths, Settings
from core.app import ValleRaApp
from core.models import RecognitionResult
from core.speak import Speaker
from services.audio.whisper_process import ProcessWhisperRecognizer, _worker


class InterruptingConnection:
    """Deliver SIGINT only to the isolated child, never the parent console."""

    def __init__(self, connection):
        self.connection = connection

    def recv(self):
        signal.raise_signal(signal.SIGINT)
        self.connection.send("survived")
        return "close", ()

    def close(self):
        self.connection.close()


def slow_worker(connection, settings):
    connection.recv()
    time.sleep(60)


def echo_worker(connection, settings):
    try:
        while True:
            command, args = connection.recv()
            result = (
                (True, "ready")
                if command == "prepare"
                else RecognitionResult("привіт", 1, "whisper")
            )
            connection.send((result, (True, "ready"), 0.01))
    except (EOFError, OSError):
        pass


class ProcessLifecycleTests(unittest.TestCase):
    def test_worker_ignores_its_own_sigint_and_exits_cleanly(self):
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=_worker, args=(InterruptingConnection(child), self.settings()))
        process.start()
        child.close()
        try:
            self.assertTrue(parent.poll(10))
            self.assertEqual(parent.recv(), "survived")
            process.join(timeout=5)
            self.assertEqual(process.exitcode, 0)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=3)
            process.close()
            parent.close()

    def test_worker_catches_keyboard_interrupt_during_pipe_read(self):
        connection = SimpleNamespace(recv=Mock(side_effect=KeyboardInterrupt), close=Mock())
        with patch("services.audio.whisper_process.signal.signal") as handler:
            _worker(connection, self.settings())
        handler.assert_called_once_with(signal.SIGINT, signal.SIG_IGN)
        connection.close.assert_called_once()

    def settings(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return Settings(ProjectPaths.from_root(Path(temp.name)))

    def test_model_worker_is_reused_and_closed(self):
        worker = ProcessWhisperRecognizer(self.settings(), worker_target=echo_worker)
        try:
            self.assertTrue(worker.prepare()[0])
            pid = worker._process.pid
            self.assertEqual(worker.transcribe(b"\x00\x00", 16000).text, "привіт")
            self.assertEqual(worker._process.pid, pid)
        finally:
            worker.close()
        self.assertIsNone(worker._process)

    def test_shutdown_interrupts_native_work_instead_of_waiting_for_it(self):
        worker = ProcessWhisperRecognizer(self.settings(), worker_target=slow_worker)
        thread = threading.Thread(target=worker.prepare, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while worker._process is None and time.monotonic() < deadline:
                time.sleep(0.01)
            started = time.monotonic()
            worker.close()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertLess(time.monotonic() - started, 4)
        finally:
            worker.close()


class SpeakerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_app_cancellation_closes_native_workers(self):
        speaker = self.speaker()
        worker = ProcessWhisperRecognizer(speaker.settings, worker_target=slow_worker)
        app = object.__new__(ValleRaApp)
        app.settings = speaker.settings
        app.web_ui = None
        app.running = True
        app.text_only = True
        app.speaker = speaker
        app.listener = SimpleNamespace(interrupt=Mock(), close=worker.close)
        app.llm = SimpleNamespace(provisional_statuses=Mock(return_value={}), select_startup_provider=Mock(), close=AsyncMock())
        app.metrics = SimpleNamespace(record=Mock())
        app.services = {"pentest": SimpleNamespace(deactivate=AsyncMock())}

        async def idle():
            await asyncio.Event().wait()

        async def initialize():
            await asyncio.to_thread(worker.prepare)

        app._text_input_loop = app._command_loop = app._reminder_loop = idle
        app._initialize_backends = initialize
        task = asyncio.create_task(app.run())
        try:
            async with asyncio.timeout(3):
                while worker._process is None:
                    await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            self.assertIsNone(worker._process)
            self.assertTrue(speaker._worker_task.done())
            app.llm.close.assert_awaited_once()
        finally:
            worker.close()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    def test_default_windows_output_uses_direct_speech_without_wav(self):
        speaker = self.speaker()
        with (
            patch("core.speak.os.name", "nt"),
            patch.object(speaker, "_generate_with_windows_speech") as synth,
        ):
            speaker._speak_sync("Привіт")
        synth.assert_called_once_with("Привіт", None)

    def speaker(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return Speaker(Settings(ProjectPaths.from_root(Path(temp.name))))

    async def test_known_answer_is_queued_in_phrases(self):
        speaker = self.speaker()
        await speaker.say("Перша фраза. Друга фраза.")
        self.assertEqual(speaker._queue.get_nowait().text, "Перша фраза.")
        self.assertEqual(speaker._queue.get_nowait().text, "Друга фраза.")

    async def test_close_terminates_owned_tts_child_and_drops_pending_audio(self):
        speaker = self.speaker()
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        speaker._tts_process = process
        try:
            await speaker.say("Цю репліку не треба відтворювати.")
            await speaker.start()
            await asyncio.wait_for(speaker.close(), 3)
            process.wait(timeout=2)
            self.assertTrue(speaker._worker_task.done())
            self.assertTrue(speaker._queue.empty())
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()

    async def test_text_reply_starting_during_capture_discards_audio(self):
        app = object.__new__(ValleRaApp)
        app.microphone_enabled = True
        app._microphone_epoch = 0
        finished = threading.Event()
        app.listener = SimpleNamespace(
            listen_once=lambda *_: finished.wait(2) and RecognitionResult("self echo", 1),
            interrupt=Mock(side_effect=finished.set),
        )
        app.speaker = SimpleNamespace(busy=False, say=AsyncMock())
        app.command_idle = asyncio.Event()
        app.command_idle.set()
        task = asyncio.create_task(app._listen_for_turn(10, None))
        await asyncio.sleep(0.02)
        app.speaker.busy = True
        result = await asyncio.wait_for(task, 1)
        self.assertEqual(result.text, "")
        app.listener.interrupt.assert_called_once()
