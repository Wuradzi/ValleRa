"""Platform-neutral PCM16 STT boundary. No dialogue, action or LLM types."""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import time
from types import SimpleNamespace
from typing import Protocol
from services.audio.profiles import resolve_profile

from core.models import RecognitionResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BackendMetadata:
    backend: str
    model: str
    language: str = 'uk'
    device: str = 'cpu'
    task: str = 'transcribe'
    compute_type: str = 'unknown'


class STTBackend(Protocol):
    metadata: BackendMetadata

    def prepare(self) -> tuple[bool, str]: ...
    def transcribe(self, pcm: bytes, sample_rate: int) -> RecognitionResult: ...
    def close(self) -> None: ...


def primary_settings(settings, profile=None):
    """Profile selection is independent of old Vosk-refinement tuning."""
    values = {name: getattr(settings, name) for name in dir(settings)
              if name.startswith('stt_whisper_')}
    profile = profile or resolve_profile(settings)[0]
    values.update(stt_whisper_model=profile.model,
                  stt_whisper_enabled=True, stt_whisper_device=profile.device,
                  stt_whisper_compute_type=profile.compute_type,
                  stt_whisper_prompt=profile.initial_prompt, stt_whisper_hotwords=profile.hotwords,
                  stt_whisper_escalation_model=profile.escalation_model,
                  stt_whisper_escalation_confidence=profile.escalation_confidence,
                  stt_whisper_local_files_only=True)
    return SimpleNamespace(**values, paths=settings.paths, language='uk',
                           stt_selective_whisper_enabled=True)


class FasterWhisperBackend:
    def __init__(self, settings, *, process=True):
        from services.audio.whisper import WhisperRecognizer
        from services.audio.whisper_process import ProcessWhisperRecognizer
        self.metadata = BackendMetadata('faster-whisper', settings.stt_whisper_model,
                                        device=settings.stt_whisper_device, compute_type=settings.stt_whisper_compute_type)
        self.recognizer = ProcessWhisperRecognizer(settings) if process else WhisperRecognizer(settings)

    def prepare(self):
        ok, detail = self.recognizer.prepare()
        if not ok:
            detail = f'backend=faster-whisper model={self.metadata.model}: {detail}; run python tools/download_whisper_model.py'
        return ok, detail

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


def create_backend(settings, profile, *, process=True, allow_download=False):
    if profile.backend == 'faster-whisper':
        selected = primary_settings(settings, profile)
        selected.stt_whisper_local_files_only = not allow_download
        return FasterWhisperBackend(selected, process=process)
    if profile.backend == 'sherpa-onnx':
        from services.audio.sherpa_backend import SherpaOnnxBackend
        return SherpaOnnxBackend(settings, profile)
    if profile.backend == 'vosk':
        source = settings.stt_model_path if profile.model == 'configured-vosk' else profile.model
        return VoskBackend(settings.paths.project_root / source)
    raise ValueError('Unsupported STT backend')
