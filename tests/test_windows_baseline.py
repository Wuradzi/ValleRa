"""Deterministic lifecycle/privacy checks, not audible playback validation."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import zipfile

from config import Settings, ProjectPaths
from services.health.probes import tts_self_test, audio_inventory
from services.health.bundle import collect, BUNDLE_FILES
from services.audio.windows_speech import synthesize_windows, SPEECH_FUNCTIONS
from tests import test_health_qol as health_fixtures


class WindowsBaselineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = Settings(ProjectPaths.from_root(self.root))

    def test_wav_validated_after_subprocess_finalization(self):
        def completed(*args, **kw):
            request = json.loads(kw['input'])
            path = Path(request['path'])
            path.touch()  # initially incomplete, only completed child yields valid WAV
            health_fixtures.HealthQoLTests.wav(path, '')
            return Mock(returncode=0, stdout=json.dumps(dict(status='ok', voice='Volodymyr', culture='uk-UA')), stderr='')
        with patch('services.audio.windows_speech.subprocess.run', side_effect=completed):
            row = tts_self_test(self.settings, synthesize_windows)
        self.assertEqual(row['status'], 'PASS')
        self.assertEqual(row['data']['voice_requested'], self.settings.tts_voice_hint)
        self.assertEqual(row['data']['voice_resolved'], 'Volodymyr')
        self.assertEqual(row['data']['stage'], 'wav_validation')
        self.assertTrue(row['data']['temp_wav_exists'])

    def test_empty_wav_precise_failure(self):
        def empty(path, hint):
            path.touch()
            return dict(status='ok')
        row = tts_self_test(self.settings, empty)
        self.assertEqual(row['status'], 'FAIL')
        self.assertEqual(row['data']['stage'], 'wav_validation')

    def test_enumeration_failure_preserves_technical_reason(self):
        data = dict(status='synthesis_failed', stage='voice_enumeration', error_type='NullReferenceException',
                    exception_message='Object reference not set', exit_code=0)
        row = tts_self_test(self.settings, lambda *a: data)
        self.assertEqual(row['status'], 'FAIL')
        self.assertEqual(row['data']['stage'], 'voice_enumeration')
        self.assertEqual(row['data']['exception_message'], data['exception_message'])

    def test_unsupported_platform_clean(self):
        with patch('services.platform.resolver.platform.system', return_value='Linux'):
            self.assertEqual(tts_self_test(self.settings)['status'], 'NOT_AVAILABLE')

    def test_playback_absent_does_not_fail_synthesis(self):
        with patch('sounddevice.query_devices', side_effect=RuntimeError('no device')):
            output = audio_inventory(self.settings)[1]
        self.assertEqual(output['status'], 'WARN')
        self.assertEqual(tts_self_test(self.settings, health_fixtures.HealthQoLTests.wav)['status'], 'PASS')

    def test_shared_functions_finalize_after_speak(self):
        self.assertLess(SPEECH_FUNCTIONS.index('$speaker.Speak'), SPEECH_FUNCTIONS.index('$speaker.SetOutputToNull'))
        self.assertIn("'voice_enumeration'", SPEECH_FUNCTIONS)
        self.assertIn('No enabled installed speech voices', SPEECH_FUNCTIONS)

    def test_canaries_in_nested_config_logs_and_excluded_sources(self):
        canaries = ['VALERA_TEST_API_KEY_938471', 'VALERA_TEST_PASSWORD_57291', 'VALERA_TEST_TOKEN_18473']
        nested = {'custom': {'innocent': [dict(zip(['api_key', 'password', 'token'], canaries)),
                                         {canaries[0]: canaries[1]}, canaries[2]]}}
        (self.root / 'config.json').write_text(json.dumps(nested), encoding='utf-8')
        for name in ('.env', 'data/memory.enc', 'data/secret-store.bin', 'recording.wav', 'clipboard.txt',
                     'logs/sessions/session.log', 'data/chat.json', 'notes.txt', 'models/weights.bin'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('[VOICE/whisper] ' + ' '.join(canaries) + '\nsession.dialogue ' + canaries[0])
        (self.root / 'data/metrics.jsonl').write_text(json.dumps(dict(event='performance',
            stage='llm.first_text', duration_ms=123, transcript=canaries)) + '\n' + json.dumps(nested))
        folder = collect(self.root, health={'stt_status': {'profile': {'model': canaries[0]}}, 'private': nested})
        with zipfile.ZipFile(folder / 'diagnostics.zip') as archive:
            self.assertEqual(set(archive.namelist()), set(BUNDLE_FILES))
            for name in archive.namelist():
                content = archive.read(name).decode('utf-8')
                for secret in canaries:
                    self.assertNotIn(secret, content)
                self.assertNotIn('[VOICE/', content)
                self.assertNotIn('session.dialogue', content)
            manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual(set(manifest['files_included']), set(archive.namelist()))
            self.assertFalse(manifest['raw_audio_included'])
            self.assertFalse(manifest['conversation_content_included'])
            self.assertTrue(manifest['redaction_applied'])
        for path in folder.iterdir():
            if path.suffix != '.zip':
                for secret in canaries:
                    self.assertNotIn(secret, path.read_text(encoding='utf-8'))
