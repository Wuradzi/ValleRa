"""Platform-neutral PCM16 STT boundary. No dialogue, action or LLM types."""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import time
from types import SimpleNamespace
from typing import Protocol

from core.models import RecognitionResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BackendMetadata:
    backend: str
    model: str
    language: str = 'uk'
    device: str = 'cpu'
    task: str = 'transcribe'


class STTBackend(Protocol):
    metadata: BackendMetadata

    def prepare(self) -> tuple[bool, str]: ...
    def transcribe(self, pcm: bytes, sample_rate: int) -> RecognitionResult: ...
    def close(self) -> None: ...


def primary_settings(settings):
    """Profile selection is independent of old Vosk-refinement tuning."""
    values = {name: getattr(settings, name) for name in dir(settings)
              if name.startswith('stt_whisper_')}
    profile = settings.stt_quality_profile
    values.update(stt_whisper_model={'quality': 'large-v3', 'balanced': 'large-v3-turbo',
                                   'low_resource': settings.stt_low_resource_model}[profile],
                  stt_whisper_enabled=True, stt_whisper_device=settings.stt_primary_device,
                  stt_whisper_compute_type='auto',
                  stt_whisper_local_files_only=settings.stt_primary_local_files_only)
    return SimpleNamespace(**values, paths=settings.paths, language='uk',
                           stt_selective_whisper_enabled=True)


class FasterWhisperBackend:
    def __init__(self, settings, *, process=True):
        from services.audio.whisper import WhisperRecognizer
        from services.audio.whisper_process import ProcessWhisperRecognizer
        self.metadata = BackendMetadata('faster-whisper', settings.stt_whisper_model,
                                        device=settings.stt_whisper_device)
        self.recognizer = ProcessWhisperRecognizer(settings) if process else WhisperRecognizer(settings)

    def prepare(self):
        return self.recognizer.prepare()

    def transcribe(self, pcm, sample_rate):
        return self.recognizer.transcribe(pcm, sample_rate)

    def close(self):
        close = getattr(self.recognizer, 'close', None)
        if close is not None:
            close()


class VoskBackend:
    def __init__(self, model_path):
        import threading
        self.metadata = BackendMetadata('vosk', str(model_path))
        self._model = None
        self._lock = threading.Lock()
        self.load_ms = 0.0

    def get_model(self):
        with self._lock:
            if self._model is None:
                from vosk import Model
                started = time.perf_counter()
                self._model = Model(self.metadata.model)
                self.load_ms = (time.perf_counter() - started) * 1000
                logger.info('stt.model_load backend=vosk ms=%.1f', self.load_ms)
            return self._model

    def prepare(self):
        try:
            self.get_model()
            return True, 'vosk ready'
        except Exception as exc:
            return False, type(exc).__name__

    def transcribe(self, pcm, sample_rate):
        from vosk import KaldiRecognizer
        recognizer = KaldiRecognizer(self.get_model(), sample_rate)
        recognizer.SetWords(True)
        parts = []
        for offset in range(0, len(pcm), 8000):
            if recognizer.AcceptWaveform(pcm[offset:offset + 8000]):
                parts.append(json.loads(recognizer.Result()))
        parts.append(json.loads(recognizer.FinalResult()))
        words = [word for part in parts for word in part.get('result', [])]
        return RecognitionResult(' '.join(part.get('text', '').strip() for part in parts).strip(),
                                 sum(word['conf'] for word in words) / len(words) if words else 0, 'vosk')

    def close(self):
        self._model = None
