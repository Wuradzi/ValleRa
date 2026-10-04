"""Deterministic performance/safety contracts, no CUDA, microphone or downloads."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from contextlib import nullcontext
from unittest.mock import Mock, patch

import numpy as np

from config import Settings, ProjectPaths, merge_config, validate_config, ConfigError
from core.models import RecognitionResult
from services.audio.backends import primary_settings
from services.audio.cuda_diagnostics import device_retry_possible
from services.audio.pcm_capture import AcousticEndpoint, capture_pcm
from services.audio.whisper import WhisperRecognizer
from services.audio.backends import BackendMetadata
from testing.probes.stt_benchmark import _variant


class STTPerformancePassTests(unittest.TestCase):
    def recognizer(self):
        settings = primary_settings(Settings(ProjectPaths.from_root(Path.cwd())))
        settings.stt_whisper_model = 'large-v3-turbo'
        settings.stt_whisper_escalation_model = 'large-v3'
        return WhisperRecognizer(settings)

    def test_good_primary_never_escalates(self):
        stt = self.recognizer()
        stt._transcribe_once = Mock(return_value=RecognitionResult('Це завершена думка.', .97))
        result = stt.transcribe(b'12', 16000)
        self.assertEqual(result.text, 'Це завершена думка.')
        stt._transcribe_once.assert_called_once()
        self.assertFalse(stt.last_metadata['escalated'])

    def test_uncertain_or_empty_escalates_exactly_once(self):
        for text, confidence in [('відкрий браузер', .3), ('', 0), ('я хотів', .99)]:
            with self.subTest(text=text):
                stt = self.recognizer()
                stt._transcribe_once = Mock(side_effect=[RecognitionResult(text, confidence),
                    RecognitionResult('Завершена репліка.', .96)])
                result = stt.transcribe(b'12', 16000)
                self.assertEqual(stt._transcribe_once.call_count, 2)
                self.assertEqual(stt.active_model, 'large-v3')
                self.assertFalse(result.recognition_unreliable)

    def test_uncertain_escalation_stays_fail_closed(self):
        stt = self.recognizer()
        stt._transcribe_once = Mock(return_value=RecognitionResult('відкрий браузер', .6))
        result = stt.transcribe(b'12', 16000)
        self.assertTrue(result.recognition_unreliable)
        self.assertTrue(result.fragmented)

    def test_runtime_error_does_not_start_second_model(self):
        stt = self.recognizer()
        def fail(*args):
            stt.last_error = 'invalid input'
            return RecognitionResult('', 0)
        stt._transcribe_once = Mock(side_effect=fail)
        stt.transcribe(b'12', 16000)
        stt._transcribe_once.assert_called_once()

    def test_device_retry_classifier(self):
        for message in ('CUDA failed with error out of memory', 'cublas64_12.dll not found',
                        'Could not load libcudnn.so.9', 'CUDA driver version is insufficient'):
            self.assertTrue(device_retry_possible(RuntimeError(message)))
        for error in (ValueError('CUDA config'), TypeError('bad API'), RuntimeError('decode failed'),
                      RuntimeError('model corrupt'), RuntimeError('out of memory'), RuntimeError('unknown bug')):
            self.assertFalse(device_retry_possible(error))

    def test_non_cuda_failure_no_cpu_retry(self):
        stt = self.recognizer()
        stt.actual_device = 'cuda'
        model = Mock()
        model.transcribe.side_effect = RuntimeError('invalid audio')
        stt._get_model = Mock(return_value=model)
        with patch.object(WhisperRecognizer, 'package_available', True):
            result = stt.transcribe(b'12', 16000)
        self.assertEqual(result.text, '')
        self.assertFalse(stt._forced_cpu)
        stt._get_model.assert_called_once()

    def test_same_model_device_fallback_preserves_quality(self):
        stt = self.recognizer()
        stt.actual_device = 'cuda'
        gpu, cpu = Mock(), Mock()
        gpu.transcribe.side_effect = RuntimeError('cuDNN library missing')
        segment = SimpleNamespace(text='Привіт', words=[SimpleNamespace(probability=.99)], no_speech_prob=0)
        cpu.transcribe.return_value = ([segment], SimpleNamespace(language_probability=1))
        stt._get_model = Mock(side_effect=[gpu, cpu])
        with patch.object(WhisperRecognizer, 'package_available', True):
            result = stt.transcribe(b'12', 16000)
        self.assertEqual(result.text, 'Привіт')
        self.assertFalse(result.recognition_unreliable)
        self.assertFalse(result.fragmented)
        self.assertEqual(stt.active_model, 'large-v3-turbo')
        self.assertEqual(stt.last_metadata['fallback'], 'same_model_cpu')

    def test_one_model_slot_and_next_turn_restores_primary(self):
        stt = self.recognizer()
        stt._model = Mock()
        stt._select_model('large-v3')
        self.assertIsNone(stt._model)
        stt._transcribe_once = Mock(return_value=RecognitionResult('Готово.', .99))
        stt.transcribe(b'12', 16000)
        self.assertEqual(stt.active_model, 'large-v3-turbo')

    def test_short_acoustic_turn_and_long_internal_pause(self):
        rate = 16000
        speech = np.full(rate // 2, 3000, dtype=np.int16).tobytes()
        endpoint = AcousticEndpoint(rate, .01, short_ms=700)
        endpoint.feed(speech)
        endpoint.feed(bytes(int(rate * .68) * 2))
        self.assertFalse(endpoint.ready)
        endpoint.feed(bytes(int(rate * .02) * 2))
        self.assertTrue(endpoint.ready)
        endpoint = AcousticEndpoint(rate, .01, short_ms=700)
        endpoint.feed(speech * 8)
        endpoint.feed(bytes(rate * 2))
        self.assertFalse(endpoint.ready)
        endpoint.feed(speech)
        endpoint.feed(bytes(rate * 4))
        self.assertTrue(endpoint.ready)

    def test_silence_never_becomes_speech(self):
        endpoint = AcousticEndpoint(16000, .01, short_ms=700)
        endpoint.feed(bytes(16000 * 30))
        self.assertFalse(endpoint.speech)
        self.assertFalse(endpoint.ready)

    def test_capture_separates_endpoint_and_return_timestamps(self):
        settings = Settings(ProjectPaths.from_root(Path.cwd()))
        speech = np.full(8000, 3000, dtype=np.int16).tobytes()
        def stream(**kwargs):
            kwargs['callback'](speech, 8000, None, None)
            kwargs['callback'](bytes(22400), 11200, None, None)
            self.assertEqual(kwargs['blocksize'], 1600)
            return nullcontext()
        sd = SimpleNamespace(RawInputStream=stream)
        with patch('services.audio.pcm_capture.time.perf_counter', side_effect=[100, 100.5, 101.2, 101.21, 101.22]):
            result = capture_pcm(sd, None, 16000, 8, settings, lambda: False, nullcontext)
        self.assertAlmostEqual(result.speech_end_at, 100.5)
        self.assertAlmostEqual(result.endpoint_at - result.speech_end_at, .71)
        self.assertAlmostEqual(result.capture_returned_at - result.capture_started_at, 1.22)
        self.assertEqual(result.endpoint_reason, 'silence')
        self.assertFalse(result.truncated)

    def test_config_bounds_and_escalation_pair(self):
        config = merge_config({'stt': {'profiles': {'quality': {
            'model': 'large-v3-turbo', 'escalation_model': 'large-v3', 'escalation_confidence': .8}}}})
        validate_config(config)
        config['stt']['profiles']['quality']['model'] = 'base'
        with self.assertRaises(ConfigError):
            validate_config(config)

    def test_benchmark_runtime_failure_is_failed_not_not_tested(self):
        settings = Settings(ProjectPaths.from_root(Path.cwd()))
        backend = Mock(metadata=BackendMetadata('faster-whisper', 'small'))
        backend.prepare.return_value = True, 'ready'
        backend.recognizer.actual_device = 'cpu'
        backend.recognizer.actual_compute_type = 'int8'
        backend.recognizer.last_error = 'invalid decoder state'
        connection = Mock()
        with patch('config.load_settings', return_value=settings), \
                patch('services.audio.backends.create_backend', return_value=backend), \
                patch('testing.probes.stt_benchmark.read_samples',
                      return_value=([({'id': 'one'}, b'12', 16000, 1)], 'digest', False)):
            _variant(connection, '.', {'id': 'small', 'backend': 'faster-whisper', 'model': 'small'}, 'cpu')
        self.assertEqual(connection.send.call_args.args[0]['status'], 'failed')
        backend.transcribe.assert_called_once()

    def test_empty_capture_bypasses_every_model(self):
        from core.stt_listener import SpeechListener
        from services.audio.pcm_capture import CapturedAudio
        listener = SpeechListener(Settings(ProjectPaths.from_root(Path.cwd())))
        self.addCleanup(listener.close)
        listener._refinement_wait.run = Mock(side_effect=AssertionError('silence decoded'))
        result = listener._recognize_capture(CapturedAudio(b'', 16000, False, False, 1, None))
        self.assertEqual(result.text, '')

    def test_inference_timer_excludes_model_load_and_records_warm_state(self):
        stt = self.recognizer()
        stt.settings.stt_whisper_escalation_model = ''
        model = Mock()
        model.transcribe.return_value = ([], SimpleNamespace(language_probability=1))
        stt._get_model = Mock(return_value=model)
        with patch.object(WhisperRecognizer, 'package_available', True), \
                patch('services.audio.whisper.time.perf_counter', side_effect=[10, 10.25]):
            stt.transcribe(b'12', 16000)
        self.assertEqual(stt.last_timings['whisper.inference'], 250)
        self.assertEqual(stt.last_metadata['cold_or_warm'], 'cold')
        stt._model = model
        stt.transcribe(b'12', 16000)
        self.assertEqual(stt.last_metadata['cold_or_warm'], 'warm')
