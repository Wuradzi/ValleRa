"""Optional conservative endpoint, without another model or discarded PCM.

This complements Vosk's speech detector. A quiet microphone alone never ends
a turn: a stable Vosk partial or final result is also required.
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
        self.fragment_held = False
        self.fragment_merged = False
        self.hold_at = None
        self.continuation_samples = 0
        self.hold_started = None
        self.hold_deadline = None
        self.wait_actual_ms = 0

    @classmethod
    def possible_fragment(cls, text):
        words = re.findall(r"[\w’']+", text.casefold())
        # Length is not evidence of incompleteness: names and scoped replies
        # must reach the existing dialogue context, without a phrase whitelist.
        if not words or words[-1] not in cls.UNFINISHED or words[-1] == 'це':
            return False
        return len(words) > 1 or words[-1] in {'відкрий', 'знайди', 'поясни', 'розкажи'}

    def wait_expired(self, now):
        if self.hold_deadline is None:
            return False
        self.wait_actual_ms = max(0, (now - self.hold_started) * 1000)
        return now >= self.hold_deadline

    def continuation(self, now=None):
        if self.hold_at is not None:
            self.continuation_samples = self.consumed - self.hold_at
            self.wait_actual_ms = max(0, ((self.consumed / self.rate if now is None else now)
                                         - self.hold_started) * 1000)
        self.fragment_merged = True
        self.hold_at = None
        self.hold_deadline = None

    def required_silence(self, text: str) -> int:
        """Conservative lexical hint, NOT a semantic sentence-completeness model.

        Short complete phrases can finish earlier. Long speech tolerates more
        silence; uncertain short phrases receive a separate bounded grace period.
        """
        if not self.adaptive:
            return self.silence_samples
        words = re.findall(r"[\w’']+", text.casefold())
        if len(words) >= 8 or self.active_samples >= self.rate * 3:
            return max(self.silence_samples, round(self.rate * 1.6))
        if words and not self.possible_fragment(text) and len(words) <= 6:
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

    def ready(self, partial: str, *, native_silence=None, now=None) -> bool:
        now = self.consumed / self.rate if now is None else now
        # Deadline is checked independently of a fresh endpoint candidate.
        if self.wait_expired(now):
            return True
        text = partial.strip()
        if text != self.partial:
            self.partial = text
            self.changed_at = self.consumed
        quiet = (self.processed - self.last_active if native_silence is None else native_silence)
        candidate = bool(
            text
            and self.active_samples >= self.rate * (.08 if native_silence is not None else .3)
            and quiet >= self.required_silence(text)
            and (native_silence is not None or self.consumed - self.changed_at >= self.rate * .5)
        )
        if not candidate:
            return False
        if self.adaptive and self.possible_fragment(text) and not self.fragment_merged:
            if not self.fragment_held:
                self.fragment_held = True
                self.hold_at = self.consumed
                self.hold_started = now
                self.hold_deadline = now + .4
            if self.hold_at is not None:
                self.continuation_samples = self.consumed - self.hold_at
                return self.wait_expired(now)
        return True
