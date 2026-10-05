"""Benchmark-only options must not silently alter production STT settings."""
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
import tempfile

from config import Settings, ProjectPaths
from services.audio.backends import FasterWhisperBackend, primary_settings
from testing.probes.moonshine_candidate import MoonshineCandidate
from testing.probes.stt_benchmark import read_matrix


class LatencyBenchmarkTests(TestCase):
    def test_matrix_is_explicit_and_contains_independent_comparisons(self):
        matrix = read_matrix(SimpleNamespace(matrix='testing/stt_latency_matrix.json', candidates=None))
        self.assertEqual(len(matrix), 4)
        self.assertNotIn('single_pass', matrix[0])
        self.assertTrue(matrix[1]['single_pass'])
        self.assertFalse(matrix[2]['word_timestamps'])
        self.assertNotIn('single_pass', matrix[2])

    def test_runtime_defaults_unchanged_benchmark_options_explicit(self):
        settings = Settings(ProjectPaths.from_root(Path.cwd()))
        backend = FasterWhisperBackend(primary_settings(settings), process=False)
        model = Mock()
        model.transcribe.return_value = ([], SimpleNamespace(language_probability=1))
        backend.recognizer._model = model
        backend.transcribe(bytes(3200), 16000)
        self.assertTrue(model.transcribe.call_args.kwargs['word_timestamps'])
        self.assertNotIn('temperature', model.transcribe.call_args.kwargs)
        backend.recognizer.settings.benchmark_decode_options = {'temperature': 0.0, 'best_of': 1}
        backend.recognizer.settings.benchmark_word_timestamps = False
        backend.transcribe(bytes(3200), 16000)
        self.assertEqual(model.transcribe.call_args.kwargs['temperature'], 0.0)
        self.assertFalse(model.transcribe.call_args.kwargs['word_timestamps'])

    def test_missing_moonshine_is_explicit_no_download(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = MoonshineCandidate(directory)
            ok, reason = candidate.prepare()
            self.assertFalse(ok)
            self.assertIn('missing', reason)

    def test_moonshine_has_no_action_confidence_and_uses_v2_api(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = MoonshineCandidate(directory)
            api = Mock()
            stream = api.OfflineRecognizer.from_moonshine_v2.return_value.create_stream.return_value
            stream.result.text = 'Привіт'
            with patch('pathlib.Path.is_file', return_value=True), patch.dict('sys.modules', sherpa_onnx=api):
                self.assertTrue(candidate.prepare()[0])
            result = candidate.transcribe(bytes(3200), 16000)
            self.assertEqual(result.text, 'Привіт')
            self.assertEqual(result.confidence, 0)
            self.assertTrue(result.recognition_unreliable)
