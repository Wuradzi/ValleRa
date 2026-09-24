import asyncio
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from config import ProjectPaths, Settings
from core.listen import VoskListener
from core.metrics import MetricsCollector
from core.models import RecognitionResult
from core.performance import CURRENT_TURN, PerformanceRecorder, TurnTiming, mark_turn
from core.speak import Speaker
from testing.probes.performance import summarize_turns


class TurnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(ProjectPaths.from_root(Path(self.temp.name)))

    def test_per_turn_breakdown_deduplicates_and_leaves_missing_values_unknown(self):
        path = Path(self.temp.name) / "metrics.jsonl"
        perf = PerformanceRecorder(MetricsCollector(path))
        with contextlib.redirect_stdout(io.StringIO()):
            turn = TurnTiming(perf, endpoint_at=10, speech_end_at=9)
            for name, at in [("recognition_ready", 12), ("dispatch", 12.1), ("llm_first_text", 13),
                             ("first_phrase", 13.5), ("tts_start", 13.6), ("tts_submit", 13.7),
                             ("tts_submit", 15)]:
                turn.mark(name, at=at)
            missing = TurnTiming(perf)
            missing.mark("dispatch")
            perf.metrics.record("private_dialogue", text="PRIVATE_TEST_CONTENT")
        _, rows = summarize_turns(path)
        self.assertEqual(rows[0]["total_estimate_ms"], 4700)
        self.assertEqual(rows[0]["recognition_ms"], 2000)
        self.assertEqual(rows[0]["buffer_ms"], 500)
        self.assertEqual(rows[0]["tts_queue_ms"], 100)
        self.assertIsNone(rows[1]["total_estimate_ms"])
        self.assertIsNone(rows[1]["recognition_ms"])
        self.assertNotIn("PRIVATE_TEST_CONTENT", str(rows))

    def test_endpoint_estimate_uses_last_vosk_word_without_changing_audio(self):
        listener = VoskListener(self.settings)
        listener._get_model = Mock()
        listener.performance = PerformanceRecorder(Mock())
        pcm = b"\x00\x10" * 4000
        recognizer = Mock()
        recognizer.Result.return_value = json.dumps({"text": "привіт", "result": [{"word": "привіт", "conf": 1, "end": 0.1}]})

        def stream(**kwargs):
            kwargs["callback"](pcm, 4000, None, None)
            return contextlib.nullcontext()

        with (patch("vosk.KaldiRecognizer", return_value=recognizer),
              patch("core.listen.suppress_native_stderr", contextlib.nullcontext),
              patch("core.performance.time.perf_counter", return_value=10.5),
              contextlib.redirect_stdout(io.StringIO())):
            result, captured = listener._listen_on_device(SimpleNamespace(RawInputStream=stream), 0, 16000, 8, None)
        self.assertEqual(captured, pcm)
        self.assertEqual(result.text, "привіт")
        self.assertAlmostEqual(result.timing.speech_end_at, 10.35)
        self.assertEqual(result.timing.endpoint_at, 10.5)

    def test_whisper_replacement_preserves_parent_turn(self):
        listener = VoskListener(self.settings)
        listener.performance = PerformanceRecorder(enabled=False)
        timing = TurnTiming(listener.performance, endpoint_at=10)
        listener._input_candidates = Mock(return_value=[(0, 16000)])
        listener._listen_on_device = Mock(return_value=(RecognitionResult("привіт", .8, timing=timing), b"pcm"))
        listener._should_refine = Mock(return_value=(True, "test"))
        listener.whisper = SimpleNamespace(enabled=True, transcribe=Mock(return_value=RecognitionResult("Привіт!", .9, "whisper")))
        result = listener.listen_once()
        self.assertIs(result.timing, timing)
        self.assertEqual(result.engine, "whisper")

    def test_file_synthesis_does_not_claim_playback(self):
        import queue
        speaker = Speaker(self.settings)
        speaker._tts_process = Mock(stdin=io.StringIO(), poll=Mock(return_value=None))
        speaker._tts_responses = queue.Queue()
        speaker._tts_responses.put({"event": "speak_call"})
        speaker._tts_responses.put({"ok": True})
        speaker._current_timing = Mock()
        speaker._generate_with_windows_speech("synthetic", Path(self.temp.name) / "fixture.wav")
        speaker._current_timing.mark.assert_called_once_with("tts_file_synthesis")


class AsyncTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_tts_worker_carries_queue_context_and_does_not_leak_to_reminders(self):
        with tempfile.TemporaryDirectory() as directory:
            speaker = Speaker(Settings(ProjectPaths.from_root(Path(directory))))
            turn = Mock()
            seen = []
            speaker._speak_sync = lambda _: seen.append(speaker._current_timing)
            await speaker.start()  # Worker was created outside the turn context.
            token = CURRENT_TURN.set(turn)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    await speaker.say("Перша фраза.")
            finally:
                CURRENT_TURN.reset(token)
            with contextlib.redirect_stdout(io.StringIO()):
                await speaker.say("Окреме нагадування.")
            await speaker.wait_until_idle()
            self.assertEqual(seen, [turn, None])
            self.assertIsNone(speaker._current_timing)
            await speaker._queue.put(None)
            await speaker._worker_task

    async def test_parallel_contexts_and_to_thread_are_isolated(self):
        first, second = Mock(), Mock()

        async def run(turn):
            token = CURRENT_TURN.set(turn)
            try:
                await asyncio.to_thread(mark_turn, "llm_first_text")
            finally:
                CURRENT_TURN.reset(token)

        await asyncio.gather(run(first), run(second))
        first.mark.assert_called_once_with("llm_first_text")
        second.mark.assert_called_once_with("llm_first_text")
        self.assertIsNone(CURRENT_TURN.get())
