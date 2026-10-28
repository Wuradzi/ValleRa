"""Native boundary tests through tester.py. No microphone, network or live Core."""
import asyncio
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

from native_ui.transport import NativeTransport


class FakeApp:
    def __init__(self):
        self.messages = []
        self.controls = []
        self.state = {'status': 'ready', 'microphone': True, 'microphone_available': True}

    def web_state(self):
        return self.state

    async def _submit_text(self, text, request_id=None):
        self.messages.append((text, request_id))

    async def web_control(self, action, data):
        self.controls.append((action, data))
        return 200, {'ok': True}


class NativeTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = FakeApp()
        self.packets = []
        self.ui = NativeTransport(self.app, self.packets.append)

    async def asyncTearDown(self):
        await self.ui.close()

    async def test_actions_use_existing_boundary_and_correlation(self):
        status, _ = await self.ui.dispatch({'action': 'message', 'text': 'так', 'request_id': 'A'})
        self.assertEqual(status, 202)
        await self.ui.submission
        self.assertEqual(self.app.messages, [('так', 'A')])
        status, _ = await self.ui.dispatch({'action': 'confirm', 'accept': True, 'request_id': 'B'})
        self.assertEqual(status, 200)
        self.assertEqual(self.app.controls[-1][1]['request_id'], 'B')
        for action, fields in [('microphone', {'enabled': False}), ('stop', {}),
                               ('cancel_progress', {'task_id': 'job', 'kind': 'file_search'})]:
            await self.ui.dispatch({'action': action, **fields})
            self.assertEqual(self.app.controls[-1], (action, {'action': action, **fields}))

    async def test_rejects_invalid_and_closes_without_late_dispatch(self):
        status, _ = await self.ui.dispatch({'action': 'message', 'text': 'x'*4001})
        self.assertEqual(status, 400)
        status, _ = await self.ui.dispatch({'action': 'execute_arbitrary_tool'})
        self.assertEqual(status, 400)
        await self.ui.close()
        self.assertEqual((await self.ui.dispatch({'action': 'stop'}))[0], 409)
        self.assertFalse(self.app.controls)

    async def test_push_is_bounded_copied_and_idle_has_no_timer(self):
        await self.ui.start()
        for i in range(400):
            self.ui.publish('assistant', str(i))
        self.assertLessEqual(len(self.ui.events), 300)
        await asyncio.sleep(.15)
        self.assertEqual(len(self.packets), 1)
        self.assertIsNone(self.ui._active_refresh)
        self.app.state['status'] = 'thinking'
        self.assertEqual(self.packets[0]['state']['status'], 'ready')
        self.ui.notify()
        await asyncio.sleep(.15)
        self.assertIsNotNone(self.ui._active_refresh)
        await self.ui.close()
        count = len(self.packets)
        await asyncio.sleep(.3)
        self.assertEqual(len(self.packets), count)
        self.assertIsNone(self.ui.server)

    async def test_submission_failure_becomes_notice_and_pending_work_is_cancelled(self):
        async def failed(*args):
            raise RuntimeError('fixture')
        self.app._submit_text = failed
        await self.ui.dispatch({'action': 'message', 'text': 'test'})
        with self.assertLogs('core.web_ui', level='ERROR'):
            await self.ui.submission
        self.assertEqual(self.ui.events[-1]['kind'], 'notice')
        cancelled = []

        async def pending(*args):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        self.app._submit_text = pending
        await self.ui.dispatch({'action': 'message', 'text': 'wait'})
        await asyncio.sleep(0)
        await self.ui.close()
        self.assertTrue(cancelled)


class NativeLaunchTests(unittest.TestCase):
    def test_cli_keeps_web_alias_and_rejects_mixed_modes(self):
        from main import parse_args
        for flags, expected in [(['--web-ui'], True), (['--ui', 'web'], True),
                                (['--ui', 'native'], False), (['--ui', 'headless'], False)]:
            with patch.object(sys, 'argv', ['main.py', *flags]):
                self.assertEqual(parse_args().web_ui, expected)
        with patch.object(sys, 'argv', ['main.py', '--web-ui', '--ui', 'native']):
            with self.assertRaises(SystemExit):
                parse_args()

    def test_headless_import_does_not_load_qt(self):
        code = "import main,sys; assert not any(n.startswith('PySide6') for n in sys.modules); sys.argv=['main.py','--ui','headless']; assert main.parse_args().ui=='headless'"
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(importlib.util.find_spec('PySide6'), 'Optional PySide6 is not installed')
    def test_offscreen_widgets_and_async_core(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, str(root / 'testing/native_ui_smoke.py')],
                                cwd=root, env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
                                capture_output=True, text=True, timeout=35)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_no_browser_engine_dependency(self):
        root = Path(__file__).resolve().parents[1]
        for path in (root / 'native_ui').glob('*.py'):
            text = path.read_text(encoding='utf-8')
            for forbidden in ('QWebEngine', 'QtWebKit', 'import webview', 'setHtml(', 'setMarkdown('):
                self.assertNotIn(forbidden, text)
        self.assertTrue(shutil.which('python') or sys.executable)
