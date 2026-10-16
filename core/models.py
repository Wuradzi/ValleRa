from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from core.performance import TurnTiming
from core.execution_result import ExecutionResult


ConfirmationCallback = Callable[[str], Awaitable[bool]]


@dataclass(slots=True)
class RecognitionResult:
    text: str
    confidence: float
    engine: str = "vosk"
    timing: TurnTiming | None = field(default=None, repr=False, compare=False)
    fragmented: bool = False
    incomplete: bool = False
    capture_truncated: bool = False
    # Explicit provenance; None supports older direct RecognitionResult fixtures.
    utterance_incomplete: bool | None = None
    recognition_unreliable: bool = False
    # False means unscored text, not a measured score of zero. Never authorizes actions.
    confidence_available: bool = True

    @property
    def resolved_utterance_incomplete(self) -> bool:
        """Explicit linguistic evidence wins; None retains the legacy fallback.

        `incomplete` also controls repeat-vs-chat, while `fragmented` is a legacy
        action restriction. Neither is interchangeable with recognition failure.
        """
        return self.incomplete if self.utterance_incomplete is None else self.utterance_incomplete


@dataclass(frozen=True, slots=True, kw_only=True)
class TurnEnvelope:
    """One immutable dispatch payload, not a store for runtime service state.

    action_eligible preserves the STT permission to enter existing safety checks;
    it is never authorization to execute a tool. clarification_required preserves
    the old repeat-vs-tool-free-chat choice independently of linguistic completion.
    """
    turn_id: str
    session_id: str
    source: str
    text: str
    transcript: str
    stt_engine: str
    confidence: float
    utterance_incomplete: bool = False
    capture_truncated: bool = False
    recognition_unreliable: bool = False
    action_eligible: bool = True
    clarification_required: bool = False
    timing: TurnTiming | None = field(default=None, repr=False, compare=False)

    confidence_available: bool = True

    @property
    def fragmented(self) -> bool:
        """Temporary compatibility view, not the reason for restricted execution."""
        return not self.action_eligible


@dataclass(slots=True)
class SkillResult:
    handled: bool
    response: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def execution(self) -> ExecutionResult:
        """Compatibility view; preserve legacy data and user-facing response."""
        return ExecutionResult.from_data(self.data)


@dataclass(slots=True)
class CommandContext:
    settings: Any
    services: dict[str, Any]
    confirm: ConfirmationCallback
    source: str = "voice"
    raw_text: str = ""
    normalized_text: str = ""
    turn_id: str | None = None
