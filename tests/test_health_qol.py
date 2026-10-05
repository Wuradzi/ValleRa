import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave
import zipfile

import numpy as np

from config import Settings, ProjectPaths, merge_config, migrate_config, validate_config
from core.models import RecognitionResult
from services.health.checks import check, doctor, exit_code
from services.health.bundle import collect, sanitize
from services.health.probes import tts_self_test, audio_probe
from services.health.cli import smoke
from services.health.runtime import latency_hud, runtime_status
from testing.release_check import run


class HealthQoLTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = Settings(ProjectPaths.from_root(self.root))

    def test_migration_preserves_unknown_fields_and_is_idempotent(self):
        old = {'custom': {'note': 'do not discard'}, 'stt': {'quality_profile': 'balanced'}}
        migrated = migrate_config(old)
        self.assertEqual(migrated, migrate_config(migrated))
        self.assertEqual(old['custom'], migrated['custom'])
        self.assertNotIn('config_version', old)
        validate_config(merge_config(old))
        self.assertEqual(merge_config(old)['config_version'], 2)

    def test_doctor_offline_warn_no_failure(self):
        with patch('services.health.checks.models', return_value=[]), \
             patch('services.health.probes.audio_inventory', return_value=[check('audio', 'WARN')]), \
             patch('services.health.probes.tts_self_test', return_value=check('TTS', 'PASS')), \
             patch('services.health.checks.find_spec', return_value=True):
            rows, report = doctor(self.root)
        self.assertEqual(exit_code(rows), 0)
        self.assertTrue(any(r['status'] == 'WARN' for r in rows))
        self.assertIn('system', report)
        self.assertFalse((self.root / 'config.json').exists())
        self.assertFalse((self.root / 'data').exists())

    def test_doctor_invalid_config_fails_without_echoing_values(self):
        (self.root / 'config.json').write_text('{private-secret', encoding='utf-8')
        rows, _ = doctor(self.root)
        self.assertEqual(exit_code(rows), 1)
        self.assertNotIn('private-secret', str(rows))

    def test_optional_escalation_missing_warns(self):
        model = dict(active=True, role='escalation', profile='quality', present=False)
        with patch('services.health.checks.models', return_value=[model]), \
             patch('services.health.probes.audio_inventory', return_value=[]), \
             patch('services.health.probes.tts_self_test', return_value=check('TTS', 'PASS')), \
             patch('services.health.checks.find_spec', return_value=True):
            rows, _ = doctor(self.root)
        self.assertEqual(exit_code(rows), 0)
        self.assertEqual(next(r for r in rows if r['name'].startswith('Model'))['status'], 'WARN')

    @staticmethod
    def wav(path, hint):
        with wave.open(str(path), 'wb') as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16000)
            out.writeframes(b'\x01\x01' * 100)
        return dict(status='ok', voice='Fixture', culture='uk-UA')

    def test_tts_nonempty_wav_and_cleanup(self):
        paths = []
        def synth(path, hint):
            paths.append(path)
            return self.wav(path, hint)
        result = tts_self_test(self.settings, synth)
        self.assertEqual(result['status'], 'PASS')
        self.assertGreater(result['data']['wav_bytes'], 0)
        self.assertFalse(paths[0].exists())

    def test_tts_empty_missing_and_voice_missing(self):
        def empty(path, hint):
            path.touch()
            return {'status': 'ok'}
        self.assertEqual(tts_self_test(self.settings, empty)['detail'], 'wav_empty')
        self.assertEqual(tts_self_test(self.settings, lambda *a: {'status': 'ok'})['detail'], 'wav_not_created')
        self.assertEqual(tts_self_test(self.settings, lambda *a: {'status': 'voice_missing'})['detail'], 'voice_missing')

    def test_tts_fallback_is_warn_not_requested_voice_success(self):
        def fallback(path, hint):
            return {**self.wav(path, hint), 'fallback': True}
        result = tts_self_test(self.settings, fallback)
        self.assertEqual(result['status'], 'WARN')
        self.assertEqual(result['data']['voice_resolved'], 'Fixture')

    def test_audio_uses_only_local_memory_and_preserves_config(self):
        sd = Mock()
        sd.query_devices.return_value = {'name': 'fixture', 'default_samplerate': 16000}
        sd.rec.return_value = np.full((16000, 1), .05)
        threshold = self.settings.noise_threshold
        result = audio_probe(self.settings, seconds=1, sd=sd)[0]
        self.assertEqual(result['status'], 'PASS')
        self.assertTrue(result['data']['speech_detected'])
        self.assertEqual(self.settings.noise_threshold, threshold)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_bundle_excludes_env_memory_audio_clipboard_and_nested_secrets(self):
        secret = 'PRIVATE-unique-value'
        (self.root / 'config.json').write_text(json.dumps({'api_key': secret, 'custom': {'password': secret, secret: secret}}))
        for name in ('.env', 'clipboard.txt', 'memory.enc', 'recording.wav'):
            (self.root / name).write_text(secret)
        folder = collect(self.root, health={'system': {'secret': secret}, 'stt_status': {}, 'tts_status': {}})
        with zipfile.ZipFile(folder / 'diagnostics.zip') as archive:
            self.assertEqual(len(archive.namelist()), 9)
            for name in archive.namelist():
                self.assertNotIn(secret, archive.read(name).decode())
                self.assertNotIn(name, ('.env', 'clipboard.txt', 'memory.enc', 'recording.wav'))
        self.assertEqual((self.root / '.env').read_text(), secret)

    def test_recursive_sanitization(self):
        result = sanitize({'api_key': 'one', 'token': 'two', 'password': 'three',
                           'custom': [{'authorization': 'four', 'innocent': 'five'}]})
        for secret in ('one', 'two', 'three', 'four', 'five'):
            self.assertNotIn(secret, json.dumps(result))

    def test_hud_missing_is_na(self):
        self.assertIn('tts_submit=n/a', latency_hud({'endpoint': 400}))
        self.assertIn('endpoint=400ms', latency_hud({'endpoint': 400}))

    def test_status_does_not_dump_objects_or_history(self):
        app = SimpleNamespace(settings=self.settings, listener=None, processor=None,
            llm=SimpleNamespace(active_name='gemini', history='PRIVATE'),
            confirmation=SimpleNamespace(awaiting=False, pending_secret='PRIVATE'), performance=None)
        result = runtime_status(app)
        self.assertNotIn('PRIVATE', result)
        self.assertIn('confirmation=False', result)

    def test_release_required_failure_and_optional_not_run(self):
        args = SimpleNamespace(with_benchmark=False, with_gpu=False, with_live=False, corpus=None)
        self.assertEqual(run(args, self.root, runner=lambda *a: 0), 0)
        self.assertEqual(run(args, self.root, runner=lambda *a: 1), 1)
        reports = [json.loads(p.read_text()) for p in self.root.glob('logs/release-check/*/report.json')]
        self.assertTrue(all(r['stages'][-1]['status'] == 'NOT_RUN' for r in reports))

    def test_release_no_cuda_is_not_tested(self):
        args = SimpleNamespace(with_benchmark=False, with_gpu=True, with_live=False, corpus=None)
        with patch('services.audio.capabilities.cuda_available', return_value=False):
            self.assertEqual(run(args, self.root, runner=lambda *a: 0), 0)
        report = json.loads(next(self.root.glob('logs/release-check/*/report.json')).read_text())
        self.assertEqual(report['stages'][-2]['status'], 'NOT_TESTED')

    def test_smoke_fake_pipeline_never_executes_actions(self):
        listener = Mock()
        listener.prepare.return_value = True, 'ready'
        listener.listen_once.side_effect = [RecognitionResult('Привіт', .9), RecognitionResult('Відкрий калькулятор', .9),
                                           RecognitionResult('так', 1), RecognitionResult('ні', 1)]
        rows = smoke(self.settings, listener_factory=lambda s: listener, prompt=lambda s: None,
                     audio=lambda s: [check('Microphone', 'PASS')], tts=lambda s: check('TTS', 'PASS'))
        self.assertEqual(exit_code(rows), 0)
        listener.close.assert_called_once()
        self.assertEqual(listener.listen_once.call_count, 4)
        self.assertTrue(listener.listen_once.call_args_list[-1].args[1])
