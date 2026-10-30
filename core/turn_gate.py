"""One place that turns STT trust flags into routing permissions.

Read-only view of a TurnEnvelope plus the caller's flags. ALLOW / permitted
here means only "may enter the existing executor or confirmation path";
it is never authorization to run a tool (see core.action_policy).
"""
from __future__ import annotations

from dataclasses import dataclass

from core.action_policy import PolicyOutcome, turn_permission
from core.models import TurnEnvelope


@dataclass(frozen=True, slots=True)
class TurnGate:
    caller_allows: bool
    """The caller (app loop, probe, test) did not forbid actions for this turn."""
    allow_actions: bool
    """Caller allows, STT marked the turn eligible and a confidence score exists."""
    incomplete: bool
    """Caller flag or the envelope asks for a repeat instead of a tool."""
    reliable: bool
    """Not incomplete, not truncated, not unreliable, not linguistically cut off."""
    confidence_known: bool
    groq_unscored: bool
    """Groq text has no confidence score; never treated as a measured score."""
    voice: bool

    @classmethod
    def evaluate(cls, turn: TurnEnvelope | None, allow_actions: bool,
                 incomplete_input: bool) -> "TurnGate":
        if turn is None:
            # Direct string callers (probes/tests) keep their existing API.
            return cls(allow_actions, allow_actions, incomplete_input, not incomplete_input,
                       True, False, False)
        incomplete = incomplete_input or turn.clarification_required
        damaged = turn.recognition_unreliable or turn.capture_truncated or turn.utterance_incomplete
        return cls(
            caller_allows=allow_actions,
            allow_actions=allow_actions and turn.action_eligible and turn.confidence_available,
            incomplete=incomplete,
            reliable=not incomplete and not damaged,
            confidence_known=turn.confidence_available,
            groq_unscored=turn.stt_engine == "groq" and not turn.confidence_available,
            voice=turn.source == "voice",
        )

    @property
    def explicit_local_request(self) -> bool:
        """Document/memory requests: reliable text, scored or explicitly unscored Groq."""
        return self.caller_allows and self.reliable and (self.allow_actions or self.groq_unscored)

    @property
    def proposal_quality(self) -> bool:
        return self.allow_actions and self.reliable

    def outcome(self, *, stop_phrase: bool) -> PolicyOutcome:
        """Pre-routing gate; a stop phrase passes only with a known confidence."""
        return turn_permission(self.allow_actions, self.incomplete,
                               stop=stop_phrase and self.confidence_known)

    def groq_candidate(self, natural_actions_enabled: bool) -> bool:
        """Separate unscored-Groq proposal path; still needs policy and confirmation."""
        return (self.caller_allows and self.voice and self.groq_unscored and self.reliable
                and natural_actions_enabled)
