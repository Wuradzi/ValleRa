import asyncio
import io
import logging
import multiprocessing
import sys
import tempfile
import threading
import unittest
import warnings
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.console import console_getpass, console_input, console_print
from core.logging_setup import (
    SessionLogging, configure_logging, configure_worker_logging, register_secret, session_queue,
)
from core.performance import PerformanceRecorder


def spawned_log_worker(log_queue):
    capture = configure_worker_logging(log_queue)
    try:
        print("[STT] child ready", flush=True)
        logging.getLogger("worker.test").warning("worker diagnostic")
        sys.stderr.write("child stderr\n")
        print("child partial", end="")
    finally:
        capture.close()


def noisy_worker(log_queue, ready):
    configure_worker_logging(log_queue)
    ready.set()
    while True:
        print("worker-write-fixture " + "x" * 64000, flush=True)


def fixture_whisper_worker(connection, settings):
    from services.audio.whisper_process import _worker

    def prepare():
        print("[STT] fixture Whisper ready")
        return True, "fixture ready"

    recognizer = Mock(last_duration_seconds=0, prepare=Mock(side_effect=prepare),
                      status=Mock(return_value=(True, "fixture ready")))
    with patch("services.audio.whisper_process.WhisperRecognizer", return_value=recognizer):
        _worker(connection, settings)


class SessionLoggingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name) / "sessions"

    def test_diagnostics_hidden_but_dialogue_and_prompts_visible(self):
        terminal, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(terminal), redirect_stderr(errors), SessionLogging(self.directory) as session:
            print("[LLM] connected")
            PerformanceRecorder().record("unit.stage", 12)
            logging.getLogger("test.session").warning("background warning")
            sys.stderr.write("stderr diagnostic\n")
            console_print("ValleRa: Привіт!")
            with patch("builtins.input", return_value="Команда: допомога"):
                self.assertEqual(console_input("> "), "Команда: допомога")
        journal = session.path.read_text(encoding="utf-8")
        self.assertEqual(terminal.getvalue(), "ValleRa: Привіт!\n> ")
        self.assertEqual(errors.getvalue(), "")
        for marker in ("[LLM] connected", "[PERF] unit.stage", "background warning",
                       "stderr diagnostic", "ValleRa: Привіт!", "[TEXT] Команда: допомога",
                       "Session started", "Session finished"):
            self.assertIn(marker, journal)

    def test_separate_sessions_preserve_previous_files_and_restore_output(self):
        self.directory.mkdir()
        previous = self.directory / "old.log"
        previous.write_text("old journal", encoding="utf-8")
        stdout, stderr = sys.stdout, sys.stderr
        handlers = logging.getLogger().handlers[:]
        with SessionLogging(self.directory) as first:
            print("first session")
        self.assertIs(sys.stdout, stdout)
        self.assertIs(sys.stderr, stderr)
        self.assertEqual(logging.getLogger().handlers, handlers)
        self.assertIsNone(session_queue())
        with SessionLogging(self.directory) as second:
            print("second session")
        self.assertNotEqual(first.path, second.path)
        self.assertNotIn("second session", first.path.read_text(encoding="utf-8"))
        self.assertNotIn("first session", second.path.read_text(encoding="utf-8"))
        self.assertEqual(previous.read_text(encoding="utf-8"), "old journal")

    def test_partial_lines_and_threads_are_flushed(self):
        def write_parts(number):
            sys.stdout.write(f"thread {number}: ")
            sys.stdout.write("done\n")

        with SessionLogging(self.directory) as session:
            threads = [threading.Thread(target=write_parts, args=(i,)) for i in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)
            print("last unfinished line", end="")
        journal = session.path.read_text(encoding="utf-8")
        for i in range(8):
            self.assertIn(f"thread {i}: done", journal)
        self.assertIn("last unfinished line", journal)

    def test_unhandled_exception_is_recorded_and_propagated(self):
        with self.assertRaisesRegex(ValueError, "test failure"):
            with SessionLogging(self.directory) as session:
                raise ValueError("test failure")
        journal = session.path.read_text(encoding="utf-8")
        self.assertIn("Traceback", journal)
        self.assertIn("ValueError: test failure", journal)
        self.assertIn("Session finished: ValueError", journal)

    def test_ctrl_c_flushes_journal(self):
        with self.assertRaises(KeyboardInterrupt):
            with SessionLogging(self.directory) as session:
                print("before interrupt")
                raise KeyboardInterrupt
        journal = session.path.read_text(encoding="utf-8")
        self.assertIn("before interrupt", journal)
        self.assertIn("Session finished: KeyboardInterrupt", journal)
        self.assertNotIn("Traceback", journal)

    def test_warnings_and_uncaught_thread_errors_are_file_only(self):
        def fail():
            raise RuntimeError("thread failure fixture")

        terminal = io.StringIO()
        hook = threading.excepthook
        with redirect_stderr(terminal), SessionLogging(self.directory) as session:
            with warnings.catch_warnings():
                warnings.simplefilter("always")
                warnings.warn("warning fixture", RuntimeWarning)
            thread = threading.Thread(target=fail, name="failing-test")
            thread.start()
            thread.join(timeout=2)
        journal = session.path.read_text(encoding="utf-8")
        self.assertEqual(terminal.getvalue(), "")
        self.assertIn("warning fixture", journal)
        self.assertIn("thread failure fixture", journal)
        self.assertIs(threading.excepthook, hook)

    def test_credentials_are_masked_in_text_and_tracebacks(self):
        terminal = io.StringIO()
        with redirect_stdout(terminal), SessionLogging(self.directory) as session:
            register_secret("private-value-ABC")
            print("unexpected private-value-ABC")
            print("пароль від пошти це мій-тестовий-пароль")
            print("GEMINI_API_KEY=fake-key-123")
            console_print("ValleRa: private-value-ABC")
            try:
                raise ValueError("private-value-ABC; token=token-fixture; Bearer bearer-fixture")
            except ValueError:
                logging.getLogger("test.session").exception("Request failed")
        journal = session.path.read_text(encoding="utf-8")
        self.assertIn("private-value-ABC", terminal.getvalue())
        for value in ("private-value-ABC", "мій-тестовий-пароль", "fake-key-123",
                      "token-fixture", "bearer-fixture"):
            self.assertNotIn(value, journal)
        self.assertIn("REDACTED", journal)

    def test_hidden_input_is_not_logged_and_uses_original_stream(self):
        terminal = io.StringIO()
        with redirect_stderr(terminal), SessionLogging(self.directory) as session:
            with patch("core.console.getpass.getpass", return_value="hidden-fixture") as ask:
                self.assertEqual(console_getpass("Майстер-пароль: "), "hidden-fixture")
            self.assertIs(ask.call_args.kwargs["stream"], terminal)
            print("accidental echo hidden-fixture")
        self.assertNotIn("hidden-fixture", session.path.read_text(encoding="utf-8"))

    def test_config_loaded_environment_values_are_masked(self):
        with SessionLogging(self.directory) as session:
            with patch.dict("os.environ", {"GEMINI_API_KEY": "env-fixture-value"}):
                configure_logging(SimpleNamespace(log_level="WARNING"))
            print("value env-fixture-value")
            print("[PERF] survives WARNING level")
        journal = session.path.read_text(encoding="utf-8")
        self.assertNotIn("env-fixture-value", journal)
        self.assertIn("survives WARNING level", journal)

    def test_spawned_worker_records_share_parent_session(self):
        with SessionLogging(self.directory) as session:
            context = multiprocessing.get_context("spawn")
            sender = session_queue()
            process = context.Process(target=spawned_log_worker, args=(sender,),
                                      name="test-whisper-logs")
            process.start()
            sender.close()
            try:
                process.join(timeout=10)
                self.assertEqual(process.exitcode, 0)
            finally:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=2)
                process.close()
        journal = session.path.read_text(encoding="utf-8")
        for marker in ("test-whisper-logs", "[STT] child ready", "worker diagnostic",
                       "child stderr", "child partial"):
            self.assertIn(marker, journal)
        self.assertEqual(len(list(self.directory.glob("*.log"))), 1)

    def test_write_failure_reports_once_without_logging_recursion(self):
        terminal = io.StringIO()
        with redirect_stdout(terminal), SessionLogging(self.directory) as session:
            original = session.file_handler.stream
            session.file_handler.stream = Mock(write=Mock(side_effect=OSError("disk full")))
            record = logging.LogRecord("fixture", logging.INFO, __file__, 1, "fixture", (), None)
            session.file_handler.handle(record)
            session.file_handler.handle(record)
            session.file_handler.stream = original
        self.assertEqual(terminal.getvalue().count("Не вдалося записати журнал"), 1)

    def test_cli_help_remains_visible(self):
        from main import parse_args

        terminal = io.StringIO()
        with redirect_stdout(terminal), SessionLogging(self.directory) as session:
            with patch("sys.argv", ["main.py", "--help"]), self.assertRaises(SystemExit) as exited:
                parse_args()
        self.assertEqual(exited.exception.code, 0)
        self.assertIn("--text-only", terminal.getvalue())
        self.assertIn("--text-only", session.path.read_text(encoding="utf-8"))

    def test_forced_worker_exit_does_not_block_parent_journal(self):
        with SessionLogging(self.directory) as session:
            context = multiprocessing.get_context("spawn")
            ready = context.Event()
            sender = session_queue()
            process = context.Process(target=noisy_worker, args=(sender, ready))
            process.start()
            sender.close()
            try:
                self.assertTrue(ready.wait(timeout=5))
            finally:
                process.terminate()
                process.join(timeout=2)
                self.assertFalse(process.is_alive())
                process.close()
            print("parent still records after worker termination")
        journal = session.path.read_text(encoding="utf-8")
        self.assertIn("parent still records after worker termination", journal)
        self.assertIn("Session finished", journal)
        self.assertTrue(all(not bridge.thread.is_alive() for bridge in session.worker_bridges))

    def test_whisper_process_routes_output_through_actual_worker_entry(self):
        from config import ProjectPaths, Settings
        from services.audio.whisper_process import ProcessWhisperRecognizer

        settings = Settings(ProjectPaths.from_root(self.directory.parent))
        with SessionLogging(self.directory) as session:
            worker = ProcessWhisperRecognizer(settings, worker_target=fixture_whisper_worker)
            try:
                self.assertEqual(worker.prepare(), (True, "fixture ready"))
            finally:
                worker.close()
        journal = session.path.read_text(encoding="utf-8")
        self.assertIn("[STT] fixture Whisper ready", journal)
        self.assertIn("valera-whisper", journal)

    def test_creation_failure_leaves_console_and_handlers_intact(self):
        stdout, stderr = sys.stdout, sys.stderr
        handlers = logging.getLogger().handlers[:]
        with patch("core.logging_setup.SessionFileHandler", side_effect=PermissionError):
            with self.assertRaises(PermissionError), SessionLogging(self.directory):
                self.fail("session should not start")
        self.assertIs(sys.stdout, stdout)
        self.assertIs(sys.stderr, stderr)
        self.assertEqual(logging.getLogger().handlers, handlers)
        self.assertIsNone(session_queue())


class MainSessionTests(unittest.TestCase):
    def run_main(self, effect):
        import main

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        journal = SessionLogging(Path(temp.name) / "sessions")
        terminal = io.StringIO()
        with (patch.object(main, "SessionLogging", return_value=journal),
              patch.object(main, "async_main", side_effect=effect),
              redirect_stdout(terminal), redirect_stderr(io.StringIO())):
            result = main.main()
        return result, terminal.getvalue(), journal.path.read_text(encoding="utf-8")

    def test_app_cleanup_precedes_end_marker(self):
        async def app():
            print("startup fixture")
            try:
                return 0
            finally:
                await asyncio.to_thread(print, "cleanup fixture")

        result, terminal, journal = self.run_main(app)
        self.assertEqual(result, 0)
        self.assertNotIn("startup fixture", terminal)
        self.assertLess(journal.index("cleanup fixture"), journal.index("Session finished"))

    def test_failed_startup_keeps_traceback_out_of_terminal(self):
        async def app():
            raise ValueError("startup failure fixture")

        result, terminal, journal = self.run_main(app)
        self.assertEqual(result, 1)
        self.assertNotIn("Traceback", terminal)
        self.assertIn("зупинено через помилку", terminal)
        self.assertIn("startup failure fixture", journal)

    def test_ctrl_c_keeps_clean_exit_and_journal(self):
        async def app():
            raise KeyboardInterrupt

        result, terminal, journal = self.run_main(app)
        self.assertEqual(result, 0)
        self.assertIn("ValleRa завершено", terminal)
        self.assertIn("Shutdown requested: Ctrl+C", journal)
