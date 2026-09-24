"""Fixed-window energy observation, independent of input callback block size.

This is only the existing capture noise gate. It neither discards audio nor
decides that the user finished speaking.
"""
from __future__ import annotations

import numpy as np


class SpeechEnergyGate:
    def __init__(self, sample_rate: int, threshold: float):
        self.window_bytes = max(800, int(sample_rate * 0.25)) * 2
        self.threshold = threshold
        self.pending = bytearray()
        self.observed = False

    def feed(self, pcm: bytes) -> bool:
        if self.observed:
            return True
        self.pending.extend(pcm)
        while len(self.pending) >= self.window_bytes:
            samples = np.frombuffer(bytes(self.pending[:self.window_bytes]), dtype=np.int16).astype(np.float32)
            del self.pending[:self.window_bytes]
            rms = float(np.sqrt(np.mean(np.square(samples / 32768.0))))
            if rms >= self.threshold:
                self.observed = True
                self.pending.clear()
                break
        return self.observed
