import tempfile
import unittest
from pathlib import Path

from services.storage.memory_store import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def test_memory_crud(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.json")
            store.remember("браузер", "Firefox")
            self.assertEqual(store.find("браузер")[0]["value"], "Firefox")
            self.assertEqual(store.forget("браузер"), 1)
            self.assertEqual(store.all(), [])

