"""Phase 3C.1 mocked platforms; not a new Raspberry Pi hardware run."""
import asyncio
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from config import default_config, load_settings, merge_config, migrate_config
from core.skill_loader import SkillLoader


class PiFindingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def skill(self, name, platforms, code, enabled=True):
        folder = self.root / name
        folder.mkdir()
        (folder / 'manifest.json').write_text(json.dumps(dict(name=name, platforms=platforms, enabled=enabled)))
        (folder / 'skill.py').write_text(code, encoding='utf-8')

    def media(self):
        shutil.copytree(Path(__file__).resolve().parents[1] / 'skills' / 'media', self.root / 'media')

    def test_windows_media_loads_and_executes_existing_handler(self):
        self.media()
        dependency = Mock()
        with patch('platform.system', return_value='Windows'), patch.dict(sys.modules, pyautogui=dependency):
            loaded = SkillLoader(self.root, {}).load()
            self.assertEqual([s.name for s in loaded], ['media'])
            result = asyncio.run(loaded[0].handle('збільш гучність', None))
            self.assertTrue(result.handled)
            dependency.press.assert_called_once_with('volumeup', presses=3)

    def test_linux_media_skipped_before_import_while_neutral_loads(self):
        self.media()
        self.skill('portable', ['all'], 'def handle(*args): return None')
        with patch('platform.system', return_value='Linux'), patch.dict(sys.modules, pyautogui=None), \
             patch('core.skill_loader.importlib.util.spec_from_file_location',
                   wraps=__import__('importlib.util', fromlist=['spec_from_file_location']).spec_from_file_location) as spec, \
             self.assertLogs('core.skill_loader', level='INFO') as logs:
            loaded = SkillLoader(self.root, {}).load()
        self.assertEqual([s.name for s in loaded], ['portable'])
        self.assertEqual(spec.call_count, 1)
        self.assertIn('portable', str(spec.call_args))
        self.assertTrue(any('platform_unavailable' in row for row in logs.output))
        self.assertFalse(any('ERROR' in row for row in logs.output))

    def test_generic_unsupported_and_disabled_never_imported(self):
        self.skill('unsupported', ['other'], 'raise AssertionError("must not import")')
        self.skill('disabled', ['all'], 'raise AssertionError("must not import")', enabled=False)
        with patch('core.skill_loader.importlib.util.spec_from_file_location') as spec:
            self.assertEqual(SkillLoader(self.root, {}).load(), [])
            spec.assert_not_called()

    def test_linux_supported_and_all_platforms_load(self):
        for name, platforms in [('native', ['linux']), ('neutral', ['all'])]:
            self.skill(name, platforms, 'def handle(*args): return None')
        with patch('platform.system', return_value='Linux'):
            self.assertEqual({s.name for s in SkillLoader(self.root, {}).load()}, {'native', 'neutral'})

    def pi(self, detected=True):
        return patch('services.platform.linux.metadata', return_value=dict(
            raspberry_pi=detected, headless=True, desktop_opener=False))

    def test_fresh_pi_defaults_and_runtime(self):
        with patch('platform.system', return_value='Linux'), self.pi():
            self.assertEqual(default_config()['performance']['profile'], 'raspberry_pi')
            self.assertEqual(load_settings(root=self.root, read_only=True).performance_profile, 'raspberry_pi')
        self.assertFalse((self.root / 'config.json').exists())

    def test_fresh_pi_wizard_saves_profile(self):
        from setup_wizard import run_first_start_wizard
        with patch('platform.system', return_value='Linux'), self.pi(), \
             patch('setup_wizard.choose_device', return_value=None), \
             patch('setup_wizard.calibrate_microphone', return_value=.02), \
             patch('builtins.input', return_value=''), patch('setup_wizard.console_getpass', return_value=''), \
             patch('setup_wizard.SecretStore') as vault, patch('setup_wizard.subprocess.run'):
            vault.return_value.exists = True
            run_first_start_wizard(self.root)
        config = json.loads((self.root / 'config.json').read_text(encoding='utf-8'))
        self.assertEqual(config['performance']['profile'], 'raspberry_pi')

    def test_generic_arm64_not_pi(self):
        for detected in (False, None):
            with patch('platform.system', return_value='Linux'), patch('platform.machine', return_value='aarch64'), self.pi(detected):
                self.assertEqual(default_config()['performance']['profile'], 'fast')

    def test_existing_explicit_and_missing_profile_preserved(self):
        for custom, expected in [({'performance': {'profile': 'balanced'}}, 'balanced'),
                                 ({'performance': {'profile': 'fast'}}, 'fast'),
                                 ({'performance': {'profile': 'raspberry_pi'}}, 'raspberry_pi'),
                                 ({'custom': 'preserved'}, 'fast')]:
            text = json.dumps(custom)
            (self.root / 'config.json').write_text(text)
            with patch('platform.system', return_value='Linux'), self.pi():
                self.assertEqual(load_settings(root=self.root).performance_profile, expected)
            self.assertEqual((self.root / 'config.json').read_text(), text)

    def test_cli_override_run_only(self):
        text = json.dumps({'performance': {'profile': 'balanced'}})
        (self.root / 'config.json').write_text(text)
        with patch('platform.system', return_value='Linux'), self.pi():
            self.assertEqual(load_settings('raspberry_pi', root=self.root).performance_profile, 'raspberry_pi')
            self.assertEqual(load_settings(root=self.root).performance_profile, 'balanced')
        self.assertEqual((self.root / 'config.json').read_text(), text)

    def test_windows_defaults_and_migration(self):
        with patch('platform.system', return_value='Windows'):
            self.assertEqual(default_config()['performance']['profile'], 'fast')
        old = {'config_version': 1, 'custom': {'keep': True}, 'performance': {'profile': 'balanced'}}
        with patch('platform.system', return_value='Linux'), self.pi():
            new = merge_config(old)
        self.assertEqual(new['config_version'], 2)
        self.assertEqual(new['custom'], old['custom'])
        self.assertEqual(new['performance']['profile'], 'balanced')
        self.assertEqual(old['config_version'], 1)
        self.assertEqual(migrate_config(new), new)
