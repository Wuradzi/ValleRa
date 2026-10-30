import json
import os
import tempfile
import unittest
from pathlib import Path

from core.log_retention import prune, prune_quietly, prune_sessions, prune_test_runs

DAY = 24 * 3600
NOW = 1_800_000_000.0


def session_name(index):
    return f"session-202609{index:02d}T120000.000000Z-100-{index:08x}.log"


def run_name(index):
    return f"202609{index:02d}T120000.000000Z-{index:08x}"


class LogRetentionTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.sessions = self.root / "logs" / "sessions"
        self.tests = self.root / "logs" / "tests"
        self.sessions.mkdir(parents=True)
        self.tests.mkdir(parents=True)

    def tearDown(self):
        self._temp.cleanup()

    def make_session(self, index, age_days=30):
        path = self.sessions / session_name(index)
        path.write_text("log", encoding="utf-8")
        os.utime(path, (NOW - age_days * DAY, NOW - age_days * DAY))
        return path

    def make_run(self, index, age_days=30):
        path = self.tests / run_name(index)
        path.mkdir()
        (path / "report.json").write_text("{}", encoding="utf-8")
        os.utime(path, (NOW - age_days * DAY, NOW - age_days * DAY))
        return path

    def write_evidence(self, manifest_paths=(), register=""):
        docs = self.root / "docs" / "evidence"
        docs.mkdir(parents=True, exist_ok=True)
        files = [{"id": f"E{i}", "path": path} for i, path in enumerate(manifest_paths)]
        (docs / "manifest.json").write_text(json.dumps({"files": files}), encoding="utf-8")
        (self.root / "docs" / "EVIDENCE_REGISTER.md").write_text(register, encoding="utf-8")

    def test_keeps_newest_and_removes_only_old_unregistered_entries(self):
        for index in range(1, 7):
            self.make_session(index)
        result = prune(self.sessions, self.root, keep=2, pattern="session-*.log", now=NOW)
        self.assertEqual(sorted(p.name for p in self.sessions.iterdir()), [session_name(5), session_name(6)])
        self.assertEqual(len(result.removed), 4)

    def test_registered_evidence_is_never_removed(self):
        for index in range(1, 6):
            self.make_session(index)
            self.make_run(index)
        # Manifest path, register path and a bare name without the logs/ prefix.
        self.write_evidence(
            manifest_paths=[f"logs/tests/{run_name(1)}/report.json"],
            register=f"[лог](../logs/sessions/{session_name(1)}) і прогін {run_name(2)}; "
                     f"сесія {session_name(2)}.")
        sessions = prune(self.sessions, self.root, keep=1, pattern="session-*.log", now=NOW)
        runs = prune(self.tests, self.root, keep=1, pattern="2*", now=NOW)
        self.assertTrue((self.sessions / session_name(1)).exists())
        self.assertTrue((self.sessions / session_name(2)).exists())
        self.assertTrue((self.tests / run_name(1) / "report.json").exists())
        self.assertTrue((self.tests / run_name(2)).exists())
        self.assertEqual((sessions.protected, runs.protected), (2, 2))
        self.assertEqual(sorted(sessions.removed), [session_name(3), session_name(4)])
        self.assertEqual(sorted(runs.removed), [run_name(3), run_name(4)])

    def test_unreadable_manifest_disables_pruning(self):
        for index in range(1, 5):
            self.make_session(index)
        (self.root / "docs" / "evidence").mkdir(parents=True)
        (self.root / "docs" / "evidence" / "manifest.json").write_text("{broken", encoding="utf-8")
        with self.assertLogs("core.log_retention", "WARNING"):
            result = prune(self.sessions, self.root, keep=1, pattern="session-*.log", now=NOW)
        self.assertEqual(result.removed, [])
        self.assertTrue(result.skipped_reason)
        self.assertEqual(len(list(self.sessions.iterdir())), 4)

    def test_recent_entries_survive_even_beyond_keep(self):
        # A parallel run in the same checkout must not lose its directory.
        self.make_run(1, age_days=1)
        self.make_run(2, age_days=30)
        self.make_run(3, age_days=30)
        result = prune(self.tests, self.root, keep=1, pattern="2*", now=NOW)
        self.assertEqual(result.removed, [run_name(2)])
        self.assertTrue((self.tests / run_name(1)).exists())

    def test_dry_run_and_unrelated_files_are_untouched(self):
        for index in range(1, 4):
            self.make_session(index)
        other = self.sessions / "notes.txt"
        other.write_text("keep", encoding="utf-8")
        result = prune(self.sessions, self.root, keep=1, pattern="session-*.log", now=NOW, dry_run=True)
        self.assertEqual(len(result.removed), 2)
        self.assertEqual(len(list(self.sessions.iterdir())), 4)

    def test_defaults_and_failures_never_raise(self):
        self.assertEqual(prune_sessions(self.root).removed, [])
        self.assertEqual(prune_test_runs(self.root).removed, [])

        def broken(root):
            raise PermissionError("fixture")

        with self.assertLogs("core.log_retention", "WARNING"):
            self.assertIsNone(prune_quietly(broken, self.root))


if __name__ == "__main__":
    unittest.main()
