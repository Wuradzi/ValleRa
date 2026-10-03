"""Primary whole-utterance STT; legacy constrained Vosk capture is untouched."""
import logging
import math

from core.listen import VoskListener
from core.models import RecognitionResult
from core.performance import TurnTiming
from services.audio.backends import FasterWhisperBackend, VoskBackend, primary_settings
from services.audio.endpoint import QuietEndpoint
from services.audio.pcm_capture import capture_pcm

logger = logging.getLogger(__name__)


class SpeechListener(VoskListener):
    def __init__(self, settings):
        super().__init__(settings)
        self.vosk_backend = VoskBackend(self.model_path)
        self.primary = None
        if settings.stt_backend == 'faster-whisper':
            self.primary = FasterWhisperBackend(primary_settings(settings))
            self.whisper = self.primary.recognizer  # Existing lifecycle/performance owner.
        else:
            self.whisper.enabled = False  # Explicit Vosk-only low-resource backend.

    def _get_model(self):
        return self.vosk_backend.get_model()

    def prepare(self):
        return self.primary.prepare() if self.primary is not None else self.vosk_backend.prepare()

    def status(self):
        if self.primary is None:
            return True, 'Vosk (explicit low-resource backend)'
        ok, detail = self.whisper.status()
        return ok, f'faster-whisper/{self.primary.metadata.model}; {detail}'

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
        # Existing single-job drain keeps timed-out inference isolated. No Vosk
        # transcript is needed to start the primary decoder or finish capture.
        with self.performance.span('stt.primary_transcribe'):
            result, outcome, elapsed = self._refinement_wait.run(self.primary, capture.pcm, capture.sample_rate,
                min(4000, self.settings.stt_primary_timeout_ms), self.settings.stt_primary_timeout_ms,
                self._cancelled)
        if self._cancelled() or outcome == 'cancelled':
            return RecognitionResult('', 0, 'interrupted')
        failed = (outcome != 'done' or not result.text.strip() or not math.isfinite(result.confidence)
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
        logger.info('stt.backend=%s model=%s language=uk task=transcribe latency_ms=%.1f '
                    'stt_final_engine=%s recognition_unreliable=%s capture_truncated=%s',
                    self.primary.metadata.backend, self.primary.metadata.model, elapsed,
                    result.engine, failed, capture.truncated)
        return result

    def close(self):
        super().close()
        self.vosk_backend.close()
