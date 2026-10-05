"""Mocked Linux contracts on the Windows reference host, NOT Pi validation."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, mock_open, patch
import zipfile

from config import Settings, ProjectPaths, default_config, merge_config
from services.platform import linux
from services.platform.resolver import PlatformServices, PlatformOperationError


class LinuxRuntimeTests(unittest.TestCase):
    def test_headless_core_construction_and_text_response_without_audio(self):
        script = '''
import platform, sys, tempfile, asyncio
from pathlib import Path
platform.system = lambda: 'Linux'
platform.machine = lambda: 'aarch64'
sys.modules['sounddevice'] = None
from config import load_settings
from core.app import ValleRaApp
from services.storage.secret_store import SecretStore
with tempfile.TemporaryDirectory() as root:
    settings = load_settings(root=Path(root))
    app = ValleRaApp(settings, SecretStore(Path(root) / 'secrets.json'), text_only=True)
    assert app.listener is None
    asyncio.run(app.speaker.say('fixture response'))
    assert app.speaker._queue.empty()
    assert 'services.platform.windows_tts' not in sys.modules
    assert 'services.apps.workplace_windows' not in sys.modules
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_windows_import_does_not_require_linux_backend(self):
        script = "import core.app, services.health.probes, sys; assert 'services.platform.linux' not in sys.modules"
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_hardware_identity_not_architecture_or_serial(self):
        for model, expected in [(b'Raspberry Pi 5 Model B Rev 1.0\x00', True), (b'Other ARM board', False)]:
            with patch.object(Path, 'open', mock_open(read_data=model)):
                self.assertIs(linux.metadata()['raspberry_pi'], expected)
        with patch.object(Path, 'open', side_effect=FileNotFoundError):
            self.assertIsNone(linux.metadata()['raspberry_pi'])

    def test_headless_file_open_is_unavailable_without_process(self):
        with patch.dict(os.environ, {}, clear=True), patch('subprocess.run') as run:
            with self.assertRaises(PlatformOperationError) as caught:
                PlatformServices('Linux', 'arm64').open_file('/tmp/example')
            self.assertEqual(caught.exception.status, 'not_available')
            run.assert_not_called()
            self.assertEqual(PlatformServices('Linux', 'arm64').report()['capabilities']['file_open'], 'NOT_AVAILABLE')

    def test_argv_no_shell_interpolation_and_no_verification_claim(self):
        with patch('shutil.which', return_value='/usr/bin/fixture'), patch('subprocess.Popen') as spawn:
            spawn.return_value.poll.return_value = None
            self.assertTrue(linux.open_application({'command': 'fixture "a b" ";rm" "$(anything)"'}))
            self.assertEqual(spawn.call_args.args[0], ['/usr/bin/fixture', 'a b', ';rm', '$(anything)'])
            self.assertIs(spawn.call_args.kwargs['shell'], False)

    def test_launch_errors_are_typed_and_sanitized(self):
        with patch('shutil.which', return_value=None):
            with self.assertRaises(PlatformOperationError) as caught:
                linux.open_application({'command': 'missing-fixture-3c'})
            self.assertEqual(caught.exception.status, 'not_found')
        for error, status in [(PermissionError('SECRET'), 'permission_denied'), (OSError('SECRET'), 'execution_failed')]:
            with patch('shutil.which', return_value='/fixture'), patch('subprocess.Popen', side_effect=error):
                with self.assertRaises(PlatformOperationError) as caught:
                    linux.open_application({'command': 'fixture'})
                self.assertEqual(caught.exception.status, status)
                self.assertNotIn('SECRET', str(caught.exception))

    def test_early_process_failure_not_success(self):
        with patch('shutil.which', return_value='/fixture'), patch('subprocess.Popen') as spawn:
            spawn.return_value.poll.return_value = 2
            with self.assertRaises(PlatformOperationError):
                linux.open_application({'command': 'fixture'})

    def test_desktop_opener_argv_and_nonzero(self):
        with patch.dict(os.environ, {'DISPLAY': ':fixture'}), patch('shutil.which', return_value='/usr/bin/xdg-open'), \
             patch('subprocess.run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(linux.open_file('/tmp/a b'))
            self.assertEqual(run.call_args.args[0], ['/usr/bin/xdg-open', '/tmp/a b'])
            self.assertFalse(run.call_args.kwargs['shell'])
            run.return_value.returncode = 4
            with self.assertRaises(PlatformOperationError) as caught:
                linux.open_file('/tmp/a b')
            self.assertEqual(caught.exception.status, 'execution_failed')

    def test_system_operations_are_explicitly_unsupported_without_sudo(self):
        with patch('subprocess.run') as run:
            for action in ('shutdown', 'reboot', 'cancel_shutdown', 'lock'):
                with self.assertRaises(PlatformOperationError) as caught:
                    PlatformServices('Linux', 'arm64').system_action(action)
                self.assertEqual(caught.exception.status, 'unsupported')
            run.assert_not_called()

    def test_linux_text_speaker_no_audio_queue_or_windows_import(self):
        from core.speak import Speaker
        with tempfile.TemporaryDirectory() as root, patch('core.speak.resolve_platform', return_value=PlatformServices('Linux', 'arm64')):
            speaker = Speaker(Settings(ProjectPaths.from_root(Path(root))))
            speaker.on_text = Mock()
            asyncio.run(speaker.say('fixture'))
            speaker.on_text.assert_called_once_with('fixture')
            self.assertTrue(speaker._queue.empty())
            self.assertFalse(speaker.busy)

    def test_linux_manual_index_no_windows_scan(self):
        from services.apps.indexer import ApplicationIndexer
        with tempfile.TemporaryDirectory() as root, patch('platform.system', return_value='Linux'), \
             patch.object(Path, 'rglob', side_effect=AssertionError('Windows scan')):
            index = ApplicationIndexer(Path(root) / 'apps.json', {'код': 'Editor'})
            index.file.save({'applications': [{'name': 'WindowsOnly'}], 'manual': [dict(name='Editor', command='editor', aliases=[])]})
            apps = index.rebuild()
            self.assertEqual(len(apps), 1)
            self.assertIn('код', apps[0]['aliases'])

    def test_linux_default_paths_existing_only_and_config_compatible(self):
        with patch('platform.system', return_value='Linux'), patch.object(Path, 'is_dir', return_value=False):
            self.assertEqual(default_config()['user_directories'], [])
            custom = {'user_directories': ['C:/Users/Example/Documents']}
            self.assertEqual(merge_config(custom)['user_directories'], custom['user_directories'])

    def test_linux_doctor_and_bundle_honesty(self):
        from services.health.checks import doctor
        from services.health.bundle import collect
        with tempfile.TemporaryDirectory() as root, patch('platform.system', return_value='Linux'), \
             patch('platform.machine', return_value='aarch64'), patch('services.platform.linux.metadata',
                 return_value=dict(raspberry_pi=True, headless=True, desktop_opener=False)), \
             patch('services.health.checks.models', return_value=[]), \
             patch('services.health.probes.audio_inventory', return_value=[]):
            rows, report = doctor(Path(root))
            self.assertEqual(report['platform']['architecture'], 'arm64')
            self.assertEqual(next(r for r in rows if r['name'] == 'TTS')['status'], 'NOT_AVAILABLE')
            self.assertEqual(report['platform']['capabilities']['window_control'], 'NOT_IMPLEMENTED')
            # Bundle uses a separate read-only snapshot, never raw hardware/environment.
            bundle = collect(Path(root))
            with zipfile.ZipFile(bundle / 'diagnostics.zip') as archive:
                system = json.loads(archive.read('system.json'))
                self.assertTrue(system['platform']['raspberry_pi'])
                self.assertNotIn('serial', str(system).lower())

    def test_bootstrap_check_never_installs_or_downloads(self):
        import install
        args = SimpleNamespace(profile='raspberry_pi', check=True)
        with patch('install.parse_args', return_value=args), patch('platform.system', return_value='Linux'), \
             patch('platform.machine', return_value='aarch64'), patch('install.find_spec', return_value=True), \
             patch('subprocess.run') as run:
            self.assertEqual(install.main(), 0)
            run.assert_not_called()
