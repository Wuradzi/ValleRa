"""Bounded phrase buffer for incremental TTS (not one WAV per full answer)."""

from __future__ import annotations

import re


class SpeechBuffer:
    def __init__(self, max_chars: int = 240):
        self.pending = ""
        self.max_chars = max_chars

    def feed(self, text: str, *, final: bool = False) -> list[str]:
        self.pending += text
        ready = []
        while self.pending:
            boundary = re.search(r"[.!?][\"»)]*\s+|\n+", self.pending)
            end = boundary.end() if boundary else 0
            if not end or end > self.max_chars:
                if len(self.pending) > self.max_chars:
                    end = self.pending.rfind(" ", 0, self.max_chars)
                    if end <= 0:
                        end = self.max_chars
                elif final:
                    end = len(self.pending)
                else:
                    break
            chunk = self.pending[:end].strip()
            self.pending = self.pending[end:].lstrip()
            if chunk:
                ready.append(chunk)
        return ready
