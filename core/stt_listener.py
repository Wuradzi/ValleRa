"""Primary whole-utterance STT; legacy constrained Vosk capture is untouched."""
import logging
import math
import time

from core.listen import VoskListener
from core.models import RecognitionResult
from core.performance import TurnTiming
from services.audio.backends import VoskBackend, create_backend
from services.audio.profiles import resolve_profile
from services.audio.endpoint import QuietEndpoint
from services.audio.pcm_capture import capture_pcm

logger = logging.getLogger(__name__)


class SpeechListener(VoskListener):
    def __init__(self, settings):
        super().__init__(settings)
        self.vosk_backend = VoskBackend(self.model_path)
        self.profile, self.capabilities = resolve_profile(settings)
        logger.info('STT profile=%s backend=%s model=%s device=%s compute=%s arch=%s platform=%s',
                    self.profile.name, self.profile.backend, self.profile.model, self.profile.device,
                    self.profile.compute_type, self.capabilities.architecture, self.capabilities.platform)
        self.primary = None
        if self.profile.backend != 'vosk':
            self.primary = create_backend(settings, self.profile)
            if self.profile.backend == 'faster-whisper':
                self.whisper = self.primary.recognizer  # Existing lifecycle/performance owner.
            else:
                self.whisper.enabled = False  # Constrained Vosk never uses refinement.
        else:
            self.whisper.enabled = False  # Explicit Vosk-only low-resource backend.
            self.vosk_backend = create_backend(settings, self.profile)

    def _get_model(self):
        return self.vosk_backend.get_model()

    def prepare(self):
        ok, detail = self.primary.prepare() if self.primary is not None else self.vosk_backend.prepare()
        if not ok:
            logger.warning('STT prepare profile=%s: %s', self.profile.name, detail)
        return ok, detail

    def status(self):
        if self.primary is None:
            return True, 'Vosk (explicit low-resource backend)'
        ok, detail = (self.whisper.status() if self.profile.backend == 'faster-whisper'
                      else (False, 'experimental ONNX candidate; prepare explicitly, confidence unvalidated'))
        return ok, f'{self.profile.name}/{self.primary.metadata.backend}/{self.primary.metadata.model}; {detail}'

    def _cancelled(self):
        return self._closed.is_set() or self._paused.is_set() or self._interrupt.is_set()

    def listen_once(self, timeout_seconds=8.0, grammar=None):
        if grammar is not None or self.primary is None:
            return super().listen_once(timeout_seconds, grammar)
        import sounddevice as sd
        self._interrupt.clear()
        if self._cancelled():
            return RecognitionResult('', 0, 'interrupted')
        errors = []
        for device, rate in self._input_candidates(sd):
            try:
                with self.performance.span('stt.capture_pcm'):
                    capture = capture_pcm(sd, device, rate, timeout_seconds, self.settings,
                                          self._cancelled, self._capture_state)
            except sd.PortAudioError as exc:
                errors.append(type(exc).__name__)
                continue
            self._resolved_device, self._resolved_sample_rate = device, rate
            if capture.interrupted or self._cancelled():
                return RecognitionResult('', 0, 'interrupted')
            if not capture.pcm:
                return RecognitionResult('', 0, 'whisper')
            timing = TurnTiming(self.performance, endpoint_at=capture.endpoint_at,
                                speech_end_at=capture.speech_end_at) if self.performance.enabled else None
            return self._recognize_capture(capture, timing)
        raise RuntimeError('STT microphone unavailable: ' + ', '.join(errors))

    def _recognize_capture(self, capture, timing=None):
        if not capture.pcm:
            return RecognitionResult('', 0, 'whisper')
        stt_started = time.perf_counter()
        # Existing single-job drain keeps timed-out inference isolated. No Vosk
        # transcript is needed to start the primary decoder or finish capture.
        with self.performance.span('stt.primary_transcribe'):
            result, outcome, elapsed = self._refinement_wait.run(self.primary, capture.pcm, capture.sample_rate,
                min(4000, self.settings.stt_primary_timeout_ms), self.settings.stt_primary_timeout_ms,
                self._cancelled)
        if self._cancelled() or outcome == 'cancelled':
            return RecognitionResult('', 0, 'interrupted')
        failed = (outcome != 'done' or result.recognition_unreliable or not result.text.strip() or not math.isfinite(result.confidence)
                  or result.confidence < self.settings.stt_whisper_min_confidence)
        if failed:
            reason = outcome if outcome != 'done' else 'empty_or_unreliable'
            logger.warning('stt.fallback reason=%s target=%s model=%s', reason,
                           self.settings.stt_fallback_backend, self.primary.metadata.model)
            if self.settings.stt_fallback_backend == 'vosk':
                try:
                    result = self.vosk_backend.transcribe(capture.pcm, capture.sample_rate)
                except Exception as exc:
                    logger.warning('stt.fallback failed error=%s', type(exc).__name__)
                    result = RecognitionResult('', 0, 'vosk')
        if self._cancelled():
            return RecognitionResult('', 0, 'interrupted')
        result.utterance_incomplete = QuietEndpoint.possible_fragment(result.text)
        result.capture_truncated = capture.truncated
        result.recognition_unreliable = failed
        result.fragmented = failed or capture.truncated or result.utterance_incomplete
        result.incomplete = result.fragmented
        result.timing = timing
        if timing is not None:
            timing.mark('recognition_ready')
        ready = time.perf_counter()
        metadata = getattr(getattr(self.primary, 'recognizer', None), 'metadata', {})
        worker_timings = getattr(getattr(self.primary, 'recognizer', None), 'last_timings', {})
        if outcome != 'done':
            metadata, worker_timings = {}, {}  # Never attribute a previous job's metadata to this turn.
        if timing is not None:
            timing.values.update(stt_inference=worker_timings.get('whisper.inference'),
                                 model=metadata.get('model', self.primary.metadata.model),
                                 device=metadata.get('device', 'n/a'), escalated=metadata.get('escalated'))
        logger.info('stt.latency capture_total_ms=%s speech_end_to_endpoint_ms=%s '
                    'endpoint_to_stt_start_ms=%.1f stt_inference_ms=%s endpoint_to_transcript_ms=%.1f '
                    'speech_end_to_transcript_ms=%s model=%s backend=%s device=%s compute_type=%s '
                    'fallback=%s escalated=%s cold_or_warm=%s outcome=%s',
                    (capture.capture_returned_at - capture.capture_started_at) * 1000
                    if capture.capture_started_at is not None and capture.capture_returned_at is not None else None,
                    (capture.endpoint_at - capture.speech_end_at) * 1000 if capture.speech_end_at is not None else None,
                    (stt_started - capture.endpoint_at) * 1000,
                    worker_timings.get('whisper.inference') if outcome == 'done' else None,
                    (ready - capture.endpoint_at) * 1000,
                    (ready - capture.speech_end_at) * 1000 if capture.speech_end_at is not None else None,
                    metadata.get('model', self.primary.metadata.model), self.primary.metadata.backend,
                    metadata.get('device', self.profile.device), metadata.get('compute_type', self.profile.compute_type),
                    'model_fallback_or_failure' if failed else metadata.get('fallback', 'none'),
                    metadata.get('escalated', False), metadata.get('cold_or_warm', 'unknown'), outcome)
        logger.info('stt.backend=%s model=%s language=uk task=transcribe latency_ms=%.1f '
                    'stt_final_engine=%s recognition_unreliable=%s capture_truncated=%s rtf=%.3f',
                    self.primary.metadata.backend, self.primary.metadata.model, elapsed,
                    result.engine, failed, capture.truncated,
                    elapsed / 1000 / max(.001, len(capture.pcm) / 2 / capture.sample_rate))
        return result

    def close(self):
        super().close()
        if self.primary is not None and self.profile.backend != 'faster-whisper':
            self.primary.close()
        self.vosk_backend.close()
