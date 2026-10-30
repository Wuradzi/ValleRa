"""Capability fixtures for platform-neutral offline tests (audit 2026-10-29 #4).

Each helper states what is simulated. None of them performs real audio, speech,
window or process operations: tests that use them already mock those calls.
"""
from __future__ import annotations

import contextlib
import sys
import unittest
from importlib.util import find_spec
from unittest.mock import MagicMock, patch

from services.platform.resolver import PlatformServices

# Speaker logic (queue, stop, generation, worker protocol) is gated by
# platform.supports('tts'), which is Windows-only today. Tests mock synthesis
# and playback, so a TTS-capable platform value exercises the same logic here.
TTS_CAPABLE_PLATFORM = PlatformServices("Windows", "AMD64")

WINDOWS_ONLY = unittest.skipUnless(
    sys.platform == "win32", "функція реалізована лише для Windows (manifest platforms)")


def _real_sounddevice_available() -> bool:
    try:
        import sounddevice  # noqa: F401
    except (ImportError, OSError):
        return False
    return True


def sounddevice_or_stub():
    """Context-manager form of hermetic_sounddevice, e.g. self.enterContext(...)."""
    if _real_sounddevice_available():
        return contextlib.nullcontext()
    return patch.dict(sys.modules, {"sounddevice": MagicMock(
        name="sounddevice (stub: PortAudio недоступний)")})


def hermetic_sounddevice(test):
    """Capture tests mock the stream; only the module import needs PortAudio.

    With a working PortAudio the real module is used unchanged; otherwise a
    stub is placed in sys.modules for this test only.
    """
    if _real_sounddevice_available():
        return test
    stub = MagicMock(name="sounddevice (stub: PortAudio недоступний)")
    return patch.dict(sys.modules, {"sounddevice": stub})(test)


def with_tts_capable_platform(speaker):
    """Return the speaker with a TTS-capable platform; no real synthesis occurs."""
    speaker.platform = TTS_CAPABLE_PLATFORM
    return speaker


def windows_shell_platform():
    """Patch target value for code that branches on resolve_platform().os."""
    return lambda: TTS_CAPABLE_PLATFORM


def requires_package(name: str):
    return unittest.skipUnless(find_spec(name) is not None,
                               f"опційний пакет {name} не встановлено")
