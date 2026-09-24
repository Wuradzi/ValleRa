import tempfile
import unittest
from pathlib import Path

from core.atomic_json import AtomicJSONFile


class AtomicJSONTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AtomicJSONFile(Path(temp_dir) / "data.json", {"items": []})
            storage.save({"items": [{"value": 1}]})
            self.assertEqual(storage.load()["items"][0]["value"], 1)

