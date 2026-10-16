import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from config import Settings, ProjectPaths
from core.app import RavenApp
from services.audio.selection import save_selection


class STTSelectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(ProjectPaths.from_root(Path(self.temp.name)))
        self.path = self.settings.paths.config_file
        self.original = {'stt': {'backend': 'auto', 'groq_timeout_seconds': 12}, 'custom': {'keep': 7}}
        self.path.write_text(json.dumps(self.original), encoding='utf-8')

    def test_persists_selection_only_and_preserves_backup(self):
        save_selection(self.settings, 'groq')
        result = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(result, {**self.original, 'stt': {'backend': 'groq', 'groq_timeout_seconds': 12}})
        self.assertEqual(self.settings.stt_backend, 'auto')
        self.assertEqual(json.loads(self.path.with_suffix('.json.bak1').read_text()), self.original)
        save_selection(self.settings, 'local')
        self.assertEqual(json.loads(self.path.read_text())['stt']['backend'], 'auto')

    def test_corrupt_config_never_overwritten(self):
        self.path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            save_selection(self.settings, 'groq')
        self.assertEqual(self.path.read_text(), '{broken')

    def test_invalid_selection_never_written(self):
        with self.assertRaises(ValueError):
            save_selection(self.settings, 'arbitrary')
        self.assertEqual(json.loads(self.path.read_text()), self.original)

    async def test_cloud_consent_required_and_no_hot_swap(self):
        app = RavenApp.__new__(RavenApp)
        app.settings = self.settings
        app.listener = Mock()
        app.confirmation = Mock()
        status, _ = await app.web_control('stt_backend', {'backend': 'groq'})
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(self.path.read_text()), self.original)
        status, _ = await app.web_control('stt_backend', {'backend': 'groq', 'cloud_consent': True})
        self.assertEqual(status, 200)
        self.assertEqual(app._stt_saved_selection, 'groq')
        app.listener.close.assert_not_called()
        app.listener.interrupt.assert_not_called()
        app.confirmation.submit.assert_not_called()

    async def test_write_failure_does_not_claim_success(self):
        app = RavenApp.__new__(RavenApp)
        app.settings = self.settings
        with patch('services.audio.selection.save_selection', side_effect=OSError('private-path')):
            status, body = await app.web_control('stt_backend', {'backend': 'local'})
        self.assertEqual(status, 400)
        self.assertNotIn('private-path', str(body))
        self.assertFalse(hasattr(app, '_stt_saved_selection'))
