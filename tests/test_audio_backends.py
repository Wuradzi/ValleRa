"""Offline backend/PCM boundary regressions; no downloads, GPU or microphone."""
from pathlib import Path
from types import SimpleNamespace
import unittest
import threading
from unittest.mock import Mock, patch

import numpy as np

from config import Settings, ProjectPaths
from core.confirmation import ConfirmationService
from core.models import RecognitionResult
from core.stt_listener import SpeechListener
from services.audio.backends import primary_settings, FasterWhisperBackend
from services.audio.pcm_capture import AcousticEndpoint, CapturedAudio
from services.audio.whisper import WhisperRecognizer
from testing.probes.stt_benchmark import scores, aggregate


class AudioBackendTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(ProjectPaths.from_root(Path.cwd()))

    def listener(self):
        listener = SpeechListener(self.settings)
        self.addCleanup(listener.close)
        return listener

    def capture(self, truncated=False):
        return CapturedAudio(b'\x00\x10' * 16000, 16000, truncated, False, 10., 9.)

    def test_profiles_and_forced_uk(self):
        self.settings.language = 'en'
        for profile, model in [('quality', 'large-v3'), ('balanced', 'large-v3-turbo'), ('low_resource', 'base')]:
            self.settings.stt_quality_profile = profile
            selected = primary_settings(self.settings)
            self.assertEqual(selected.stt_whisper_model, model)
            self.assertEqual(selected.language, 'uk')
            self.assertIn(selected.stt_whisper_device, {'cpu', 'cuda'})
            self.assertTrue(selected.stt_whisper_local_files_only)

    def test_primary_does_not_invoke_vosk(self):
        listener = self.listener()
        listener.vosk_backend.transcribe = Mock(side_effect=AssertionError('Vosk-first'))
        listener._refinement_wait.run = Mock(return_value=(RecognitionResult('Це завершена репліка.', .95, 'whisper'), 'done', 30))
        result = listener._recognize_capture(self.capture())
        self.assertEqual(result.engine, 'whisper')
        self.assertFalse(result.recognition_unreliable)
        self.assertFalse(result.capture_truncated)
        listener.vosk_backend.transcribe.assert_not_called()

    def test_unrestricted_capture_never_constructs_vosk(self):
        listener = self.listener()
        listener._input_candidates = Mock(return_value=[(None, 16000)])
        listener._get_model = Mock(side_effect=AssertionError('Vosk capture'))
        with patch('core.stt_listener.capture_pcm', return_value=self.capture()), \
             patch.object(listener, '_recognize_capture', return_value=RecognitionResult('Привіт', .9, 'whisper')):
            self.assertEqual(listener.listen_once().engine, 'whisper')
        listener._get_model.assert_not_called()

    def test_primary_timeout_drains_once_without_late_dispatch(self):
        listener = self.listener()
        listener.settings.stt_primary_timeout_ms = 20
        finished, release = threading.Event(), threading.Event()

        def transcribe(*args):
            release.wait(2)
            finished.set()
            return RecognitionResult('відкрий браузер', .99, 'whisper')

        listener.whisper.transcribe = Mock(side_effect=transcribe)
        try:
            self.assertEqual(listener._recognize_capture(self.capture()).text, '')
            self.assertEqual(listener._recognize_capture(self.capture()).text, '')
            listener.whisper.transcribe.assert_called_once()
        finally:
            release.set()
            self.assertTrue(finished.wait(2))

    def test_primary_low_confidence_is_not_action_eligible(self):
        listener = self.listener()
        listener._refinement_wait.run = Mock(return_value=(RecognitionResult('відкрий браузер', .1, 'whisper'), 'done', 1))
        result = listener._recognize_capture(self.capture())
        self.assertTrue(result.recognition_unreliable)
        self.assertTrue(result.incomplete)

    def test_confirmation_uses_unchanged_legacy_path(self):
        listener = self.listener()
        result = RecognitionResult('ні', .99, 'vosk')
        with patch('core.listen.VoskListener.listen_once', return_value=result) as legacy:
            self.assertIs(listener.listen_once(5, ConfirmationService.GRAMMAR), result)
        legacy.assert_called_once_with(5, ConfirmationService.GRAMMAR)

    def test_explicit_vosk_backend(self):
        self.settings.stt_backend = 'vosk'
        listener = self.listener()
        self.assertIsNone(listener.primary)
        self.assertFalse(listener.whisper.enabled)
        with patch('core.listen.VoskListener.listen_once') as legacy:
            listener.listen_once(4)
        legacy.assert_called_once_with(4, None)

    def test_failed_primary_fallback_never_becomes_reliable_action(self):
        self.settings.stt_fallback_backend = 'vosk'
        listener = self.listener()
        listener.vosk_backend.transcribe = Mock(return_value=RecognitionResult('відкрий браузер', .99, 'vosk'))
        for outcome in ('timeout', 'busy', 'done'):
            listener._refinement_wait.run = Mock(return_value=(RecognitionResult('', 0, 'whisper'), outcome, 60))
            result = listener._recognize_capture(self.capture())
            self.assertEqual(result.engine, 'vosk')
            self.assertTrue(result.recognition_unreliable)
            self.assertTrue(result.incomplete)
            self.assertTrue(result.fragmented)

    def test_no_implicit_fallback(self):
        listener = self.listener()
        listener.vosk_backend.transcribe = Mock()
        listener._refinement_wait.run = Mock(return_value=(RecognitionResult('', 0, 'whisper'), 'timeout', 60))
        result = listener._recognize_capture(self.capture())
        self.assertEqual(result.text, '')
        listener.vosk_backend.transcribe.assert_not_called()

    def test_cancelled_primary_never_falls_back(self):
        self.settings.stt_fallback_backend = 'vosk'
        listener = self.listener()
        listener.vosk_backend.transcribe = Mock()
        listener._refinement_wait.run = Mock(return_value=(RecognitionResult('відкрий браузер', .99, 'whisper'), 'cancelled', 1))
        self.assertEqual(listener._recognize_capture(self.capture()).text, '')
        listener.vosk_backend.transcribe.assert_not_called()

    def test_truncation_separate_from_confidence(self):
        listener = self.listener()
        listener._refinement_wait.run = Mock(return_value=(RecognitionResult('Це завершена репліка.', .99, 'whisper'), 'done', 1))
        result = listener._recognize_capture(self.capture(True))
        self.assertTrue(result.capture_truncated)
        self.assertFalse(result.recognition_unreliable)
        self.assertTrue(result.fragmented)

    def test_transcribe_forces_uk_task_and_preserves_hints(self):
        self.settings.stt_whisper_prompt = 'Українська мова.'
        self.settings.stt_whisper_hotwords = 'ValleRa'
        backend = FasterWhisperBackend(primary_settings(self.settings), process=False)
        model = Mock()
        model.transcribe.return_value = ([], SimpleNamespace(language_probability=1))
        backend.recognizer._model = model
        backend.transcribe(b'\0\0' * 1600, 16000)
        kwargs = model.transcribe.call_args.kwargs
        self.assertEqual(kwargs['language'], 'uk')
        self.assertEqual(kwargs['task'], 'transcribe')
        self.assertEqual(kwargs['hotwords'], 'ValleRa')
        self.assertEqual(kwargs['initial_prompt'], 'Українська мова.')

    def test_cuda_load_failure_uses_same_model_cpu(self):
        self.settings.stt_profile = 'quality'
        self.settings.stt_primary_device = 'cuda'
        recognizer = WhisperRecognizer(primary_settings(self.settings))
        with patch('ctranslate2.get_cuda_device_count', return_value=1), \
             patch('services.audio.whisper.resolve_model', return_value='cached-model'), \
             patch('faster_whisper.WhisperModel', side_effect=[RuntimeError('cuda'), Mock()]) as constructor:
            recognizer._get_model()
        self.assertEqual(constructor.call_count, 2)
        self.assertEqual(constructor.call_args.args, ('cached-model',))
        self.assertEqual(recognizer.actual_device, 'cpu')
        self.assertEqual(recognizer.actual_compute_type, 'int8')

    def test_cpu_auto_selection(self):
        recognizer = WhisperRecognizer(primary_settings(self.settings))
        with patch('ctranslate2.get_cuda_device_count', return_value=0), \
             patch('services.audio.whisper.resolve_model', return_value='cached-model'), \
             patch('faster_whisper.WhisperModel'):
            recognizer._get_model()
        self.assertEqual(recognizer.actual_device, 'cpu')

    def test_acoustic_silence_never_creates_turn(self):
        endpoint = AcousticEndpoint(16000, .01)
        endpoint.feed(bytes(16000 * 6))
        self.assertFalse(endpoint.ready)
        self.assertFalse(endpoint.speech)

    def test_acoustic_endpoint_ignores_internal_pause(self):
        endpoint = AcousticEndpoint(16000, .01)
        endpoint.feed(np.full(16000 * 4, 5000, dtype=np.int16).tobytes())
        endpoint.feed(bytes(16000 * 2))
        self.assertFalse(endpoint.ready)
        endpoint.feed(np.full(8000, 5000, dtype=np.int16).tobytes())
        endpoint.feed(bytes(16000 * 4))
        self.assertTrue(endpoint.ready)

    def test_benchmark_scores_and_rtf(self):
        result = scores('привіт світе', 'привіт світе')
        self.assertEqual(result['word_errors'], 0)
        self.assertEqual(result['cer_percent'], 0)
        summary = aggregate([{**result, 'latency_ms': 500, 'audio_seconds': 2}])
        self.assertEqual(summary['realtime_factor'], .25)
