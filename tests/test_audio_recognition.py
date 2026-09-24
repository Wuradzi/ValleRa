from __future__ import annotations

import io
import unittest
import wave
from types import SimpleNamespace

import numpy as np

from core.listen import VoskListener
from core.models import RecognitionResult
from services.audio.whisper import WhisperRecognizer


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

