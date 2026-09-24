import tempfile
import unittest
from pathlib import Path

from services.storage.secret_store import SecretStore


class SecretStoreTests(unittest.TestCase):
    def test_password_and_recovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "secrets.json"
            store = SecretStore(path)
            recovery = store.create("strong-password")
            store.set("Steam", "example-secret")

            reopened = SecretStore(path)
            self.assertTrue(reopened.unlock_with_password("strong-password"))
            self.assertEqual(reopened.get("Steam"), "example-secret")

            recovered = SecretStore(path)
            self.assertTrue(recovered.unlock_with_recovery_key(recovery))
            self.assertEqual(recovered.get("Steam"), "example-secret")

    def test_lookup_is_exact_and_case_insensitive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SecretStore(Path(temp_dir) / "secrets.json")
            store.create("strong-password")
            store.set("GitHub", "one")
            store.set("GitLab", "two")

            self.assertEqual(store.get("github"), "one")
            self.assertIsNone(store.get("git"))

