"""Acoustic endpoint for whole-utterance STT, independent of any transcript."""
from dataclasses import dataclass
import queue
import time

import numpy as np


@dataclass(frozen=True, slots=True)
class CapturedAudio:
    pcm: bytes
    sample_rate: int
    truncated: bool
    interrupted: bool
    endpoint_at: float
    speech_end_at: float | None


class AcousticEndpoint:
    def __init__(self, rate, threshold, silence_ms=1200):
        self.rate, self.threshold = rate, threshold
        self.silence_ms = silence_ms or 1200
        self.pending = bytearray()
        self.processed = self.last_active = self.active = 0

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
                self.last_active = self.processed
                self.active += frame

    @property
    def threshold_ms(self):
        return max(self.silence_ms, 1600) if self.active >= self.rate * 3 else self.silence_ms

    @property
    def ready(self):
        return self.speech and self.processed - self.last_active >= self.rate * self.threshold_ms / 1000


def capture_pcm(sd, device, rate, timeout, settings, cancelled, capture_state):
    from core.suppress_stderr import suppress_native_stderr
    blocks = queue.Queue(maxsize=256)
    overflow = False
    endpoint = AcousticEndpoint(rate, max(.0025, min(.08, settings.noise_threshold * .75)),
                                settings.stt_endpoint_silence_ms)

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
    truncated = True
    with suppress_native_stderr(), sd.RawInputStream(samplerate=rate,
            blocksize=max(800, int(rate * settings.stt_audio_block_ms / 1000)), device=device,
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
                break
    end = time.perf_counter()
    speech_end = (last_at - (endpoint.processed - endpoint.last_active) / rate
                  if last_at is not None and endpoint.speech else None)
    return CapturedAudio(bytes(captured) if endpoint.speech else b'', rate,
                         truncated or overflow, cancelled(), end, speech_end)
