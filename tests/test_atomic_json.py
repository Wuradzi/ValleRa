import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.atomic_json import AtomicJSONFile


class AtomicJSONTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AtomicJSONFile(Path(temp_dir) / "data.json", {"items": []})
            storage.save({"items": [{"value": 1}]})
            self.assertEqual(storage.load()["items"][0]["value"], 1)

    def test_forget_history_leaves_no_backup_of_deleted_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "data.json"
            storage = AtomicJSONFile(path, {"items": []}, backup_count=3)
            for value in ("old", "secret", "kept"):
                storage.save({"items": [value]})
            self.assertTrue(path.with_name("data.json.bak1").exists())
            unrelated = path.with_name("data.json.bak-manual")
            unrelated.write_text("{}", encoding="utf-8")
            storage.save({"items": []}, forget_history=True)
            self.assertEqual(sorted(p.name for p in Path(temp_dir).iterdir()),
                             ["data.json", "data.json.bak-manual"])
            self.assertEqual(storage.load(), {"items": []})
            # Later ordinary saves rotate again, starting from the cleared state.
            storage.save({"items": ["new"]})
            backup = path.with_name("data.json.bak1").read_text(encoding="utf-8")
            self.assertNotIn("secret", backup)

    def test_undeletable_backup_is_reported_not_hidden(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "data.json"
            storage = AtomicJSONFile(path, {})
            storage.save({"a": 1})
            storage.save({"a": 2})
            original_unlink = Path.unlink

            def locked(self, *args, **kwargs):
                if self.name == "data.json.bak1":
                    raise PermissionError("locked")
                return original_unlink(self, *args, **kwargs)

            with mock.patch.object(Path, "unlink", locked), self.assertLogs("core.atomic_json", "WARNING"):
                remaining = storage.purge_backups()
            self.assertEqual([p.name for p in remaining], ["data.json.bak1"])

