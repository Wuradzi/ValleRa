"""Offline-only Moonshine v2 candidate; deliberately not registered for runtime."""
from pathlib import Path

import numpy as np

from core.models import RecognitionResult
from services.audio.backends import BackendMetadata


class MoonshineCandidate:
    def __init__(self, directory, threads=2):
        self.directory = Path(directory)
        self.threads = threads
        self.metadata = BackendMetadata('moonshine-onnx', self.directory.name, compute_type='int8')
        self.recognizer = None

    def prepare(self):
        files = {key: self.directory / name for key, name in (
            ('encoder', 'encoder_model.ort'), ('decoder', 'decoder_model_merged.ort'), ('tokens', 'tokens.txt'))}
        if not all(path.is_file() for path in files.values()):
            return False, 'missing Moonshine v2 model files (offline only; no automatic download)'
        try:
            import sherpa_onnx
            self.recognizer = sherpa_onnx.OfflineRecognizer.from_moonshine_v2(
                **{key: str(path) for key, path in files.items()},
                num_threads=self.threads, provider='cpu')
        except (ImportError, AttributeError):
            return False, 'missing sherpa-onnx runtime with Moonshine v2 support'
        return True, 'Moonshine v2 Ukrainian candidate ready'

    def transcribe(self, pcm, sample_rate):
        stream = self.recognizer.create_stream()
        stream.accept_waveform(sample_rate, np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768)
        self.recognizer.decode_stream(stream)
        # No calibrated confidence: never manufacture ACTION eligibility.
        return RecognitionResult(stream.result.text.strip(), 0, 'moonshine', recognition_unreliable=True)

    def close(self):
        self.recognizer = None
