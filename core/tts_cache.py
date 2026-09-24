from __future__ import annotations

import hashlib
from pathlib import Path


class TTSCache:
    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, text: str, voice: str, rate: int) -> Path:
        digest = hashlib.md5(
            f"{voice}|{rate}|{text}".encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()
        return self.directory / f"{digest}.wav"
