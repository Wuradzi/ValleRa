"""Offline Groq contract tests: never sends audio or loads an ASR model."""
import io
import os
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

import httpx

from config import Settings, ProjectPaths, merge_config, validate_config, ConfigError
from services.audio.backends import create_backend
from services.audio.capabilities import Capabilities
from services.audio.profiles import resolve_profile
from services.audio.groq_backend import GroqBackend, ENDPOINT


class GroqSTTTests(unittest.TestCase):
    def test_polite_scope_does_not_accept_negation_or_narrative(self):
        from core.natural_turn import request_scope
        self.assertEqual(request_scope('А ти можеш мені відкрити програму?'), 'open_app')
        for text in ('А ти можеш мені не відкривати програму?',
                     'Він сказав: а ти можеш мені відкрити програму?',
                     'Якщо ти можеш мені відкрити програму'):
            self.assertIsNone(request_scope(text))

    def test_mixed_ukrainian_latin_names_keep_unknown_trust(self):
        for text in ('Знайди редактор Blender', 'Покажи програму Photoshop', 'Відкрий програму Blender'):
            self.response({'text': text})
            result = self.backend.transcribe(bytes(3200), 16000)
            self.assertEqual(result.text, text)
            self.assertFalse(result.confidence_available)
            self.assertTrue(result.fragmented)

    def test_primary_prepare_warms_confirmation_even_if_primary_fails(self):
        from core.stt_listener import SpeechListener
        listener = SpeechListener.__new__(SpeechListener)
        listener.vosk_backend = Mock()
        listener.vosk_backend.prepare.return_value = (True, 'ready')
        listener.primary = Mock()
        listener.primary.prepare.return_value = (False, 'missing_key')
        listener.profile = SimpleNamespace(name='test', backend='faster-whisper')
        self.assertEqual(listener.prepare(), (False, 'missing_key'))
        listener.vosk_backend.prepare.assert_called_once_with()

    def test_cloud_prepare_never_loads_vosk(self):
        from core.stt_listener import SpeechListener
        listener = SpeechListener.__new__(SpeechListener)
        listener.profile = SimpleNamespace(name='test', backend='groq')
        listener.vosk_backend = Mock()
        listener.primary = Mock()
        listener.primary.prepare.return_value = (True, 'ready')
        self.assertEqual(listener.prepare(), (True, 'ready'))
        listener.vosk_backend.prepare.assert_not_called()

    def test_cloud_confirmation_uses_primary_capture_not_vosk(self):
        from core.stt_listener import SpeechListener
        from core.listen import VoskListener
        from core.confirmation import ConfirmationService
        import contextlib
        listener = SpeechListener(self.settings)
        self.addCleanup(listener.close)
        listener._input_candidates = Mock(return_value=[(0, 16000)])
        capture = SimpleNamespace(pcm=b'\0\0'*1600, interrupted=False)
        listener._recognize_capture = Mock(return_value='cloud-result')
        with patch('core.stt_listener.capture_pcm', return_value=capture) as capture_fn, patch.object(
                VoskListener, 'listen_once', side_effect=AssertionError('Vosk forbidden')), patch(
                'core.stt_listener.TurnTiming', return_value=None):
            listener.performance = SimpleNamespace(span=lambda _: contextlib.nullcontext(), enabled=False)
            self.assertEqual(listener.listen_once(8, ConfirmationService.GRAMMAR), 'cloud-result')
            self.assertIsNone(capture_fn.call_args.kwargs['utterance_timeout'])
            self.assertEqual(capture_fn.call_args.args[3], 8)
            listener._recognize_capture.assert_called_once_with(capture, None)
            self.assertEqual(listener.listen_once(15), 'cloud-result')
            self.assertEqual(capture_fn.call_args.kwargs['utterance_timeout'], 45)
            self.assertEqual(capture_fn.call_args.args[3], 15)

    def setUp(self):
        self.settings = Settings(ProjectPaths.from_root(Path.cwd()), stt_backend='groq')
        self.key_patch = patch.dict(os.environ, {'GROQ_API_KEY': 'private-test-key'})
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)
        self.profile = resolve_profile(self.settings, Capabilities('arm64', 'Linux', ram_mb=905))[0]
        self.backend = GroqBackend(self.settings, self.profile)
        self.addCleanup(self.backend.close)
        self.client_patch = patch('services.audio.groq_backend.httpx.Client')
        self.client_factory = self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.client = self.client_factory.return_value.__enter__.return_value

    def response(self, body=None, status=200):
        self.client.post.return_value = httpx.Response(status, json=body if body is not None else {'text': 'Привіт, Рейвен.'})

    def test_success_wav_language_model_unknown_trust(self):
        self.response()
        result = self.backend.transcribe(bytes(88200), 44100)
        self.assertEqual(result.text, 'Привіт, Рейвен.')
        self.assertEqual((result.engine, result.confidence), ('groq', 0))
        self.assertFalse(result.recognition_unreliable)
        self.assertFalse(result.confidence_available)
        self.assertTrue(result.fragmented)
        self.assertFalse(result.incomplete)
        self.assertEqual(self.client.post.call_args.args, (ENDPOINT,))
        kwargs = self.client.post.call_args.kwargs
        self.assertEqual(kwargs['data'], dict(model='whisper-large-v3-turbo', language='uk', temperature='0', response_format='json'))
        with wave.open(io.BytesIO(kwargs['files']['file'][1])) as wav:
            self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getnframes()), (44100, 1, 2, 44100))
        self.assertTrue(self.backend.last_metrics['success'])
        self.assertEqual(self.backend.last_metrics['audio_seconds'], 1)
        self.client_factory.assert_called_once_with(timeout=15, follow_redirects=False)

    def test_non_ukrainian_transcript_is_discarded_without_authority(self):
        for text in ('Vitgræ Brásar', 'Vigrei Browser', 'Ne відкрifa í browser.', 'Открой новый браузер'):
            self.response({'text': text})
            result = self.backend.transcribe(bytes(3200), 16000)
            self.assertEqual(result.text, '')
            self.assertTrue(result.recognition_unreliable)
            self.assertEqual(self.backend.last_error, 'non_ukrainian_transcript')

    def test_ukrainian_requests_and_product_names_survive_guard(self):
        for text in ('Запусти Telegram', 'Відкрий Obsidian', 'Знайди документацію FastAPI',
                     'Відкрий VeryLongUnknownProductName', 'Покажи файл report_final.docx',
                     'Raven, запусти браузер', 'Відміна', 'ні'):
            self.response({'text': text})
            self.assertEqual(self.backend.transcribe(bytes(3200), 16000).text, text)

    def test_latin_names_need_current_exact_scope(self):
        for text in ('Telegram', 'VS Code', 'Obsidian', 'UnlistedProduct'):
            self.response({'text': text})
            self.assertEqual(self.backend.transcribe(bytes(3200), 16000).text, '')
            allowed = Mock(return_value=True)
            result = self.backend.transcribe(bytes(3200), 16000, name_reply_allowed=allowed)
            self.assertEqual(result.text, text)
            self.assertFalse(result.confidence_available)
            self.assertTrue(result.fragmented)
            allowed.assert_called_once_with(text)
            result = self.backend.transcribe(bytes(3200), 16000, name_reply_allowed=Mock(return_value=False))
            self.assertEqual(result.text, '')

    def test_scoped_latin_name_context_is_request_local(self):
        import time
        from core.stt_listener import SpeechListener
        from services.audio.pcm_capture import CapturedAudio
        listener = SpeechListener(self.settings)
        self.addCleanup(listener.close)
        listener.primary = self.backend
        dialogue = listener.recognition_policy.dialogue
        dialogue.services = {'apps': SimpleNamespace(has_exact_name=Mock(return_value=True))}
        capture = CapturedAudio(bytes(3200), 16000, False, False, 10, 9)
        for state in ('active', 'expired', 'replaced', 'unknown'):
            self.response({'text': 'Obsidian'})
            dialogue.pending = {'tool': 'open_app', 'expires': time.monotonic() + 60}
            dialogue.services['apps'].has_exact_name.return_value = state != 'unknown'
            if state == 'expired':
                dialogue.pending['expires'] = time.monotonic() - 1
            response = self.client.post.return_value
            def post(*args, **kwargs):
                if state == 'replaced':
                    dialogue.pending = {'tool': 'open_app', 'expires': time.monotonic() + 60}
                return response
            self.client.post.side_effect = post
            result = listener._recognize_capture(capture)
            self.assertEqual(result.text, 'Obsidian' if state == 'active' else '')
            self.assertFalse(result.confidence_available)
            self.assertTrue(result.fragmented)
        self.client.post.side_effect = None

    def test_prepare_missing_key_never_calls_network(self):
        with patch.dict(os.environ, {'GROQ_API_KEY': ''}):
            self.backend = GroqBackend(self.settings, self.profile)
        self.assertEqual(self.backend.prepare(), (False, 'MISSING_KEY'))
        self.assertEqual(self.backend.transcribe(bytes(3200), 16000).text, '')
        self.assertEqual(self.backend.last_error, 'missing_key')
        self.client.post.assert_not_called()

    def test_prepare_success_does_not_spend_quota(self):
        self.assertTrue(self.backend.prepare()[0])
        self.client_factory.assert_not_called()

    def test_key_survives_core_environment_scrub(self):
        self.response()
        with patch.dict(os.environ, {'GROQ_API_KEY': ''}):
            self.assertTrue(self.backend.prepare()[0])
            self.assertTrue(self.backend.transcribe(bytes(3200), 16000).text)

    def test_http_failures_are_safe_without_retry(self):
        for status, error in [(401, 'authorization'), (403, 'authorization'), (429, 'rate_limit'), (500, 'server'), (503, 'server'), (302, 'http_error')]:
            with self.subTest(status=status):
                self.client.post.reset_mock()
                self.response({'message': 'private-test-key'}, status)
                with self.assertLogs('services.audio.groq_backend', level='INFO') as logs:
                    result = self.backend.transcribe(bytes(3200), 16000)
                self.assertEqual(result.text, '')
                self.assertEqual(self.backend.last_error, error)
                self.assertEqual(self.backend.last_metrics['http_status'], status)
                self.assertNotIn('private-test-key', str(logs.output))
                self.client.post.assert_called_once()

    def test_transport_failures_no_secret_logging(self):
        for exception, reason in [(httpx.ReadTimeout('private-test-key'), 'timeout'), (httpx.ConnectError('private-test-key'), 'network')]:
            with self.subTest(reason=reason):
                self.client.post.side_effect = exception
                with self.assertLogs('services.audio.groq_backend', level='INFO') as logs:
                    result = self.backend.transcribe(bytes(3200), 16000)
                self.assertFalse(result.text)
                self.assertEqual(self.backend.last_error, reason)
                self.assertNotIn('private-test-key', str(logs.output))

    def test_empty_malformed_and_non_json(self):
        for body, reason in [({}, 'malformed_response'), ([], 'malformed_response'), ({'text': 7}, 'malformed_response'), ({'text': '  '}, 'empty_transcription')]:
            with self.subTest(body=body):
                self.response(body)
                self.assertFalse(self.backend.transcribe(bytes(3200), 16000).text)
                self.assertEqual(self.backend.last_error, reason)
        self.client.post.return_value = httpx.Response(200, text='not JSON')
        self.assertFalse(self.backend.transcribe(bytes(3200), 16000).text)
        self.assertEqual(self.backend.last_error, 'malformed_response')

    def test_invalid_audio_not_uploaded(self):
        for pcm, rate in [(b'', 16000), (b'x', 16000), (b'xx', 0), (b'xx', 100)]:
            self.assertFalse(self.backend.transcribe(pcm, rate).text)
        self.client.post.assert_not_called()

    def test_close_during_request_discards_late_result(self):
        entered, release = threading.Event(), threading.Event()
        def request(*args, **kwargs):
            entered.set()
            release.wait(2)
            return httpx.Response(200, json={'text': 'відкрий браузер'})
        self.client.post.side_effect = request
        results = []
        worker = threading.Thread(target=lambda: results.append(self.backend.transcribe(bytes(3200), 16000)))
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.backend.close()
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0].text, '')
        self.assertEqual(self.backend.last_error, 'cancelled')

    def test_config_defaults_validation_and_platform_resolution(self):
        self.assertEqual(merge_config({})['stt']['backend'], 'auto')
        for os_name, arch in [('Windows', 'x86_64'), ('Linux', 'arm64')]:
            profile, _ = resolve_profile(self.settings, Capabilities(arch, os_name, ram_mb=905))
            self.assertEqual((profile.backend, profile.device, profile.compute_type), ('groq', 'cloud', 'remote'))
            backend = create_backend(self.settings, profile)
            self.assertIsInstance(backend, GroqBackend)
            backend.close()
        validate_config(merge_config({'stt': {'backend': 'groq'}}))
        for changes in [{'groq_model': 'invalid'}, {'groq_timeout_seconds': 0}, {'groq_timeout_seconds': True}]:
            with self.assertRaises(ConfigError):
                validate_config(merge_config({'stt': changes}))
        self.settings.stt_backend = 'auto'
        profile, _ = resolve_profile(self.settings, Capabilities('x86_64', 'Windows', ram_mb=4096))
        self.assertEqual(profile.backend, 'faster-whisper')

    def test_listener_unknown_result_remains_restricted(self):
        from core.stt_listener import SpeechListener
        from services.audio.pcm_capture import CapturedAudio
        listener = SpeechListener(self.settings)
        try:
            self.response({'text': 'відкрий браузер'})
            result = listener._recognize_capture(CapturedAudio(bytes(3200), 16000, False, False, 10., 9.))
            self.assertFalse(result.recognition_unreliable)
            self.assertFalse(result.confidence_available)
            self.assertTrue(result.fragmented)
            self.assertEqual(result.confidence, 0)
        finally:
            listener.close()

    def test_cloud_failure_never_falls_back_to_vosk(self):
        from core.stt_listener import SpeechListener
        from services.audio.pcm_capture import CapturedAudio
        self.settings.stt_fallback_backend = 'vosk'
        listener = SpeechListener(self.settings)
        self.addCleanup(listener.close)
        self.response(status=503)
        with patch.object(listener.vosk_backend, 'transcribe', side_effect=AssertionError('no local fallback')):
            result = listener._recognize_capture(CapturedAudio(bytes(3200), 16000, False, False, 10., 9.))
        self.assertTrue(result.recognition_unreliable)
        self.assertFalse(result.text)

    def test_benchmark_cloud_requires_explicit_permission(self):
        from testing.probes.stt_benchmark import _variant
        pipe = Mock()
        _variant(pipe, Path('.'), {'backend': 'groq', 'model': self.profile.model}, None)
        self.assertEqual(pipe.send.call_args.args[0]['reason'], 'cloud_not_authorized')
        self.client.post.assert_not_called()

    def test_benchmark_mocked_cloud_metrics_no_hidden_warmup(self):
        from testing.probes.stt_benchmark import _variant
        self.response()
        pipe = Mock()
        samples = [({'id': 'sample', 'reference': 'Привіт, Рейвен.'}, bytes(3200), 16000, .1)]
        with patch('config.load_settings', return_value=self.settings), \
             patch('testing.probes.stt_benchmark.read_samples', return_value=(samples, 'a'*64, True)):
            _variant(pipe, Path('.'), {'backend': 'groq', 'model': self.profile.model}, None, allow_cloud=True)
        report = pipe.send.call_args.args[0]
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(report['summary']['wer_percent'], 0)
        self.assertEqual(report['summary']['cer_percent'], 0)
        self.assertEqual(report['resource_scope'], 'client_only')
        self.assertEqual(report['metadata']['backend'], 'groq')
        self.client.post.assert_called_once()

    def test_doctor_remote_needs_no_model_files(self):
        from services.health.checks import models
        inventory = models(self.settings)
        self.assertEqual([m['role'] for m in inventory], ['confirmation'])
        self.client_factory.assert_not_called()

    def test_benchmark_failure_preserves_diagnostics_and_stops(self):
        from testing.probes.stt_benchmark import _variant
        self.response({'error': 'private-test-key'}, 429)
        pipe = Mock()
        samples = [({'id': str(i), 'reference': 'Привіт'}, bytes(3200), 16000, .1) for i in range(2)]
        with patch('config.load_settings', return_value=self.settings), \
             patch('testing.probes.stt_benchmark.read_samples', return_value=(samples, 'a'*64, True)):
            _variant(pipe, Path('.'), {'backend': 'groq', 'model': self.profile.model}, None, allow_cloud=True)
        report = pipe.send.call_args.args[0]
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['failures'], 1)
        self.assertEqual(report['error_class'], 'rate_limit')
        self.assertIn('resources', report)
        self.assertEqual(report['resource_scope'], 'client_only')
        self.assertNotIn('private-test-key', str(report))
        self.client.post.assert_called_once()

    def test_backend_needs_no_local_inference_imports(self):
        import builtins
        original = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.split('.')[0] in {'faster_whisper', 'ctranslate2', 'sherpa_onnx', 'torch', 'groq'}:
                raise AssertionError('Unnecessary optional runtime import')
            return original(name, *args, **kwargs)
        for system, arch in [('Windows', 'x86_64'), ('Linux', 'arm64')]:
            with patch('builtins.__import__', side_effect=guarded):
                profile, _ = resolve_profile(self.settings, Capabilities(arch, system))
                backend = create_backend(self.settings, profile)
                self.assertTrue(backend.prepare()[0])
                backend.close()

    def test_matrix_opt_in(self):
        from testing.probes.stt_benchmark import read_matrix
        matrix = read_matrix(SimpleNamespace(matrix=Path('testing/stt_groq_matrix.json'), candidates=None))
        self.assertEqual(len(matrix), 2)
        self.assertTrue(all(c['backend'] == 'groq' for c in matrix))
