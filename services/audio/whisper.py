from __future__ import annotations

import io
import logging
import math
import threading
import time
import wave
from contextlib import contextmanager
from importlib.util import find_spec

from core.models import RecognitionResult
from services.audio.model_cache import resolve_model

logger = logging.getLogger(__name__)


class WhisperRecognizer:
    """Lazy local Whisper recognizer used to refine unrestricted Vosk text."""

    def __init__(self, settings):
        self.settings = settings
        self.enabled = bool(settings.stt_whisper_enabled)
        self._model = None
        self._model_lock = threading.Lock()
        self._load_announced = False
        self.last_error = ""
        self._retry_after = 0.0
        self.transcription_count = 0
        self.skipped_silence = 0
        self.last_duration_seconds = 0.0
        self.last_timings: dict[str, float] = {}

    @contextmanager
    def _measure(self, stage):
        started = time.perf_counter()
        if getattr(self.settings, "measure_performance", False):
            print(f"[PERF] {stage}: початок (Whisper worker)", flush=True)
        try:
            yield
        finally:
            self.last_timings[stage] = (time.perf_counter() - started) * 1000

    @property
    def package_available(self) -> bool:
        return find_spec("faster_whisper") is not None

    def status(self) -> tuple[bool, str]:
        if not self.enabled:
            return True, "вимкнений у конфігурації; використовується Vosk"
        if not self.package_available:
            return False, "faster-whisper не встановлено; виконайте python install.py"
        if self.last_error:
            return False, self.last_error
        state = "модель завантажена" if self._model is not None else "готовий до завантаження"
        return True, (
            f"{self.settings.stt_whisper_model}; {state}; "
            f"розпізнавань: {self.transcription_count}; "
            f"пропущено тиші: {self.skipped_silence}"
        )

    def note_skip(self, reason: str) -> None:
        if reason == "silence":
            self.skipped_silence += 1

    def prepare(self) -> tuple[bool, str]:
        if not self.enabled:
            return self.status()
        try:
            self._get_model()
            return self.status()
        except Exception as exc:
            self.last_error = str(exc)
            self._retry_after = time.monotonic() + 60.0
            logger.exception("Whisper model preparation failed")
            return False, self.last_error

    def transcribe(self, pcm: bytes, sample_rate: int) -> RecognitionResult:
        if not self.enabled or not pcm:
            return RecognitionResult("", 0.0, "whisper")
        if not self.package_available:
            self.last_error = (
                "faster-whisper не встановлено; запустіть python install.py"
            )
            return RecognitionResult("", 0.0, "whisper")
        if time.monotonic() < self._retry_after:
            return RecognitionResult("", 0.0, "whisper")

        try:
            started = time.perf_counter()
            model = self._get_model()
            audio = self._wav_buffer(pcm, sample_rate)
            segments, info = model.transcribe(
                audio,
                language=self.settings.language,
                beam_size=self.settings.stt_whisper_beam_size,
                vad_filter=True,
                vad_parameters={
                    "min_silence_duration_ms": self.settings.stt_whisper_silence_ms,
                },
                condition_on_previous_text=False,
                initial_prompt=self.settings.stt_whisper_prompt or None,
                hotwords=self.settings.stt_whisper_hotwords or None,
                word_timestamps=getattr(self.settings, "benchmark_word_timestamps", True),
            )
            materialized = list(segments)
            text = " ".join(segment.text.strip() for segment in materialized).strip()
            confidence = self._confidence(materialized, info)
            self.transcription_count += 1
            self.last_duration_seconds = time.perf_counter() - started
            self.last_error = ""
            return RecognitionResult(text, confidence, "whisper")
        except Exception as exc:
            self.last_error = str(exc)
            self._retry_after = time.monotonic() + 60.0
            logger.exception("Whisper transcription failed; keeping Vosk result")
            return RecognitionResult("", 0.0, "whisper")

    def _get_model(self):
        if self._model is not None:
            return self._model
        if not self.package_available:
            raise RuntimeError(
                "faster-whisper не встановлено; запустіть python install.py"
            )

        with self._model_lock:
            if self._model is not None:
                return self._model
            if not self._load_announced:
                print(
                    "[STT] Завантажую в пам’ять локальну Whisper-модель "
                    f"{self.settings.stt_whisper_model}..."
                )
                self._load_announced = True
            with self._measure("whisper.import"):
                from faster_whisper import WhisperModel
                from faster_whisper.utils import download_model

            model_dir = self.settings.paths.models_dir / "faster-whisper"
            model_dir.mkdir(parents=True, exist_ok=True)
            with self._measure("whisper.resolve_files"):
                source = resolve_model(
                    self.settings.stt_whisper_model, model_dir, download_model,
                    local_files_only=getattr(self.settings, "benchmark_local_files_only", False),
                )
            with self._measure("whisper.load_weights"):
                self._model = WhisperModel(
                    source,
                    device=self.settings.stt_whisper_device,
                    compute_type=self.settings.stt_whisper_compute_type,
                    cpu_threads=self.settings.stt_whisper_cpu_threads,
                    download_root=str(model_dir),
                )
            self.last_error = ""
            self._retry_after = 0.0
            print(
                "[STT] Whisper готовий: "
                f"{self.settings.stt_whisper_model} "
                f"({self.settings.stt_whisper_device}/"
                f"{self.settings.stt_whisper_compute_type})."
            )
            return self._model

    @staticmethod
    def _wav_buffer(pcm: bytes, sample_rate: int) -> io.BytesIO:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(pcm)
        buffer.seek(0)
        return buffer

    @staticmethod
    def _confidence(segments, info) -> float:
        word_probabilities = [
            float(word.probability)
            for segment in segments
            for word in (segment.words or [])
        ]
        if word_probabilities:
            acoustic = sum(word_probabilities) / len(word_probabilities)
        elif segments:
            acoustic = sum(
                math.exp(min(0.0, float(segment.avg_logprob)))
                for segment in segments
            ) / len(segments)
        else:
            return 0.0

        speech_probability = 1.0 - (
            sum(float(segment.no_speech_prob) for segment in segments)
            / len(segments)
        )
        language_probability = float(getattr(info, "language_probability", 1.0))
        return max(
            0.0,
            min(1.0, acoustic * speech_probability * max(0.5, language_probability)),
        )
