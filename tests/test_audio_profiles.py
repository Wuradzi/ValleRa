"""Hardware is fake; profiles never download models or infer ACTION permission."""
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from config import Settings, ProjectPaths, merge_config, validate_config, apply_performance_profile
from core.recognition_policy import RecognitionPolicy
from services.audio.capabilities import Capabilities
from services.audio.profiles import resolve_profile
from services.audio.backends import primary_settings, FasterWhisperBackend, create_backend
from services.audio.sherpa_backend import SherpaOnnxBackend
from testing.probes.stt_benchmark import semantic_scores, aggregate, scores, read_matrix, _variant


class DeploymentProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(ProjectPaths.from_root(Path(self.temp.name)))
        self.cpu = Capabilities('x86_64', 'windows', False, 8192)
        self.arm = Capabilities('arm64', 'linux', False, 2048)
        self.gpu = Capabilities('x86_64', 'linux', True, 32768, 12288)

    def test_auto_quality_requires_known_resources(self):
        p, _ = resolve_profile(self.settings, self.gpu)
        self.assertEqual((p.name, p.device, p.model), ('quality', 'cuda', 'large-v3'))
        self.assertEqual(resolve_profile(self.settings, Capabilities('x86_64', 'linux', True))[0].name, 'balanced')

    def test_balanced_cpu_no_cuda(self):
        p, _ = resolve_profile(self.settings, self.cpu)
        self.assertEqual((p.name, p.device, p.compute_type), ('balanced', 'cpu', 'int8'))

    def test_linux_x86_is_not_edge(self):
        self.assertEqual(resolve_profile(self.settings, Capabilities('x86_64', 'linux'))[0].name, 'balanced')

    def test_arm_edge_has_no_cuda_dependency(self):
        p, caps = resolve_profile(self.settings, self.arm)
        self.assertEqual((p.name, p.backend, p.device, caps.platform), ('edge', 'sherpa-onnx', 'cpu', 'linux'))
        self.assertNotIn('raspberry', str(asdict(p)))

    def test_explicit_quality_cpu_beats_auto(self):
        self.settings.stt_profile = 'quality'
        self.settings.stt_primary_device = 'cpu'
        self.assertEqual(resolve_profile(self.settings, self.gpu)[0].device, 'cpu')
        self.assertEqual(resolve_profile(self.settings, self.cpu)[0].name, 'quality')

    def test_profile_does_not_equal_model(self):
        self.settings.stt_profiles = {'balanced': {'model': 'medium', 'compute_type': 'float32'}}
        p, _ = resolve_profile(self.settings, self.cpu)
        self.assertEqual((p.model, p.compute_type), ('medium', 'float32'))

    def test_legacy_low_resource_preserves_explicit_model(self):
        self.settings.stt_quality_profile = 'low_resource'
        self.settings.stt_low_resource_model = 'small'
        p, _ = resolve_profile(self.settings, self.arm)
        self.assertEqual((p.name, p.backend, p.model, p.device), ('edge', 'faster-whisper', 'small', 'cpu'))

    def test_new_low_resource_alias_uses_edge_candidate(self):
        self.settings.stt_profile = 'low_resource'
        self.assertEqual(resolve_profile(self.settings, self.cpu)[0].backend, 'sherpa-onnx')

    def test_config_migration_explicit_new_beats_old(self):
        config = merge_config({'stt': {'profile': 'edge', 'quality_profile': 'quality'}})
        self.assertIsNone(config['stt']['quality_profile'])
        validate_config(config)
        old = merge_config({'stt': {'quality_profile': 'balanced'}})
        self.assertEqual(old['stt']['profiles']['balanced']['model'], 'large-v3-turbo')

    def test_legacy_pi_cli_only_maps_profile(self):
        config = apply_performance_profile(merge_config({}), 'raspberry_pi')
        self.assertEqual(config['stt']['profile'], 'edge')
        self.assertEqual(config['stt']['backend'], 'auto')
        validate_config(config)

    def test_missing_quality_never_downloads_online(self):
        self.settings.stt_primary_local_files_only = False  # Legacy knob cannot enable startup downloads.
        self.settings.stt_profile = 'quality'
        p, _ = resolve_profile(self.settings, self.cpu)
        backend = FasterWhisperBackend(primary_settings(self.settings, p), process=False)
        with patch('faster_whisper.utils.download_model', side_effect=FileNotFoundError('not cached')) as download:
            ok, detail = backend.prepare()
        self.assertFalse(ok)
        self.assertIn('large-v3', detail)
        self.assertIn('download_whisper_model.py', detail)
        self.assertTrue(all(c.kwargs['local_files_only'] for c in download.call_args_list))

    def test_missing_edge_preparation_is_actionable(self):
        p, _ = resolve_profile(self.settings, self.arm)
        backend = SherpaOnnxBackend(self.settings, p)
        ok, detail = backend.prepare()
        self.assertFalse(ok)
        for part in ('edge', 'sherpa-onnx', 'requirements-edge.txt', 'docs/STT_BACKENDS.md'):
            self.assertIn(part, detail)

    def test_edge_adapter_forces_uk_cpu_with_local_files(self):
        p, _ = resolve_profile(self.settings, self.arm)
        backend = SherpaOnnxBackend(self.settings, p)
        backend.directory.mkdir(parents=True)
        for name in ('small-encoder.int8.onnx', 'small-decoder.int8.onnx', 'small-tokens.txt'):
            (backend.directory / name).write_bytes(b'fixture')
        runtime = Mock()
        with patch.dict('sys.modules', {'sherpa_onnx': runtime}):
            self.assertTrue(backend.prepare()[0])
        kwargs = runtime.OfflineRecognizer.from_whisper.call_args.kwargs
        self.assertEqual((kwargs['provider'], kwargs['language'], kwargs['task']), ('cpu', 'uk', 'transcribe'))
        backend.close()

    def test_onnx_close_during_decode_discards_late_result(self):
        p, _ = resolve_profile(self.settings, self.arm)
        backend = SherpaOnnxBackend(self.settings, p)
        backend._model = Mock()
        entered, release = threading.Event(), threading.Event()
        results = []
        def decode(stream):
            entered.set()
            release.wait(2)
        backend._model.decode_stream.side_effect = decode
        thread = threading.Thread(target=lambda: results.append(backend.transcribe(b'12', 16000)))
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            backend.close()
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0].text, '')
        self.assertIsNone(backend._model)

    def test_onnx_result_never_invents_confidence(self):
        p, _ = resolve_profile(self.settings, self.arm)
        backend = SherpaOnnxBackend(self.settings, p)
        backend._model = Mock()
        backend._model.create_stream.return_value.result.text = 'відкрий браузер'
        result = backend.transcribe(b'\x00\x01' * 1600, 16000)
        self.assertTrue(result.recognition_unreliable)
        self.assertFalse(RecognitionPolicy.action_eligible(result))
        backend.close()
        self.assertEqual(backend.transcribe(b'12', 16000).text, '')

    def test_missing_model_benchmark_is_not_tested(self):
        p, _ = resolve_profile(self.settings, self.arm)
        fake = Mock(metadata=SimpleNamespace())
        # Real metadata remains a dataclass across every backend.
        fake.metadata = SherpaOnnxBackend(self.settings, p).metadata
        fake.prepare.return_value = False, 'missing'
        connection = Mock()
        with patch('config.load_settings', return_value=self.settings), \
             patch('testing.probes.stt_benchmark.read_samples', return_value=([], 'digest', False)), \
             patch('services.audio.backends.create_backend', return_value=fake):
            _variant(connection, '.', {'id': 'edge', 'backend': 'sherpa-onnx', 'model': 'missing', 'profile': 'edge'}, None)
        self.assertEqual(connection.send.call_args.args[0]['status'], 'NOT_TESTED')
        fake.transcribe.assert_not_called()

    def test_semantic_metrics_distinguish_meaningful_errors(self):
        annotation = {'intent': 'web_search', 'intent_phrases': ['пошукай', 'знайди'],
                      'entities': {'query': 'рецепт шаурми'}}
        good = semantic_scores(annotation, 'Пошукай рецепт шаурми')
        bad = semantic_scores(annotation, 'Послухай мені рецепт форми')
        self.assertTrue(good['intent_preserved'])
        self.assertTrue(good['entities_preserved']['query'])
        self.assertFalse(bad['intent_preserved'])
        self.assertFalse(bad['entities_preserved']['query'])
        self.assertFalse(semantic_scores(annotation, 'знайди рецепт шаурмища')['entities_preserved']['query'])

    def test_unannotated_is_not_fake_hundred_percent(self):
        row = {**scores('привіт', 'привіт'), 'latency_ms': 100, 'audio_seconds': 1}
        result = aggregate([row, {**row, 'latency_ms': 200}])
        self.assertIsNone(result['intent_preservation_accuracy'])
        self.assertEqual(result['median_transcription_ms'], 150)
        self.assertEqual(result['p95_transcription_ms'], 200)

    def test_matrix_has_distinct_cpu_and_edge_candidates(self):
        args = SimpleNamespace(matrix=Path('testing/stt_matrix.json'), candidates=None)
        matrix = read_matrix(args)
        self.assertEqual(len({x['id'] for x in matrix}), len(matrix))
        self.assertTrue(any(x['backend'] == 'sherpa-onnx' for x in matrix))
        self.assertTrue(any(x['id'] == 'turbo-cpu' for x in matrix))

    def test_vosk_baseline_model_and_custom_profile_are_resolved(self):
        matrix = read_matrix(SimpleNamespace(matrix=None, models=['vosk'], candidates=None))
        self.settings.stt_profiles = {'balanced': {'backend': 'vosk', 'model': matrix[0]['model']}}
        p, _ = resolve_profile(self.settings, self.cpu)
        self.assertEqual(Path(create_backend(self.settings, p).metadata.model),
                         self.settings.paths.project_root / self.settings.stt_model_path)
        self.settings.stt_profiles['balanced']['model'] = 'models/custom-vosk'
        p, _ = resolve_profile(self.settings, self.cpu)
        self.assertTrue(create_backend(self.settings, p).metadata.model.endswith('custom-vosk'))
