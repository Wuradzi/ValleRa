from __future__ import annotations

import io
import unittest
import wave
import tempfile
import threading
from pathlib import Path
from unittest.mock import Mock
from types import SimpleNamespace

import numpy as np

from core.listen import VoskListener
from core.models import RecognitionResult
from services.audio.whisper import WhisperRecognizer
from services.audio.refinement import RefinementWait
from config import Settings, ProjectPaths


class HybridRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.listener = object.__new__(VoskListener)
        self.listener.settings = SimpleNamespace(
            stt_whisper_min_confidence=0.45,
            stt_whisper_skip_silence=True,
            noise_threshold=0.005,
        )

    def test_whisper_replaces_unrestricted_vosk_text(self):
        result = self.listener._select_result(
            RecognitionResult("валера команда відкриє голограм", 0.81, "vosk"),
            RecognitionResult(
                "Валера, команда, відкрий Telegram", 0.88, "whisper"
            ),
        )
        self.assertEqual(result.text, "Валера, команда, відкрий Telegram")
        self.assertEqual(result.engine, "whisper")

    def test_low_confidence_whisper_keeps_vosk(self):
        vosk = RecognitionResult("валера котра година", 0.76, "vosk")
        result = self.listener._select_result(
            vosk,
            RecognitionResult("незрозумілий шум", 0.2, "whisper"),
        )
        self.assertIs(result, vosk)

    def test_prefix_conflict_is_not_overridden_by_confidence_fallback(self):
        result = self.listener._select_result(
            RecognitionResult("команда відкрий браузер", 1, "vosk"),
            RecognitionResult("відкрий браузер", .2, "whisper"),
        )
        self.assertEqual(result.engine, "conflict")
        self.assertFalse(self.listener._contains_command_prefix(result.text))

    def test_whisper_text_is_not_modified_with_vosk_words(self):
        result = self.listener._select_result(
            RecognitionResult("валера відкритого грам", 0.78, "vosk"),
            RecognitionResult("відкрий Telegram", 0.9, "whisper"),
        )
        self.assertEqual(result.text, "відкрий Telegram")
        self.assertEqual(result.engine, "whisper")

    def test_whisper_preserves_natural_address_by_name(self):
        result = self.listener._select_result(
            RecognitionResult("моя команда", 0.7, "vosk"),
            RecognitionResult("Валера, моя команда", 0.86, "whisper"),
        )
        self.assertEqual(result.text, "Валера, моя команда")
        self.assertEqual(result.engine, "whisper")

    def test_command_prefix_disagreement_keeps_non_command_transcript(self):
        result = self.listener._select_result(
            RecognitionResult(
                "команда відкрий браузер",
                0.82,
                "vosk",
            ),
            RecognitionResult(
                "відкрий браузер",
                0.91,
                "whisper",
            ),
        )
        self.assertEqual(result.text, "відкрий браузер")
        self.assertEqual(result.engine, "conflict")
        self.assertEqual(result.confidence, 0.82)

    def test_zero_filled_windows_endpoint_is_rejected(self):
        self.assertFalse(VoskListener._signal_present(b"\x00\x00" * 400))
        self.assertTrue(
            VoskListener._signal_present(b"\x00\x00\x10\x00" * 200)
        )

    def test_silence_does_not_invoke_whisper_refinement(self):
        refine, reason = self.listener._should_refine(
            RecognitionResult("", 0.0, "vosk"),
            b"\x00\x00" * 16_000,
            16_000,
        )
        self.assertFalse(refine)
        self.assertEqual(reason, "silence")

    def test_short_name_is_refined_like_any_other_phrase(self):
        samples = np.full(16_000, 3000, dtype=np.int16).tobytes()
        refine, reason = self.listener._should_refine(
            RecognitionResult("валера", 0.9, "vosk"),
            samples,
            16_000,
        )
        self.assertTrue(refine)
        self.assertEqual(reason, "")

    def test_spoken_request_still_uses_whisper(self):
        samples = np.full(16_000, 3000, dtype=np.int16).tobytes()
        refine, reason = self.listener._should_refine(
            RecognitionResult("валера команда відкрий браузер", 0.9, "vosk"),
            samples,
            16_000,
        )
        self.assertTrue(refine)
        self.assertEqual(reason, "")

    def test_fast_profile_only_skips_high_confidence_harmless_phrases(self):
        self.listener.settings.performance_profile = "fast"
        samples = np.full(16_000, 3000, dtype=np.int16).tobytes()
        self.assertEqual(self.listener._should_refine(RecognitionResult("привіт", .99), samples, 16000), (False, "fast-chat"))
        for text, confidence in [("привіт", .8), ("команда погода в Луцьку", 1), ("розкажи про погоду", 1)]:
            self.assertTrue(self.listener._should_refine(RecognitionResult(text, confidence), samples, 16000)[0])


class SelectiveRefinementTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.listener = VoskListener(Settings(
            ProjectPaths.from_root(Path(self.directory.name)), stt_selective_whisper_enabled=True,
            stt_endpoint_adaptive=True, stt_whisper_soft_budget_ms=500, stt_whisper_hard_budget_ms=1000))
        self.pcm = b'\x00\x10' * 16000
        self.listener._input_candidates = Mock(return_value=[(0, 16000)])
        self.listener.whisper = Mock(enabled=True)
        self.listener.whisper.transcribe.return_value = RecognitionResult('Зрозуміла відповідь', .9, 'whisper')

    def capture(self, text, confidence=.96, **flags):
        self.listener._listen_on_device = Mock(return_value=(RecognitionResult(text, confidence, **flags), self.pcm))

    def test_confident_chat_fast_path_and_low_confidence_refinement(self):
        self.capture('Сьогодні був цікавий день')
        self.assertEqual(self.listener.listen_once().engine, 'vosk')
        self.listener.whisper.transcribe.assert_not_called()
        self.capture('Сьогодні був цікавий день', .65)
        self.assertEqual(self.listener.listen_once().engine, 'whisper')
        self.listener.whisper.transcribe.assert_called_once()

    def test_incomplete_suspicious_and_actions_refine_regardless_of_length(self):
        for text, flags in [('я хотів', {}), ('це це це', {}), ('відкрий Telegram', {}),
                            ('Завершена фраза', {'capture_truncated': True}),
                            ('Завершена фраза', {'fragmented': True})]:
            with self.subTest(text=text, flags=flags):
                self.assertTrue(self.listener._should_refine(RecognitionResult(text, .99, **flags), self.pcm, 16000)[0])

    def test_confirmation_and_scoped_reply_do_not_require_whisper(self):
        for text in ('так', 'ні', 'гаразд'):
            self.capture(text)
            self.assertEqual(self.listener.listen_once(grammar=['так', 'ні', 'гаразд']).engine, 'vosk')
        self.listener.scoped_reply = lambda text: text == 'Telegram'
        self.capture('Telegram')
        self.assertEqual(self.listener._should_refine(RecognitionResult('Telegram', .96), self.pcm, 16000),
                         (False, 'scoped_context'))
        self.assertEqual(self.listener.listen_once().engine, 'vosk')
        self.listener.whisper.transcribe.assert_not_called()

    def test_scoped_hint_uses_existing_pending_context_and_catalogue(self):
        import time
        from core.processor import CommandProcessor
        processor = object.__new__(CommandProcessor)
        processor.services = {'apps': SimpleNamespace(has_exact_name=lambda name: name == 'Telegram')}
        pending = {'tool': 'open_app', 'expires': time.monotonic() + 60}
        processor._natural_pending = pending
        self.assertTrue(processor.stt_scoped_reply('Telegram'))
        self.assertFalse(processor.stt_scoped_reply('невідома програма'))
        self.assertFalse(processor.stt_scoped_reply('відкрий Telegram'))
        self.assertIs(processor._natural_pending, pending)
        pending['expires'] = 0
        self.assertFalse(processor.stt_scoped_reply('Telegram'))

    def test_empty_or_unreliable_refinement_never_authorizes_action(self):
        for refined in (RecognitionResult('', 0, 'whisper'), RecognitionResult('відкрий Telegram', .2, 'whisper')):
            self.capture('відкрий Telegram', .7)
            self.listener.whisper.transcribe.return_value = refined
            result = self.listener.listen_once()
            self.assertTrue(result.fragmented)
            self.assertTrue(result.incomplete)
            self.assertTrue(result.recognition_unreliable)
            self.assertFalse(result.utterance_incomplete)  # Complete words, but repeat required for safety.

    def test_timeout_chat_fallback_and_action_repeat(self):
        self.listener.settings.stt_whisper_soft_budget_ms = 10
        self.listener.settings.stt_whisper_hard_budget_ms = 40
        for text, incomplete in [('День був цікавий', False), ('відкрий Telegram', True)]:
            release = threading.Event()
            completed = threading.Event()

            def transcribe(*args):
                release.wait(2)
                completed.set()
                return RecognitionResult('late result', .99, 'whisper')

            self.listener.whisper.transcribe.side_effect = transcribe
            self.capture(text, .7)
            try:
                result = self.listener.listen_once()
                self.assertEqual(result.text, text)
                self.assertEqual(result.engine, 'vosk')
                self.assertTrue(result.fragmented)  # Existing processor tool-free guard.
                self.assertEqual(result.incomplete, incomplete)
                self.assertTrue(result.recognition_unreliable)
                self.assertFalse(result.utterance_incomplete)
            finally:
                release.set()
                self.assertTrue(completed.wait(1))
                self.assertTrue(self.listener._refinement_wait._active.wait(1))
            self.assertEqual(result.text, text)  # Late completion cannot mutate the turn.

    def test_busy_cancelled_and_late_results_do_not_start_parallel_jobs(self):
        waiter = RefinementWait()
        release = threading.Event()
        recognizer = Mock()
        recognizer.transcribe.side_effect = lambda *args: (release.wait(2), RecognitionResult('late', .9, 'whisper'))[1]
        try:
            self.assertEqual(waiter.run(recognizer, b'', 16000, 5, 20, lambda: False)[1], 'timeout')
            self.assertEqual(waiter.run(recognizer, b'', 16000, 5, 20, lambda: False)[1], 'busy')
            recognizer.transcribe.assert_called_once()
        finally:
            release.set()
            self.assertTrue(waiter._active.wait(1))
        release.clear()
        try:
            self.assertEqual(waiter.run(recognizer, b'', 16000, 5, 20, lambda: True)[1], 'cancelled')
        finally:
            release.set()
            self.assertTrue(waiter._active.wait(1))

    def test_interruption_ignores_pending_refinement(self):
        def transcribe(*args):
            self.listener.interrupt()
            return RecognitionResult('відкрий Telegram', .99, 'whisper')
        self.listener.whisper.transcribe.side_effect = transcribe
        self.capture('відкрий Telegram', .7)
        result = self.listener.listen_once()
        self.assertEqual(result.engine, 'interrupted')
        self.assertEqual(result.text, '')


class WhisperAudioTests(unittest.TestCase):
    def test_pcm_is_wrapped_as_valid_mono_wav(self):
        pcm = b"\x00\x00\x01\x00" * 80
        buffer = WhisperRecognizer._wav_buffer(pcm, 48_000)
        self.assertIsInstance(buffer, io.BytesIO)
        with wave.open(buffer, "rb") as audio:
            self.assertEqual(audio.getnchannels(), 1)
            self.assertEqual(audio.getsampwidth(), 2)
            self.assertEqual(audio.getframerate(), 48_000)
            self.assertEqual(audio.readframes(audio.getnframes()), pcm)
