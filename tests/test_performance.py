import asyncio
import io
import json
import queue
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock, patch

from config import ProjectPaths, Settings
from core.app import ValleRaApp
from core.metrics import MetricsCollector
from core.performance import PerformanceRecorder
from core.speak import Speaker
from services.audio.whisper import WhisperRecognizer
from services.audio.whisper_process import ProcessWhisperRecognizer, _worker
from testing.probes.performance import summarize


class RecorderTests(unittest.TestCase):
    def test_span_uses_monotonic_clock_and_records_only_timing_fields(self):
        metrics = Mock()
        with patch("core.performance.time.perf_counter", side_effect=[10, 11, 11.25, 11.3]):
            recorder = PerformanceRecorder(metrics)
            with redirect_stdout(io.StringIO()), recorder.span("unit.stage"):
                pass
        event, = metrics.record.call_args.args
        fields = metrics.record.call_args.kwargs
        self.assertEqual(event, "performance")
        self.assertEqual(fields["duration_ms"], 250)
        self.assertEqual(fields["elapsed_ms"], 1300)
        self.assertEqual(set(fields), {"run_id", "stage", "duration_ms", "elapsed_ms", "status"})

    def test_failed_span_does_not_swallow_or_record_exception_body(self):
        metrics = Mock()
        output = io.StringIO()
        recorder = PerformanceRecorder(metrics)
        with redirect_stdout(output), self.assertRaises(ValueError):
            with recorder.span("unit.error"):
                raise ValueError("PRIVATE_TEST_CONTENT")
        self.assertEqual(metrics.record.call_args.kwargs["status"], "error")
        self.assertNotIn("PRIVATE_TEST_CONTENT", output.getvalue() + str(metrics.record.call_args))

    def test_write_failure_does_not_break_work(self):
        recorder = PerformanceRecorder(Mock(record=Mock(side_effect=OSError("PRIVATE_TEST_CONTENT"))))
        with redirect_stdout(io.StringIO()), self.assertLogs("core.performance") as logs:
            recorder.record("unit.stage", 1)
        self.assertNotIn("PRIVATE_TEST_CONTENT", str(logs.output))

    def test_disabled_recorder_has_no_output_or_writes(self):
        metrics, output = Mock(), io.StringIO()
        recorder = PerformanceRecorder(metrics, enabled=False)
        with redirect_stdout(output), recorder.span("unit.stage"):
            pass
        self.assertEqual(output.getvalue(), "")
        metrics.record.assert_not_called()

    def test_summary_selects_latest_run_and_ignores_non_timing_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "timings.jsonl"
            metrics = MetricsCollector(path)
            metrics.record("performance", run_id="old", stage="old", duration_ms=99, status="ok")
            metrics.record("performance", run_id="new", stage="stage", duration_ms=2, status="ok")
            metrics.record("performance", run_id="new", stage="stage", duration_ms=4, status="error")
            metrics.record("unrelated", private="PRIVATE_TEST_CONTENT")
            # A crash may leave one partial JSON line at EOF.
            with path.open("a", encoding="utf-8") as target:
                target.write('{"event":')
            run_id, rows = summarize(path)
        self.assertEqual(run_id, "new")
        self.assertEqual(rows, [{"stage": "stage", "count": 2, "average_ms": 3, "max_ms": 4, "not_ok": 1}])


class AsyncRecorderTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_measurement_propagates_cancellation(self):
        metrics = Mock()
        recorder = PerformanceRecorder(metrics)

        async def cancelled():
            raise asyncio.CancelledError()

        with redirect_stdout(io.StringIO()), self.assertRaises(asyncio.CancelledError):
            await recorder.measure("unit.cancel", cancelled())
        self.assertEqual(metrics.record.call_args.kwargs["status"], "cancelled")

    async def test_stt_stages_record_vosk_then_unavailable_whisper(self):
        metrics = Mock()
        app = object.__new__(ValleRaApp)
        app.performance = PerformanceRecorder(metrics)
        app.settings = SimpleNamespace(stt_whisper_preload=True)
        app.listener = SimpleNamespace(
            _get_model=Mock(),
            whisper=SimpleNamespace(enabled=True, prepare=Mock(return_value=(False, "unavailable"))),
        )
        with redirect_stdout(io.StringIO()):
            await app._prepare_stt()
        rows = [call.kwargs for call in metrics.record.call_args_list]
        self.assertEqual([row["stage"] for row in rows], ["startup.vosk_ready", "startup.whisper_ready"])
        self.assertEqual(rows[-1]["status"], "unavailable")


class BackendTimingTests(unittest.TestCase):
    def settings(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Settings(ProjectPaths.from_root(Path(temporary.name)))

    def test_whisper_resolves_cache_separately_and_does_not_reload(self):
        settings = ProcessWhisperRecognizer(self.settings()).settings
        settings.benchmark_local_files_only = True
        recognizer = WhisperRecognizer(settings)
        model_path = settings.paths.models_dir / "cached-model"
        model_path.mkdir(parents=True)
        for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
            (model_path / name).write_bytes(b"test-fixture")
        download = Mock(return_value=str(model_path))
        constructor = Mock()
        with (
            patch.object(WhisperRecognizer, "package_available", new_callable=PropertyMock, return_value=True),
            patch.dict("sys.modules", {"faster_whisper": SimpleNamespace(WhisperModel=constructor),
                                       "faster_whisper.utils": SimpleNamespace(download_model=download)}),
            redirect_stdout(io.StringIO()),
        ):
            first = recognizer._get_model()
            self.assertIs(recognizer._get_model(), first)
        download.assert_called_once_with(settings.stt_whisper_model,
                                         cache_dir=str(settings.paths.models_dir / "faster-whisper"), local_files_only=True)
        constructor.assert_called_once()
        self.assertEqual(constructor.call_args.args, (str(model_path),))
        self.assertEqual(set(recognizer.last_timings), {"whisper.import", "whisper.resolve_files", "whisper.load_weights"})

    def test_worker_transmits_timings_without_transcript_in_timing_payload(self):
        recognizer = Mock(last_duration_seconds=0)
        recognizer.prepare.side_effect = lambda: recognizer.last_timings.update({"whisper.import": 12}) or (True, "ready")
        recognizer.status.return_value = (True, "ready")
        connection = Mock(recv=Mock(side_effect=[("prepare", ()), ("close", ())]))
        with patch("services.audio.whisper_process.signal.signal"), patch(
            "services.audio.whisper_process.WhisperRecognizer", return_value=recognizer
        ):
            _worker(connection, self.settings())
        payload = connection.send.call_args.args[0]
        self.assertEqual(payload[3]["whisper.import"], 12)
        self.assertIn("whisper.worker_prepare", payload[3])
        connection.close.assert_called_once()

    def test_parent_records_worker_timings(self):
        worker = ProcessWhisperRecognizer(self.settings())
        worker._process = Mock()
        worker._connection = Mock(recv=Mock(return_value=(
            (True, "ready"), (True, "ready"), 0, {"whisper.load_weights": 42},
        )))
        metrics = Mock()
        worker.performance = PerformanceRecorder(metrics)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(worker._request("prepare"), (True, "ready"))
        rows = {call.kwargs["stage"]: call.kwargs for call in metrics.record.call_args_list}
        self.assertEqual(rows["whisper.load_weights"]["duration_ms"], 42)
        self.assertIn("whisper.roundtrip_prepare", rows)

    def test_tts_speak_event_is_not_a_completion_acknowledgement(self):
        speaker = Speaker(self.settings())
        speaker._tts_process = Mock(stdin=io.StringIO(), poll=Mock(return_value=None))
        speaker._tts_responses = queue.Queue()
        speaker._tts_responses.put({"event": "speak_call"})
        speaker._tts_responses.put({"ok": True})
        speaker._current_queued_at = time.perf_counter()
        metrics = Mock()
        speaker.performance = PerformanceRecorder(metrics)
        with redirect_stdout(io.StringIO()):
            speaker._generate_with_windows_speech("PRIVATE_TEST_CONTENT", None)
        self.assertTrue(speaker._tts_responses.empty())
        self.assertEqual([call.kwargs["stage"] for call in metrics.record.call_args_list],
                         ["tts.request_to_speak_call", "tts.queue_to_speak_call"])
        self.assertNotIn("PRIVATE_TEST_CONTENT", str(metrics.record.call_args_list))
        self.assertEqual(json.loads(speaker._tts_process.stdin.getvalue())["text"], "PRIVATE_TEST_CONTENT")
