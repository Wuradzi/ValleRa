"""No models, speech synthesis, microphone or API: synthetic measurement tests."""
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from testing.probes.voice_resources import VoiceResources, sensors, public_report, read_checkpoint
from testing.probes.tts_benchmark import FakeTTSBackend, benchmark, wav_info


def _crash_after_checkpoint(path):
    import os
    VoiceResources(path).start()
    os._exit(7)  # Isolated fixture: intentionally bypass normal finally/cleanup.


class VoiceBenchmarkInfrastructureTests(unittest.TestCase):
    def test_abrupt_candidate_exit_preserves_atomic_checkpoint(self):
        import multiprocessing
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'resources.json'
            worker = multiprocessing.get_context('spawn').Process(target=_crash_after_checkpoint, args=(path,))
            worker.start()
            try:
                worker.join(10)
                self.assertEqual(worker.exitcode, 7)
                self.assertGreater(read_checkpoint(path)['samples'], 0)
            finally:
                if worker.is_alive():
                    worker.terminate()
                    worker.join(2)
                worker.close()

    def test_unavailable_windows_counters_do_not_abort_sampling(self):
        with patch('psutil.swap_memory', side_effect=RuntimeError('Counters disabled')):
            sampler = VoiceResources()
            sampler.sample()
        self.assertIsNone(sampler.data['swap_used_mb'])
        self.assertIsNotNone(sampler.data['rss_mb'])
        self.assertIsNotNone(sampler.data['available_before_load_mb'])

    def test_system_swap_deltas(self):
        with patch('psutil.swap_memory', side_effect=[
            SimpleNamespace(used=1048576, sin=10, sout=20),
            SimpleNamespace(used=2097152, sin=12, sout=25),
        ]):
            sampler = VoiceResources()
            sampler.sample()
            sampler.sample()
        self.assertEqual(sampler.data['swap_used_delta_mb'], 1)
        self.assertEqual(sampler.data['swap_in_delta_bytes'], 2)
        self.assertEqual(sampler.data['swap_out_delta_bytes'], 5)

    def test_numeric_sampling_no_swap_and_cpu(self):
        process = Mock()
        process.memory_info.return_value.rss = 100 * 1048576
        process.cpu_times.side_effect = [SimpleNamespace(user=1, system=1), SimpleNamespace(user=2, system=1)]
        with patch('psutil.Process', return_value=process), \
             patch('psutil.virtual_memory', side_effect=[SimpleNamespace(available=500*1048576), SimpleNamespace(available=400*1048576)]), \
             patch('psutil.swap_memory', return_value=SimpleNamespace(used=0, sin=0, sout=0)), \
             patch('testing.probes.voice_resources.sensors', return_value=(None, None)):
            sampler = VoiceResources()
            sampler.sample()
            sampler.inference()
        self.assertEqual(sampler.data['available_before_load_mb'], 500)
        self.assertEqual(sampler.data['min_available_inference_mb'], 400)
        self.assertEqual(sampler.data['peak_rss_mb'], 100)
        self.assertEqual(sampler.data['swap_used_delta_mb'], 0)
        self.assertEqual(sampler.data['swap_out_delta_bytes'], 0)
        self.assertEqual(sampler.data['cpu_seconds'], 1)
        self.assertGreater(sampler.data['cpu_one_core_percent'], 0)

    def test_sensors_unavailable_and_windows_do_not_read(self):
        with patch('platform.system', return_value='Windows'), patch.object(Path, 'open') as opened:
            self.assertEqual(sensors(), (None, None))
            opened.assert_not_called()
        with patch('platform.system', return_value='Linux'), patch.object(Path, 'open', side_effect=PermissionError):
            self.assertEqual(sensors(), (None, None))

    def test_numeric_linux_sensors(self):
        import io
        with patch('platform.system', return_value='Linux'), \
             patch.object(Path, 'open', side_effect=[io.StringIO('52000'), io.StringIO('0x50005')]):
            self.assertEqual(sensors(), (52.0, 0x50005))

    def test_checkpoint_survives_failure_and_excludes_unknown_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'resources.json'
            sampler = VoiceResources(path).start()
            sampler.stop()
            data = json.loads(path.read_text())
            data['secret'] = 'CANARY'
            path.write_text(json.dumps(data))
            recovered = read_checkpoint(path)
            self.assertGreater(recovered['samples'], 0)
            self.assertIn('peak_rss_mb', recovered)
            self.assertNotIn('secret', recovered)

    def test_fake_tts_success_metadata_and_no_subjective_score(self):
        result = benchmark(FakeTTSBackend())
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(result['rows']), 2)
        self.assertEqual(result['sample_rates'], [16000])
        self.assertAlmostEqual(result['corpus_duration_seconds'], .2)
        self.assertGreaterEqual(result['load_ms'], 0)
        self.assertIn('synthesis_rtf', result['rows'][0])
        self.assertEqual(result['human_quality_evaluation'], 'NOT_TESTED')
        self.assertIn('available_before_load_mb', result['resources'])

    def test_fake_failure_and_malformed_keep_resources(self):
        for mode in ('failure', 'malformed'):
            result = benchmark(FakeTTSBackend(mode))
            self.assertEqual(result['status'], 'failed')
            self.assertGreater(result['resources']['samples'], 0)

    def test_prepare_failure_still_reports_resources_and_closes(self):
        backend = FakeTTSBackend()
        backend.prepare = Mock(return_value=(False, '/home/CANARY/API_KEY'))
        backend.close = Mock()
        result = benchmark(backend)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('resources', result)
        backend.close.assert_called_once()
        self.assertNotIn('CANARY', json.dumps(result))

    def test_cooperative_cancellation_and_unsupported(self):
        result = benchmark(FakeTTSBackend('cancel'), cancellation=True)
        self.assertEqual(result['cancellation'], 'cancelled')
        backend = FakeTTSBackend()
        backend.supports_cancellation = False
        self.assertEqual(benchmark(backend, cancellation=True)['cancellation'], 'NOT_SUPPORTED')

    def test_truncated_wave_rejected(self):
        import wave
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.wav'
            with wave.open(str(path), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(bytes(3200))
            path.write_bytes(path.read_bytes()[:-100])
            with self.assertRaises(ValueError):
                wav_info(path)

    def test_report_scrubs_paths_text_and_errors_preserving_metrics(self):
        source = dict(candidate={'model': '/home/private/model', 'id': 'CANARY'},
                      metadata={'model': 'C:/Users/private/model'}, error='API_KEY=CANARY',
                      rows=[{'reference': 'private', 'transcript': 'private', 'latency_ms': 12,
                             'entities_preserved': {'private': True}}],
                      summary={'wer_percent': 3, 'realtime_factor': .5})
        result = public_report(source)
        text = json.dumps(result)
        for secret in ('private', '/home/', 'C:/Users', 'CANARY', 'API_KEY'):
            self.assertNotIn(secret, text)
        self.assertEqual(result['summary'], source['summary'])
        self.assertEqual(public_report(result), result)

    def test_stt_variant_compatibility_without_model(self):
        from config import Settings, ProjectPaths
        from services.audio.backends import BackendMetadata
        from core.models import RecognitionResult
        from testing.probes.stt_benchmark import _variant
        backend = Mock(metadata=BackendMetadata('vosk', '/home/private/model'))
        backend.prepare.return_value = (True, '')
        backend.transcribe.return_value = RecognitionResult('привіт', .9, 'vosk')
        row = {'id': 'one', 'reference': 'привіт'}
        connection = Mock()
        with patch('config.load_settings', return_value=Settings(ProjectPaths.from_root(Path.cwd()))), \
             patch('services.audio.backends.create_backend', return_value=backend), \
             patch('testing.probes.stt_benchmark.read_samples', return_value=([(row, bytes(3200),16000,.1)], 'a'*64,True)):
            _variant(connection, '.', {'id': 'vosk', 'backend': 'vosk', 'model': 'configured-vosk'}, 'cpu')
        result = connection.send.call_args.args[0]
        self.assertEqual(result['status'], 'completed')
        for key in ('load_ms', 'first_inference_ms', 'rows', 'summary', 'metadata', 'resources'):
            self.assertIn(key, result)
        self.assertEqual(result['summary']['wer_percent'], 0)
        self.assertIn('peak_vram_mb', result['resources'])
        self.assertEqual(result['sample_rates'], [16000])
        self.assertNotIn('/home', json.dumps(result))
