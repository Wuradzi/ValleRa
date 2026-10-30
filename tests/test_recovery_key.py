"""Regression for audit 2026-10-29 #1: the recovery key never lands beside the vault."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
import setup_wizard
from core.logging_setup import _redact

KEY = "recovery-key-for-tests-0123456789abcdef"


class RecoveryKeyDeliveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.outside = self.base / "outside"
        self.outside.mkdir()
        self.out = io.StringIO()

    def deliver(self, answers, secrets):
        answers, secrets = iter(answers), iter(secrets)
        with patch.object(setup_wizard, "print"):
            return setup_wizard.deliver_recovery_key(
                KEY, self.project,
                ask=lambda _prompt: next(answers),
                ask_secret=lambda _prompt: next(secrets),
                out=self.out,
            )

    def test_default_flow_shows_key_once_and_writes_no_file(self):
        result = self.deliver([""], [KEY[-6:]])

        self.assertIsNone(result)
        self.assertEqual(self.out.getvalue().count(KEY), 1)
        self.assertEqual(list(self.project.iterdir()), [])
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_wrong_confirmation_is_repeated_until_key_matches(self):
        secrets = ["", "wrong!", KEY[-6:]]

        self.deliver([""], secrets)  # StopIteration would mean an extra prompt

    def test_export_inside_project_is_refused_then_outside_path_is_used(self):
        inside = self.project / "RECOVERY_KEY.txt"
        nested = self.project / "data" / "key.txt"
        target = self.outside / "raven-recovery.txt"

        result = self.deliver([str(inside), str(nested), str(target)], [KEY[-6:]])

        self.assertEqual(result, target.resolve())
        self.assertFalse(inside.exists())
        self.assertIn(KEY, target.read_text(encoding="utf-8"))
        self.assertEqual(list(self.project.iterdir()), [])

    def test_existing_file_is_never_overwritten(self):
        target = self.outside / "existing.txt"
        target.write_text("keep", encoding="utf-8")

        self.assertIn("існує", setup_wizard.recovery_export_allowed(target, self.project))
        self.deliver([str(target), ""], [KEY[-6:]])
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")

    def test_key_is_masked_in_session_logs(self):
        self.deliver([""], [KEY[-6:]])

        self.assertNotIn(KEY, _redact(f"stdout line {KEY}"))


class LocalRecoveryCopyWarningTests(unittest.TestCase):
    def test_warns_only_when_file_is_in_project_root(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(main, "console_print") as shown:
            root = Path(temp)
            self.assertFalse(main.warn_local_recovery_copy(root))
            (root / "RECOVERY_KEY.txt").write_text("x", encoding="utf-8")
            self.assertTrue(main.warn_local_recovery_copy(root))
            self.assertIn("RECOVERY_KEY.txt", shown.call_args.args[0])
            self.assertTrue((root / "RECOVERY_KEY.txt").exists())  # warning never deletes data


if __name__ == "__main__":
    unittest.main()
