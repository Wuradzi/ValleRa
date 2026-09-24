"""Optional conservative endpoint, without another model or discarded PCM.

This is an experiment, not a replacement for Vosk's speech detector. A quiet
microphone alone never ends a turn: a stable Vosk partial is also required.
All durations use the consumed audio timeline, not decoder/CPU wall time.
"""
from __future__ import annotations

import re

import numpy as np


class QuietEndpoint:
    UNFINISHED = frozenset({
        "і", "й", "а", "але", "або", "бо", "що", "щоб", "як", "про", "в", "у", "на",
        "до", "з", "із", "для", "без", "не", "це", "хочу", "хотів", "запитати",
        "скажи", "розкажи", "поясни", "знайди", "відкрий",
    })

    def __init__(self, sample_rate: int, silence_ms: int, *, adaptive: bool = False):
        self.rate = sample_rate
        self.adaptive = adaptive
        self.silence_samples = round(sample_rate * silence_ms / 1000)
        self.frame_samples = max(1, round(sample_rate * .02))
        self.pending = bytearray()
        self.consumed = 0
        self.processed = 0
        self.last_active = 0
        self.active_samples = 0
        self.partial = ""
        self.changed_at = 0

    def required_silence(self, text: str) -> int:
        """Conservative lexical hint, NOT a semantic sentence-completeness model.

        Commands, long utterances and obvious unfinished tails keep the original
        delay. Short stable chat can end earlier, but never on text alone.
        """
        if not self.adaptive:
            return self.silence_samples
        words = re.findall(r"[\w’']+", text.casefold())
        if (1 <= len(words) <= 6 and "команда" not in words
                and words[-1] not in self.UNFINISHED and self.active_samples <= self.rate * 4):
            return min(self.silence_samples, round(self.rate * .9))
        return self.silence_samples

    def feed(self, pcm: bytes) -> None:
        self.consumed += len(pcm) // 2
        self.pending.extend(pcm)
        size = self.frame_samples * 2
        complete = len(self.pending) // size * size
        if not complete:
            return
        frames = np.frombuffer(bytes(self.pending[:complete]), dtype=np.int16).astype(np.float32)
        frames = frames.reshape(-1, self.frame_samples)
        del self.pending[:complete]
        # Low threshold intentionally favours waiting on quiet speech/noise.
        active = np.sqrt(np.mean(frames * frames, axis=1)) >= 32
        indices = np.flatnonzero(active)
        if indices.size:
            self.last_active = self.processed + (int(indices[-1]) + 1) * self.frame_samples
            self.active_samples += len(indices) * self.frame_samples
        self.processed += len(frames) * self.frame_samples

    def ready(self, partial: str) -> bool:
        text = partial.strip()
        if text != self.partial:
            self.partial = text
            self.changed_at = self.consumed
        return bool(
            text
            and self.active_samples >= self.rate * .3
            and self.processed - self.last_active >= self.required_silence(text)
            and self.consumed - self.changed_at >= self.rate * .5
        )
