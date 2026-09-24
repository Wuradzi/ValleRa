from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

from core.speak import Speaker


class SpeakerWaveValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "voice.wav"

    def test_header_only_wav_is_rejected(self) -> None:
        self._write_wav(frames=b"")

        self.assertFalse(Speaker._is_valid_wav(self.path))

    def test_wav_with_audio_frames_is_accepted(self) -> None:
        self._write_wav(frames=b"\x00\x00" * 2000)

        self.assertTrue(Speaker._is_valid_wav(self.path))

    def _write_wav(self, frames: bytes) -> None:
        with wave.open(str(self.path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(frames)

