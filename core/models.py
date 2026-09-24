from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from core.performance import TurnTiming


ConfirmationCallback = Callable[[str], Awaitable[bool]]


@dataclass(slots=True)
class RecognitionResult:
    text: str
    confidence: float
    engine: str = "vosk"
    timing: TurnTiming | None = field(default=None, repr=False, compare=False)


@dataclass(slots=True)
class SkillResult:
    handled: bool
    response: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CommandContext:
    settings: Any
    services: dict[str, Any]
    confirm: ConfirmationCallback
    source: str = "voice"
    raw_text: str = ""
    normalized_text: str = ""
