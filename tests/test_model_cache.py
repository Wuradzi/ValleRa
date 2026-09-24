import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from services.audio.model_cache import resolve_model


class ModelCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cache = Path(self.temporary.name)
        self.model = self.cache / "snapshot"
        self.model.mkdir()
        for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
            (self.model / name).write_bytes(b"fixture")

    def test_complete_cache_never_calls_network(self):
        download = Mock(return_value=str(self.model))
        self.assertEqual(resolve_model("base", self.cache, download), str(self.model))
        download.assert_called_once_with("base", cache_dir=str(self.cache), local_files_only=True)

    def test_explicit_local_path_does_not_call_hub(self):
        download = Mock()
        self.assertEqual(resolve_model(str(self.model), self.cache, download), str(self.model))
        download.assert_not_called()

    def test_missing_cache_downloads_once(self):
        download = Mock(side_effect=[FileNotFoundError(), str(self.model)])
        self.assertEqual(resolve_model("base", self.cache, download), str(self.model))
        self.assertEqual([call.kwargs["local_files_only"] for call in download.call_args_list], [True, False])

    def test_offline_missing_cache_does_not_download(self):
        download = Mock(side_effect=FileNotFoundError())
        with self.assertRaises(FileNotFoundError):
            resolve_model("base", self.cache, download, local_files_only=True)
        download.assert_called_once()

    def test_missing_tokenizer_cannot_trigger_implicit_network_in_constructor(self):
        (self.model / "tokenizer.json").unlink()
        download = Mock(return_value=str(self.model))
        with self.assertRaises(FileNotFoundError):
            resolve_model("base", self.cache, download, local_files_only=True)
        download.assert_called_once()

    def test_incomplete_cache_is_checked_after_download_too(self):
        (self.model / "model.bin").write_bytes(b"")
        download = Mock(return_value=str(self.model))
        with self.assertRaises(FileNotFoundError):
            resolve_model("base", self.cache, download)
        self.assertEqual(download.call_count, 2)

    def test_other_errors_do_not_trigger_network_fallback(self):
        for error in (PermissionError(), ValueError("invalid model")):
            download = Mock(side_effect=error)
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                resolve_model("base", self.cache, download)
            download.assert_called_once()
