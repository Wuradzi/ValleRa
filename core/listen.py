from __future__ import annotations

import json
import logging
import math
import os
import queue
import re
import threading
import time
from contextlib import contextmanager

import numpy as np
from vosk import SetLogLevel

from core.models import RecognitionResult
from core.performance import DISABLED_PERFORMANCE, TurnTiming
from core.suppress_stderr import suppress_native_stderr
from services.audio.whisper_process import ProcessWhisperRecognizer
from services.audio.activity import SpeechEnergyGate
from services.audio.endpoint import QuietEndpoint

logger = logging.getLogger(__name__)
SetLogLevel(-1)


class SilentAudioError(RuntimeError):
    pass


class VoskListener:
    FAST_CHAT = re.compile(
        r"^(?:валера[\s,]+)?(?:привіт|здорово|дякую|дякую тобі|як справи|"
        r"як твої справи|так|ні|добре|гаразд|зрозуміло|до побачення)[.!?]*$",
        re.IGNORECASE,
    )
    def __init__(self, settings):
        self.settings = settings
        self.model_path = settings.paths.project_root / settings.stt_model_path
        self._model = None
        self._model_lock = threading.Lock()
        self._resolved_device: int | None = None
        self._resolved_sample_rate: int | None = None
        self._interrupt = threading.Event()
        self._closed = threading.Event()
        self._paused = threading.Event()
        self.capture_active = threading.Event()
        self.whisper = ProcessWhisperRecognizer(settings)
        self.performance = DISABLED_PERFORMANCE

    def interrupt(self) -> None:
        self._interrupt.set()

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._paused.set()
            self.interrupt()
        else:
            self._paused.clear()

    @contextmanager
    def _capture_state(self):
        self.capture_active.set()
        try:
            yield
        finally:
            self.capture_active.clear()

    def close(self) -> None:
        self._closed.set()
        self.interrupt()
        self.whisper.close()

    def _get_model(self):
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    if not self.model_path.exists():
                        raise FileNotFoundError(
                            f"Vosk-модель не знайдено: {self.model_path}. "
                            "Запустіть python tools/download_vosk_model.py"
                        )
                    from vosk import Model
                    with self.performance.span("stt.vosk_load"):
                        self._model = Model(str(self.model_path))
        return self._model

    def listen_once(
        self,
        timeout_seconds: float = 8.0,
        grammar: list[str] | None = None,
    ) -> RecognitionResult:
        import sounddevice as sd

        self._interrupt.clear()
        if self._closed.is_set() or self._paused.is_set():
            return RecognitionResult("", 0.0, "interrupted")
        errors = []
        for device, sample_rate in self._input_candidates(sd):
            if self._paused.is_set():
                return RecognitionResult("", 0.0, "interrupted")
            try:
                with self.performance.span("stt.capture_vosk_including_wait"):
                    result, pcm = self._listen_on_device(
                        sd, device, sample_rate, timeout_seconds, grammar,
                    )
                self._resolved_device = device
                self._resolved_sample_rate = sample_rate
                timing = result.timing
                if (
                    grammar is None
                    and self.whisper.enabled
                    and result.engine != "interrupted"
                ):
                    refine, reason = self._should_refine(
                        result,
                        pcm,
                        sample_rate,
                    )
                    if refine:
                        with self.performance.span("stt.whisper_refinement"):
                            refined = self.whisper.transcribe(pcm, sample_rate)
                        result = self._select_result(result, refined)
                    else:
                        self.whisper.note_skip(reason)
                if self._closed.is_set() or self._interrupt.is_set():
                    return RecognitionResult("", 0.0, "interrupted")
                result.timing = timing
                if timing is not None:
                    timing.mark("recognition_ready")
                return result
            except (sd.PortAudioError, SilentAudioError) as exc:
                errors.append(f"{device} ({sample_rate} Гц): {exc}")
                logger.warning(
                    "Microphone device %s at %s Hz failed: %s",
                    device,
                    sample_rate,
                    exc,
                )
                if device == self._resolved_device:
                    self._resolved_device = None
                    self._resolved_sample_rate = None

        detail = "; ".join(errors[-3:]) or "пристроїв введення не знайдено"
        raise RuntimeError(f"Не вдалося відкрити мікрофон: {detail}")

    def probe_input(self) -> tuple[bool, str]:
        import sounddevice as sd

        errors = []
        for device, sample_rate in self._input_candidates(sd):
            if self._closed.is_set():
                return False, "Мікрофон зупинено"
            try:
                probe_chunks: list[bytes] = []

                def probe_callback(indata, frames, time_info, status):
                    if status:
                        logger.warning("Audio probe status: %s", status)
                    probe_chunks.append(bytes(indata))

                with suppress_native_stderr(), sd.RawInputStream(
                    samplerate=sample_rate,
                    blocksize=max(800, int(sample_rate * 0.1)),
                    device=device,
                    dtype="int16",
                    channels=1,
                    callback=probe_callback,
                ):
                    sd.sleep(500)
                if not self._signal_present(b"".join(probe_chunks)):
                    raise SilentAudioError("пристрій повертає нульовий аудіосигнал")
                self._resolved_device = device
                self._resolved_sample_rate = sample_rate
                name = sd.query_devices(device).get("name", str(device))
                return True, f"{name}; індекс {device}; {sample_rate} Гц"
            except (sd.PortAudioError, SilentAudioError) as exc:
                errors.append(f"{device}: {exc}")
        return False, "; ".join(errors[-3:]) or "пристроїв введення не знайдено"

    def _listen_on_device(
        self,
        sd,
        device: int,
        sample_rate: int,
        timeout_seconds: float,
        grammar: list[str] | None,
    ) -> tuple[RecognitionResult, bytes]:
        from vosk import KaldiRecognizer

        audio_queue = queue.Queue()
        model = self._get_model()
        recognizer = (
            KaldiRecognizer(
                model,
                sample_rate,
                json.dumps(grammar, ensure_ascii=False),
            )
            if grammar
            else KaldiRecognizer(model, sample_rate)
        )
        recognizer.SetWords(True)
        endpoint_ms = getattr(self.settings, "stt_endpoint_silence_ms", 0)
        adaptive_endpoint = getattr(self.settings, "stt_endpoint_adaptive", False)
        # Confirmation grammars retain native timing. Partial hypotheses never
        # become executable text: flush the decoder before normal STT routing.
        endpoint = (QuietEndpoint(sample_rate, endpoint_ms, adaptive=adaptive_endpoint)
                    if endpoint_ms and grammar is None and self.whisper.enabled and self.whisper.status()[0]
                    else None)
        captured = bytearray()
        observed_peak = 0
        speech_observed = False
        speech_threshold = max(
            0.0025,
            min(0.08, float(self.settings.noise_threshold) * 0.75),
        )
        energy_gate = SpeechEnergyGate(sample_rate, speech_threshold)

        def callback(indata, frames, time_info, status):
            nonlocal speech_observed
            if status:
                logger.warning("Audio status: %s", status)
            raw = bytes(indata)
            speech_observed = energy_gate.feed(raw)
            # Never discard quiet speech before it reaches Vosk. The previous
            # hard RMS gate produced empty recognition results on low-gain
            # laptop microphone arrays.
            audio_queue.put((raw, time.perf_counter()))

        last_block_at = None

        def finish(payload, kind="native"):
            parsed = self._parse(payload)
            logger.info("STT endpoint: kind=%s adaptive=%s block_ms=%s quiet_ms=%s backlog_ms=%s",
                         kind, adaptive_endpoint, getattr(self.settings, "stt_audio_block_ms", 250),
                         round((endpoint.processed - endpoint.last_active) * 1000 / sample_rate)
                         if endpoint is not None else None,
                         round(max(0, time.perf_counter() - last_block_at) * 1000)
                         if last_block_at is not None else None)
            if self.performance.enabled and parsed.text:
                endpoint_at = time.perf_counter()
                # Vosk word offsets are relative to the input audio timeline.
                # Callback receipt is only an estimate of device time, not an
                # acoustic measurement. Never use this for speech gating.
                words = payload.get("result", [])
                end = words[-1].get("end") if words else None
                audio_seconds = len(captured) / (2 * sample_rate)
                speech_end_at = None
                if (last_block_at is not None and isinstance(end, (int, float))
                        and math.isfinite(end) and 0 <= end <= audio_seconds):
                    speech_end_at = last_block_at - (audio_seconds - end)
                parsed.timing = TurnTiming(self.performance, endpoint_at=endpoint_at,
                                           speech_end_at=speech_end_at)
            return parsed

        started = time.monotonic()
        if self._paused.is_set():
            return RecognitionResult("", 0.0, "interrupted"), b""
        with suppress_native_stderr(), sd.RawInputStream(
            samplerate=sample_rate,
            blocksize=max(800, int(sample_rate * getattr(self.settings, "stt_audio_block_ms", 250) / 1000)),
            device=device,
            dtype="int16",
            channels=1,
            callback=callback,
        ), self._capture_state():
            while time.monotonic() - started < timeout_seconds:
                if self._interrupt.is_set() or self._paused.is_set():
                    return RecognitionResult("", 0.0, "interrupted"), bytes(captured)
                try:
                    data, last_block_at = audio_queue.get(timeout=0.25)
                except queue.Empty:
                    continue
                captured.extend(data)
                if endpoint is not None:
                    endpoint.feed(data)
                samples = np.frombuffer(data, dtype=np.int16)
                if samples.size:
                    observed_peak = max(
                        observed_peak,
                        int(np.max(np.abs(samples.astype(np.int32)))),
                    )
                if (
                    time.monotonic() - started >= 0.75
                    and observed_peak <= 1
                ):
                    raise SilentAudioError(
                        f"пристрій {device} повертає нульовий аудіосигнал"
                    )
                if recognizer.AcceptWaveform(data):
                    result = json.loads(recognizer.Result())
                    parsed = self._parse(result)
                    if parsed.text and speech_observed:
                        return finish(result), bytes(captured)
                elif endpoint is not None:
                    partial = json.loads(recognizer.PartialResult()).get("partial", "")
                    if (endpoint.ready(partial) and (adaptive_endpoint or not self.FAST_CHAT.fullmatch(partial))
                            and speech_observed and audio_queue.empty()):
                        result = json.loads(recognizer.FinalResult())
                        logger.debug("Speech endpoint: quiet pause %s ms (adaptive=%s)",
                                     round(endpoint.required_silence(partial) * 1000 / sample_rate), adaptive_endpoint)
                        return finish(result, "quiet"), bytes(captured)

        final_payload = json.loads(recognizer.FinalResult())
        parsed = self._parse(final_payload)
        if parsed.text and not speech_observed:
            logger.debug(
                "Recognition rejected below calibrated threshold %.5f",
                speech_threshold,
            )
            parsed = RecognitionResult("", 0.0, "vosk-noise")
        elif parsed.text:
            parsed = finish(final_payload, "timeout")
        return parsed, bytes(captured)

    def _input_candidates(self, sd) -> list[tuple[int, int]]:
        devices = sd.query_devices()
        host_apis = sd.query_hostapis()
        candidates: list[tuple[int, int, int]] = []
        explicit = self.settings.input_device
        try:
            default_input = int(sd.default.device[0])
        except (IndexError, TypeError, ValueError):
            default_input = -1

        for index, device in enumerate(devices):
            if int(device.get("max_input_channels", 0)) < 1:
                continue
            sample_rate = int(round(float(device.get("default_samplerate", 0))))
            if sample_rate <= 0:
                sample_rate = int(self.settings.stt_sample_rate)

            host_name = host_apis[int(device.get("hostapi", 0))].get("name", "")
            name = str(device.get("name", "")).lower()
            score = 0
            if index == explicit:
                score += 10_000
            if index == self._resolved_device:
                score += 20_000
            if index == default_input:
                score += 1_000
            if "microphone array" in name or "мікрофон" in name:
                score += 500
            if os.name == "nt":
                score += {
                    "Windows WASAPI": 300,
                    "Windows DirectSound": 200,
                    "MME": 100,
                    # WDM-KS endpoints can open successfully while returning
                    # zero-filled buffers much faster than real time.
                    "Windows WDM-KS": -500,
                }.get(host_name, 0)
            candidates.append((score, index, sample_rate))

        candidates.sort(reverse=True)
        return [(index, sample_rate) for _, index, sample_rate in candidates]

    @staticmethod
    def _parse(result: dict) -> RecognitionResult:
        words = result.get("result", [])
        confidence = (
            sum(float(word.get("conf", 0.0)) for word in words) / len(words)
            if words else 0.0
        )
        return RecognitionResult(result.get("text", "").strip(), confidence, "vosk")

    def _select_result(
        self,
        vosk_result: RecognitionResult,
        whisper_result: RecognitionResult,
    ) -> RecognitionResult:
        """Choose the accurate text without weakening the command boundary."""
        if not whisper_result.text:
            return vosk_result
        vosk_is_command = self._contains_command_prefix(vosk_result.text)
        whisper_is_command = self._contains_command_prefix(whisper_result.text)
        if vosk_is_command != whisper_is_command:
            # Never manufacture a local-command prefix from conflicting engines.
            # Keep the engine result that does not grant local execution. It can
            # still be handled as ordinary conversation or explicitly repeated.
            safe_result = whisper_result if not whisper_is_command else vosk_result
            return RecognitionResult(
                safe_result.text,
                min(vosk_result.confidence, whisper_result.confidence),
                "conflict",
            )
        if self._prefer_vosk(vosk_result):
            return vosk_result
        if (
            whisper_result.confidence < self.settings.stt_whisper_min_confidence
            and vosk_result.text
        ):
            return vosk_result
        return whisper_result

    @staticmethod
    def _contains_command_prefix(text: str) -> bool:
        normalized = " ".join(text.lower().strip().split())
        return bool(re.match(r"^команда\b", normalized))

    def _prefer_vosk(self, result: RecognitionResult) -> bool:
        # Vosk's threshold is local to this engine, not compared to Whisper's
        # probability. The policy is opt-in and never uses reference phrases.
        return (
            getattr(self.settings, "stt_refinement_policy", "legacy") == "vosk_first"
            and result.engine == "vosk"
            and bool(result.text.strip())
            and math.isfinite(result.confidence)
            and 0.85 <= result.confidence <= 1.0
        )

    def _should_refine(
        self,
        result: RecognitionResult,
        pcm: bytes,
        sample_rate: int,
    ) -> tuple[bool, str]:
        if (
            self.settings.stt_whisper_skip_silence
            and not self._has_speech_energy(
                pcm,
                sample_rate,
                float(self.settings.noise_threshold),
            )
        ):
            return False, "silence"

        # Commands still require the existing second-engine prefix check.
        if self._prefer_vosk(result) and not self._contains_command_prefix(result.text):
            return False, "vosk-first"

        # Only an exact, harmless conversational phrase may skip refinement.
        # Complex utterances and every command still use Whisper when enabled.
        if (
            getattr(self.settings, "performance_profile", "balanced") == "fast"
            and result.confidence >= 0.92
            and self.FAST_CHAT.fullmatch(result.text.strip())
        ):
            return False, "fast-chat"

        return True, ""

    @staticmethod
    def _has_speech_energy(
        pcm: bytes,
        sample_rate: int,
        noise_threshold: float,
    ) -> bool:
        if not pcm or sample_rate <= 0:
            return False
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        frame_size = max(1, int(sample_rate * 0.02))
        complete = samples.size // frame_size
        if complete == 0:
            return False
        frames = samples[: complete * frame_size].reshape(complete, frame_size)
        rms = np.sqrt(np.mean(np.square(frames), axis=1))
        threshold = max(0.0025, min(0.08, noise_threshold * 0.75))
        return int(np.count_nonzero(rms >= threshold)) >= 4

    @staticmethod
    def _signal_present(pcm: bytes) -> bool:
        if not pcm:
            return False
        samples = np.frombuffer(pcm, dtype=np.int16)
        return bool(samples.size and np.max(np.abs(samples.astype(np.int32))) > 1)
