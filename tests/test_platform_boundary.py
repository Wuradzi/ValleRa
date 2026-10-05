"""Mocked platform facts and execution contracts; no Linux/Pi hardware claim."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from services.platform.resolver import resolve_platform, PlatformServices, PlatformCapabilityUnavailable


class PlatformBoundaryTests(unittest.TestCase):
    def test_resolver_facts_not_pi_detection(self):
        for os_name, arch, expected in [('Windows', 'AMD64', 'x86_64'), ('Linux', 'x86_64', 'x86_64'),
                                         ('Linux', 'aarch64', 'arm64'), ('Windows', 'ARM64', 'arm64')]:
            with patch('platform.system', return_value=os_name), patch('platform.machine', return_value=arch):
                target = resolve_platform()
            self.assertEqual((target.os, target.architecture), (os_name, expected))
            self.assertEqual(target.supports('tts'), os_name == 'Windows')
            self.assertIsNot(target.report().get('raspberry_pi'), True)

    def test_linux_core_import_does_not_load_windows_implementations(self):
        script = '''
import platform, sys
platform.system = lambda: 'Linux'
platform.machine = lambda: 'aarch64'
import main, core.app, core.speak, services.health.probes
assert 'services.audio.windows_speech' not in sys.modules
assert 'services.platform.windows_tts' not in sys.modules
assert 'services.apps.workplace_windows' not in sys.modules
assert 'win32com.client' not in sys.modules
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_windows_launch_contract(self):
        target = PlatformServices('Windows', 'x86_64')
        app = {'name': 'fixture', 'command': 'fixture.exe'}
        with patch('services.platform.windows.os.startfile', create=True) as launch:
            self.assertTrue(target.open_application(app))
            launch.assert_called_once_with('fixture.exe')

    def test_unsupported_never_success_or_shell_execution(self):
        target = PlatformServices('Linux', 'arm64')
        with patch('subprocess.Popen') as spawn:
            for operation in (lambda: target.system_action('shutdown'),
                              lambda: target.windows().close('anything'), lambda: target.speech(None, 'text', None)):
                with self.assertRaises(PlatformCapabilityUnavailable):
                    operation()
            spawn.assert_not_called()
        self.assertEqual(target.drive_roots(), [])

    def test_windows_probe_uses_stabilized_synthesis(self):
        target = PlatformServices('Windows', 'x86_64')
        with patch('services.audio.windows_speech.synthesize_windows', return_value={'status': 'ok'}) as synth:
            self.assertEqual(target.synthesize_probe('fixture.wav', 'Volodymyr'), {'status': 'ok'})
            synth.assert_called_once_with('fixture.wav', 'Volodymyr')

    def test_platform_report_contains_only_fixed_metadata(self):
        with patch('platform.system', return_value='SECRET_OS'), patch('platform.machine', return_value='SECRET_ARCH'):
            text = json.dumps(resolve_platform().report())
        self.assertNotIn('SECRET', text)
        self.assertIn('NOT_IMPLEMENTED', text)
        self.assertIn('NOT_TESTED', text)

    def test_stt_profiles_not_selected_by_platform_adapter(self):
        from config import Settings, ProjectPaths
        from services.audio.profiles import resolve_profile
        settings = Settings(ProjectPaths.from_root(Path.cwd()))
        caps = SimpleNamespace(architecture='aarch64', cuda=False, ram_mb=2048, vram_mb=0)
        for name in ('quality', 'balanced', 'edge'):
            settings.stt_profile = name
            with patch('platform.system', return_value='Linux'):
                selected, _ = resolve_profile(settings, caps)
            self.assertEqual(selected.name, name)
        self.assertEqual(PlatformServices('Linux', 'arm64').report()['hardware_validation'], 'NOT_TESTED')

    def test_confirmation_stays_before_platform_execution(self):
        from skills.system.skill import handle
        target = Mock()
        context = SimpleNamespace(confirm=AsyncMock(return_value=False))
        with patch('skills.system.skill.resolve_platform', return_value=target):
            asyncio.run(handle("вимкни комп'ютер", context, {}))
            target.system_action.assert_not_called()
            context.confirm.return_value = True
            target.system_action.return_value = True
            asyncio.run(handle("вимкни комп'ютер", context, {}))
            target.system_action.assert_called_once_with('shutdown')

    def test_doctor_linux_reports_missing_tts_not_success(self):
        from config import Settings, ProjectPaths
        from services.health.probes import tts_self_test
        with patch('platform.system', return_value='Linux'):
            row = tts_self_test(Settings(ProjectPaths.from_root(Path.cwd())))
        self.assertEqual(row['status'], 'NOT_AVAILABLE')
