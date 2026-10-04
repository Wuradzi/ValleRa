"""Experimental CPU ONNX Whisper adapter. Never invent ASR confidence."""
import logging
from pathlib import Path
import threading
import time

import numpy as np

from core.models import RecognitionResult
from services.audio.backends import BackendMetadata

logger = logging.getLogger(__name__)


class SherpaOnnxBackend:
    def __init__(self, settings, profile):
        self.profile = profile
        source = Path(profile.model)
        self.directory = source if source.is_absolute() else settings.paths.models_dir / profile.model
        self.threads = max(1, settings.stt_whisper_cpu_threads)
        self.metadata = BackendMetadata('sherpa-onnx', profile.model, compute_type=profile.compute_type)
        self._model = None
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self.last_error = ''

    def files(self):
        suffix = '.int8.onnx' if self.profile.compute_type == 'int8' else '.onnx'
        matches = [list(self.directory.glob(pattern)) for pattern in
                   (f'*-encoder{suffix}', f'*-decoder{suffix}', '*-tokens.txt')]
        if any(len(group) != 1 or group[0].stat().st_size == 0 for group in matches):
            raise FileNotFoundError('Need exactly one multilingual encoder, decoder and tokens file')
        if any('.en-' in group[0].name for group in matches):
            raise ValueError('English-only Whisper cannot recognize Ukrainian')
        return [str(group[0]) for group in matches]

    def prepare(self):
        with self._lock:
            if self._closed.is_set():
                return False, 'closed'
            if self._model is not None:
                return True, 'experimental; confidence unavailable, voice ACTION disabled'
            started = time.perf_counter()
            try:
                if self.profile.hotwords or self.profile.initial_prompt:
                    raise ValueError('Whisper ONNX adapter does not support prompt/hotwords; remove these overrides')
                if self.profile.compute_type not in {'int8', 'float32'}:
                    raise ValueError('ONNX candidate requires int8 or float32 files')
                encoder, decoder, tokens = self.files()
                import sherpa_onnx
                self._model = sherpa_onnx.OfflineRecognizer.from_whisper(
                    encoder=encoder, decoder=decoder, tokens=tokens, language='uk', task='transcribe',
                    provider='cpu', num_threads=self.threads)
                self.last_error = ''
                return True, 'experimental; confidence unavailable, voice ACTION disabled'
            except Exception as exc:
                self.last_error = (f'profile={self.profile.name} backend=sherpa-onnx model={self.profile.model}: '
                    f'{type(exc).__name__}: {exc}; install requirements-edge.txt and prepare files as in docs/STT_BACKENDS.md')
                return False, self.last_error
            finally:
                logger.info('stt.model_load backend=sherpa-onnx ms=%.1f', (time.perf_counter() - started) * 1000)

    def transcribe(self, pcm, sample_rate):
        ok, detail = self.prepare()
        if not ok:
            logger.warning('%s', detail)
            return RecognitionResult('', 0, 'sherpa-onnx', fragmented=True, incomplete=True, recognition_unreliable=True)
        with self._lock:
            if self._closed.is_set():
                return RecognitionResult('', 0, 'interrupted')
            try:
                stream = self._model.create_stream()
                stream.accept_waveform(sample_rate, np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768)
                self._model.decode_stream(stream)
                if self._closed.is_set():
                    return RecognitionResult('', 0, 'interrupted')
                # No calibrated confidence in this API: useful for offline WER,
                # NOT automatically trusted for conversation or ACTION execution.
                return RecognitionResult(stream.result.text, 0, 'sherpa-onnx', fragmented=True,
                                         incomplete=True, recognition_unreliable=True)
            finally:
                if self._closed.is_set():
                    self._model = None

    def close(self):
        self._closed.set()
        # Do not destroy native state while a timed-out drain still owns it.
        if self._lock.acquire(blocking=False):
            try:
                self._model = None
            finally:
                self._lock.release()
