"""One UTF-8 journal per application session, with a single file writer.

Python stdout/stderr are diagnostics by default. Interactive output explicitly
uses core.console; passwords are never read by the stream capture. Spawned STT
workers send records to the parent's queue, not to the same file descriptor.
"""
from __future__ import annotations

import io
import logging
import multiprocessing
import os
import queue
import re
import sys
import threading
import uuid
import warnings
from datetime import datetime, timezone
from logging.handlers import QueueHandler, QueueListener
from pathlib import Path

from core.security import is_sensitive_env_name, redact_user_text

_session = None
_known_secrets: set[str] = set()
_secrets_lock = threading.Lock()
_credentials = re.compile(
    r'''(?i)(\b(?:api[_-]?key|password|passwd|secret|recovery[_-]?key|access[_-]?token|token)\b["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;&]+)'''
)
_bearer = re.compile(r"(?i)(\bBearer\s+)\S+")


def register_secret(value: str) -> None:
    if value:
        with _secrets_lock:
            _known_secrets.add(value)


def _redact(text: str) -> str:
    with _secrets_lock:
        values = sorted(_known_secrets, key=len, reverse=True)
    for value in values:
        text = text.replace(value, "[REDACTED]")
    text = "\n".join(redact_user_text(line) for line in text.split("\n"))
    return _bearer.sub(r"\1[REDACTED]", _credentials.sub(r"\1[REDACTED]", text))


class RedactingFilter(logging.Filter):
    SENSITIVE = {"password", "secret", "token", "api_key", "recovery_key"}

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if any(word in message.lower() for word in self.SENSITIVE):
            message = "[REDACTED SENSITIVE LOG MESSAGE]"
        record.msg, record.args = _redact(message), ()
        return True


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        # Also redact exception text and traceback lines, not just record.msg.
        return _redact(super().format(record))


class SessionStream(io.TextIOBase):
    """Line-buffered Python output; separate buffers prevent thread interleaving."""

    def __init__(self, console, name, level=logging.INFO):
        self.console = console
        self.logger = logging.getLogger(name)
        self.level = level
        self._buffers: dict[int, str] = {}
        self._lock = threading.RLock()
        self._active = True

    @property
    def encoding(self):
        return getattr(self.console, "encoding", "utf-8")

    def writable(self):
        return True

    def isatty(self):
        return False

    def fileno(self):
        return self.console.fileno()

    def write(self, text):
        if not text or not self._active:
            return len(text)
        ident = threading.get_ident()
        with self._lock:
            pending = self._buffers.get(ident, "") + text
            lines = pending.split("\n")
            self._buffers[ident] = lines.pop()
            for line in lines:
                if line.strip():
                    self.logger.log(self.level, line.rstrip("\r"))
        return len(text)

    def flush(self):
        with self._lock:
            for pending in self._buffers.values():
                if pending.strip() and self._active:
                    self.logger.log(self.level, pending)
            self._buffers.clear()

    def finish(self):
        with self._lock:
            self.flush()
            self._active = False


class QuietQueueHandler(QueueHandler):
    def handleError(self, record):
        # Never recurse through captured stderr if logging itself fails.
        _logging_failure()


class SessionFileHandler(logging.FileHandler):
    def handleError(self, record):
        _logging_failure()


class WorkerLogQueue:
    """A picklable, one-worker sender with the QueueHandler interface.

    Do not share a multiprocessing.Queue with an explicitly terminable worker:
    terminate() can leave its feeder holding the shared queue's write lock.
    A private pipe limits an interrupted write to this worker's last record.
    """

    def __init__(self, connection):
        self.connection = connection

    def put_nowait(self, record):
        self.connection.send(record)

    def close(self):
        self.connection.close()


class WorkerLogBridge:
    def __init__(self, destination):
        receiver, sender = multiprocessing.get_context("spawn").Pipe(duplex=False)
        self.sender = WorkerLogQueue(sender)

        def receive():
            try:
                while True:
                    destination.put_nowait(receiver.recv())
            except (EOFError, OSError):
                pass
            finally:
                receiver.close()

        self.thread = threading.Thread(target=receive, name="whisper-log-reader", daemon=True)
        self.thread.start()

    def close(self):
        self.sender.close()
        self.thread.join(timeout=2)


def _logging_failure():
    if _session is not None and not _session.write_failed:
        _session.write_failed = True
        print("Не вдалося записати журнал сесії. Перевірте вільне місце й доступ до logs/sessions.",
              file=_session.stdout, flush=True)


class OutputCapture:
    def __init__(self, log_queue):
        self.log_queue = log_queue
        self.root = logging.getLogger()
        self.handlers, self.level = self.root.handlers[:], self.root.level
        self.handler = QuietQueueHandler(log_queue)
        self.handler.addFilter(RedactingFilter())
        self.handler.setFormatter(RedactingFormatter())
        self.root.handlers = [self.handler]
        self.root.setLevel(logging.INFO)
        # Terminal transcripts and errors remain recorded even at WARNING level.
        self.logger_levels = {}
        for name in ("session.stdout", "session.stderr", "session.dialogue", "session.lifecycle"):
            logger = logging.getLogger(name)
            self.logger_levels[name] = logger.level
            logger.setLevel(logging.INFO)
        self.stdout, self.stderr = sys.stdout, sys.stderr
        self.out = SessionStream(self.stdout, "session.stdout")
        self.err = SessionStream(self.stderr, "session.stderr", logging.ERROR)
        sys.stdout, sys.stderr = self.out, self.err
        self.warning_hook = warnings.showwarning
        logging.captureWarnings(True)
        self.thread_hook = threading.excepthook
        threading.excepthook = self._thread_error

    @staticmethod
    def _thread_error(args):
        logging.getLogger("session.stderr").error(
            "Unhandled thread error: %s", args.thread.name if args.thread else "unknown",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    def close(self):
        self.out.finish()
        self.err.finish()
        sys.stdout, sys.stderr = self.stdout, self.stderr
        threading.excepthook = self.thread_hook
        logging.captureWarnings(False)
        warnings.showwarning = self.warning_hook
        self.root.handlers = self.handlers
        self.root.setLevel(self.level)
        for name, level in self.logger_levels.items():
            logging.getLogger(name).setLevel(level)
        self.handler.close()
        if isinstance(self.log_queue, WorkerLogQueue):
            self.log_queue.close()


class SessionLogging:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        self.path = self.directory / f"session-{stamp}-{os.getpid()}-{uuid.uuid4().hex[:8]}.log"
        self.write_failed = False
        self.worker_bridges = []

    def __enter__(self):
        global _session
        if _session is not None:
            raise RuntimeError("A session journal is already active")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.file_handler = SessionFileHandler(self.path, mode="x", encoding="utf-8")
        self.file_handler.setFormatter(RedactingFormatter(
            "%(asctime)s | %(levelname)s | %(processName)s/%(threadName)s | %(name)s | %(message)s"
        ))
        # Redact again on the receiving side: worker processes do not receive
        # credentials, while the parent knows the values to mask.
        self.file_handler.addFilter(RedactingFilter())
        self.queue = self.listener = None
        try:
            self.queue = queue.Queue()
            self.listener = QueueListener(self.queue, self.file_handler)
            self.listener.start()
            self.capture = OutputCapture(self.queue)
        except BaseException:
            if self.listener is not None:
                self.listener.stop()
            self.file_handler.close()
            raise
        self.stdout = self.capture.stdout
        _session = self
        for name, value in os.environ.items():
            if is_sensitive_env_name(name):
                register_secret(value)
        logging.getLogger("session.lifecycle").info("Session started: %s", self.path.name)
        return self

    def __exit__(self, exc_type, exc, traceback):
        global _session
        logger = logging.getLogger("session.lifecycle")
        if exc_type and not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            logger.error("Unhandled application error", exc_info=(exc_type, exc, traceback))
        for bridge in self.worker_bridges:
            bridge.close()
        self.capture.out.flush()
        self.capture.err.flush()
        logger.info("Session finished: %s", exc_type.__name__ if exc_type else "normal")
        self.capture.close()
        # App/Whisper cleanup completes before this sentinel; drain pending logs.
        self.listener.stop()
        self.file_handler.close()
        _session = None
        with _secrets_lock:
            _known_secrets.clear()


def session_queue():
    if _session is None:
        return None
    bridge = WorkerLogBridge(_session.queue)
    _session.worker_bridges.append(bridge)
    return bridge.sender


def configure_worker_logging(log_queue):
    return OutputCapture(log_queue) if log_queue is not None else None


def configure_logging(settings) -> None:
    logging.getLogger().setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    # load_settings may have loaded .env after the session was created.
    for name, value in os.environ.items():
        if is_sensitive_env_name(name):
            register_secret(value)
