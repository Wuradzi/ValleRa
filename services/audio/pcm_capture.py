"""Acoustic endpoint for whole-utterance STT, independent of any transcript."""
from dataclasses import dataclass
import queue
import time
import logging

import numpy as np


@dataclass(frozen=True, slots=True)
class CapturedAudio:
    pcm: bytes
    sample_rate: int
    truncated: bool
    interrupted: bool
    endpoint_at: float
    speech_end_at: float | None
    capture_started_at: float | None = None
    speech_started_at: float | None = None
    capture_returned_at: float | None = None
    endpoint_reason: str = 'unknown'


class AcousticEndpoint:
    def __init__(self, rate, threshold, silence_ms=1200, *, short_ms=None, long_ms=1600):
        self.rate, self.threshold = rate, threshold
        self.silence_ms = silence_ms or 1200
        self.pending = bytearray()
        self.processed = self.last_active = self.active = 0
        self.first_active = None
        self.short_ms, self.long_ms = short_ms, long_ms

    @property
    def speech(self):
        return self.active >= self.rate * .25

    def feed(self, pcm):
        self.pending.extend(pcm)
        frame = max(1, self.rate // 50)
        while len(self.pending) >= frame * 2:
            data = bytes(self.pending[:frame * 2])
            del self.pending[:frame * 2]
            samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768
            self.processed += frame
            if float(np.sqrt(np.mean(samples * samples))) >= self.threshold:
                if self.first_active is None:
                    self.first_active = self.processed - frame
                self.last_active = self.processed
                self.active += frame

    @property
    def threshold_ms(self):
        span = self.last_active - (self.first_active or 0)
        if self.short_ms is not None and span < self.rate:
            return self.short_ms
        return max(self.silence_ms, self.long_ms) if span >= self.rate * 3 else self.silence_ms

    @property
    def ready(self):
        return self.speech and self.processed - self.last_active >= self.rate * self.threshold_ms / 1000


def capture_pcm(sd, device, rate, timeout, settings, cancelled, capture_state):
    from core.suppress_stderr import suppress_native_stderr
    blocks = queue.Queue(maxsize=256)
    overflow = False
    endpoint = AcousticEndpoint(rate, max(.0025, min(.08, settings.noise_threshold * .75)),
                                settings.stt_endpoint_silence_ms,
                                short_ms=settings.stt_pcm_short_silence_ms,
                                long_ms=settings.stt_pcm_long_silence_ms)

    def callback(data, frames, time_info, status):
        nonlocal overflow
        if status:
            overflow = True
        try:
            blocks.put_nowait((bytes(data), time.perf_counter()))
        except queue.Full:
            overflow = True

    captured = bytearray()
    last_at = None
    started = time.monotonic()
    capture_started = time.perf_counter()
    endpoint_at = None
    truncated = True
    with suppress_native_stderr(), sd.RawInputStream(samplerate=rate,
            blocksize=max(1, int(rate * min(100, settings.stt_audio_block_ms) / 1000)), device=device,
            dtype='int16', channels=1, callback=callback), capture_state():
        while time.monotonic() - started < timeout and not cancelled():
            try:
                data, last_at = blocks.get(timeout=.05)
            except queue.Empty:
                continue
            captured.extend(data)
            endpoint.feed(data)
            if endpoint.ready and blocks.empty():
                truncated = False
                endpoint_at = time.perf_counter()
                break
    end = time.perf_counter()
    speech_end = (last_at - (endpoint.processed - endpoint.last_active) / rate
                  if last_at is not None and endpoint.speech else None)
    speech_started = (last_at - (endpoint.processed - endpoint.first_active) / rate
                      if last_at is not None and endpoint.speech else None)
    reason = 'interrupted' if cancelled() else ('silence' if not truncated else 'capture_limit')
    logging.getLogger(__name__).info(
        'stt.capture capture_total_ms=%.1f speech_started=%s speech_last_detected=%s '
        'speech_end_estimated=%s endpoint_detected=%.6f capture_returned=%.6f '
        'speech_end_to_endpoint_ms=%s endpoint_reason=%s effective_threshold_ms=%s '
        'speech_span_ms=%.1f active_audio_ms=%.1f overflow=%s',
        (end - capture_started) * 1000, speech_started, speech_end, speech_end, endpoint_at or end, end,
        ((endpoint_at or end) - speech_end) * 1000 if speech_end is not None else None,
        reason, endpoint.threshold_ms,
        (speech_end - speech_started) * 1000 if speech_started is not None else 0,
        endpoint.active / rate * 1000, overflow)
    return CapturedAudio(bytes(captured) if endpoint.speech else b'', rate,
                         truncated or overflow, cancelled(), endpoint_at or end, speech_end,
                         capture_started, speech_started, end, reason)
