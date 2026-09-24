import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from testing.probes.whisper import cached_models, edit_distance, load_audio, normalize, score, summarize


class AccuracyTests(unittest.TestCase):
    def test_normalization_ignores_case_and_punctuation_not_spelling(self):
        self.assertEqual(normalize("КОМАНДА: відкрий браузер!"), "команда відкрий браузер")
        self.assertEqual(normalize("пам’ятаєш"), normalize("пам'ятаєш"))
        self.assertNotEqual(normalize("Луцьк"), normalize("Лучк"))

    def test_edit_distance_counts_substitution_deletion_insertion(self):
        self.assertEqual(edit_distance(["a", "b"], ["a", "c"]), 1)
        self.assertEqual(edit_distance(["a", "b"], ["a"]), 1)
        self.assertEqual(edit_distance(["a"], ["a", "b"]), 1)
        self.assertEqual(edit_distance([], ["a"]), 1)

    def test_empty_transcript_counts_all_reference_words_as_errors(self):
        result = score("Команда відкрий браузер", "")
        self.assertEqual(result["word_errors"], 3)
        self.assertEqual(result["wer_percent"], 100)
        self.assertFalse(result["exact"])
        self.assertFalse(result["command_prefix_correct"])

    def test_command_prefix_invention_is_counted(self):
        self.assertFalse(score("відкрий браузер", "Команда відкрий браузер")["command_prefix_correct"])
        self.assertTrue(score("Привіт!", "Привіт.")["exact"])

    def test_inserted_hint_words_can_make_wer_exceed_one_hundred_percent(self):
        result = score("привіт", "привіт Валера Команда браузер")
        self.assertEqual(result["wer_percent"], 300)

    def test_weighted_wer_and_rtf_not_averages_of_sentence_percentages(self):
        rows = [{"event": "load", "model": "base", "seconds": 2}]
        for seconds, duration, expected, text in ((2, 1, "слово", "помилка"), (2, 3, "раз два три", "раз два три")):
            rows.append({"event": "sample", "model": "base", "seconds": seconds,
                         "audio_seconds": duration, **score(expected, text)})
        result, = summarize(rows)
        self.assertEqual(result["wer_percent"], 25)
        self.assertEqual(result["rtf"], 1)
        self.assertEqual(result["exact"], 1)


class OfflineBenchmarkTests(unittest.TestCase):
    def test_cached_lookup_never_falls_back_to_network_without_flag(self):
        settings = SimpleNamespace(paths=SimpleNamespace(models_dir=Path("models")))
        snapshot = Mock(side_effect=FileNotFoundError)
        with patch.dict("sys.modules", {"huggingface_hub": SimpleNamespace(snapshot_download=snapshot)}):
            with self.assertRaisesRegex(RuntimeError, "missing cached"):
                cached_models(settings, ["base"], download=False)
        snapshot.assert_called_once()
        self.assertTrue(snapshot.call_args.kwargs["local_files_only"])
        self.assertIs(snapshot.call_args.kwargs["token"], False)

    def test_incomplete_snapshot_is_rejected_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = SimpleNamespace(paths=SimpleNamespace(models_dir=Path(directory)))
            snapshot = Mock(return_value=directory)
            with patch.dict("sys.modules", {"huggingface_hub": SimpleNamespace(snapshot_download=snapshot)}):
                with self.assertRaisesRegex(RuntimeError, "missing cached"):
                    cached_models(settings, ["tiny"], download=False)
            snapshot.assert_called_once()

    def test_download_requires_flag_and_fetches_public_weights_only(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = SimpleNamespace(paths=SimpleNamespace(models_dir=Path(directory)))
            snapshot = Mock(side_effect=[FileNotFoundError(), directory])
            with patch.dict("sys.modules", {"huggingface_hub": SimpleNamespace(snapshot_download=snapshot)}):
                result = cached_models(settings, ["base"], download=True)
            self.assertEqual(result["base"], str(Path(directory).resolve()))
            self.assertIs(snapshot.call_args.kwargs["token"], False)
            self.assertEqual(snapshot.call_args.kwargs["repo_id"], "Systran/faster-whisper-base")
            self.assertNotIn("*.py", snapshot.call_args.kwargs["allow_patterns"])

    def test_audio_validation_requires_pcm16_mono(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.wav"
            for channels in (1, 2):
                with wave.open(str(path), "wb") as output:
                    output.setnchannels(channels)
                    output.setsampwidth(2)
                    output.setframerate(16000)
                    output.writeframes(b"\x00\x00" * 16000 * channels)
                if channels == 1:
                    pcm, rate, seconds = load_audio(path)
                    self.assertEqual((len(pcm), rate, seconds), (32000, 16000, 1))
                else:
                    with self.assertRaises(ValueError):
                        load_audio(path)
