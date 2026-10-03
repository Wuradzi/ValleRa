"""Compatibility policy for recognition evidence; no audio devices or execution.

Rules are extracted verbatim from the listener. A refinement hint is not an
execution permission. Pending clarification belongs to DialogueState.
"""
from __future__ import annotations

import math
import re
import time

from core.models import RecognitionResult
from core.action_proposal import ProposalState
from core.natural_turn import (
    direct_request, voice_request, short_clarification, application_name_reply,
)
from services.audio.endpoint import QuietEndpoint


class DialogueState:
    """Existing single pending clarification; approval state stays elsewhere."""

    def __init__(self, services=None):
        self.pending = None
        self.proposals = ProposalState()
        self.services = services if services is not None else {}

    def scoped_reply(self, text):
        pending = self.pending
        if not (pending and time.monotonic() < pending['expires']
                and pending.get('tool') in {'open_app', 'window_control'}
                and short_clarification(text) and not direct_request(voice_request(text), '')):
            return False
        name = application_name_reply(text)
        exact = getattr(self.services.get('apps'), 'has_exact_name', None)
        return bool(name and callable(exact) and exact(name))


class RecognitionPolicy:
    FAST_CHAT = re.compile(
        r"^(?:валера[\s,]+)?(?:привіт|здорово|дякую|дякую тобі|як справи|"
        r"як твої справи|так|ні|добре|гаразд|зрозуміло|до побачення)[.!?]*$",
        re.IGNORECASE,
    )

    def __init__(self, settings, dialogue=None):
        self.settings = settings
        self.dialogue = dialogue if dialogue is not None else DialogueState()

    @staticmethod
    def action_eligible(result):
        """Legacy evidence adapter, not approval to execute."""
        return not result.fragmented

    def finish_quality(self, result, *, fragmented, incomplete, unsafe_refinement):
        # Preserve the old compatibility flags and repeat-vs-chat choice.
        if unsafe_refinement:
            fragmented = True
            incomplete = incomplete or self._action_hint(result.text) or not (
                result.text.strip() and math.isfinite(result.confidence)
                and result.confidence >= self.settings.stt_chat_confidence_threshold)
        return fragmented, incomplete

    def _select_result(
        self,
        vosk_result: RecognitionResult,
        whisper_result: RecognitionResult,
    ) -> RecognitionResult:
        """Choose the accurate text without weakening the command boundary."""
        if not whisper_result.text:
            return vosk_result
        vosk_is_command = self._contains_command_prefix(vosk_result.text)
        whisper_is_command = self._contains_command_prefix(whisper_result.text)
        if vosk_is_command != whisper_is_command:
            # Never manufacture a local-command prefix from conflicting engines.
            # Keep the engine result that does not grant local execution. It can
            # still be handled as ordinary conversation or explicitly repeated.
            safe_result = whisper_result if not whisper_is_command else vosk_result
            return RecognitionResult(
                safe_result.text,
                min(vosk_result.confidence, whisper_result.confidence),
                "conflict",
            )
        if self._prefer_vosk(vosk_result):
            return vosk_result
        if (
            whisper_result.confidence < self.settings.stt_whisper_min_confidence
            and vosk_result.text
        ):
            return vosk_result
        return whisper_result

    @staticmethod
    def _contains_command_prefix(text: str) -> bool:
        normalized = " ".join(text.lower().strip().split())
        return bool(re.match(r"^команда\b", normalized))

    def _prefer_vosk(self, result: RecognitionResult) -> bool:
        # Vosk's threshold is local to this engine, not compared to Whisper's
        # probability. The policy is opt-in and never uses reference phrases.
        return (
            not getattr(self.settings, 'stt_selective_whisper_enabled', False)
            and
            getattr(self.settings, "stt_refinement_policy", "legacy") == "vosk_first"
            and result.engine == "vosk"
            and bool(result.text.strip())
            and math.isfinite(result.confidence)
            and 0.85 <= result.confidence <= 1.0
        )

    def _should_refine(
        self,
        result: RecognitionResult,
        pcm: bytes,
        sample_rate: int,
        has_speech_energy,
    ) -> tuple[bool, str]:
        if (
            self.settings.stt_whisper_skip_silence
            and not (getattr(self.settings, 'stt_selective_whisper_enabled', False) and result.text.strip())
            and not has_speech_energy(
                pcm,
                sample_rate,
                float(self.settings.noise_threshold),
            )
        ):
            return False, "silence"

        # Commands still require the existing second-engine prefix check.
        if getattr(self.settings, 'stt_selective_whisper_enabled', False):
            if result.fragmented or result.incomplete or result.capture_truncated or QuietEndpoint.possible_fragment(result.text):
                return True, 'incomplete'
            words = re.findall(r"[\w’']+", result.text.casefold())
            if (not words or not result.text.isprintable() or '\ufffd' in result.text
                    or re.search(r'\b(\w+)(?:\s+\1){2,}\b', result.text, re.I)):
                return True, 'suspicious_fragment'
            if not math.isfinite(result.confidence) or not self.settings.stt_vosk_chat_confidence <= result.confidence <= 1:
                return True, 'low_confidence'
            if self.dialogue.scoped_reply(result.text):
                return False, 'scoped_context'
            if self._action_hint(result.text):
                return True, 'action_uncertain'
            return False, 'high_confidence_chat'

        if self._prefer_vosk(result) and not self._contains_command_prefix(result.text):
            return False, "vosk-first"

        # Only an exact, harmless conversational phrase may skip refinement.
        # Complex utterances and every command still use Whisper when enabled.
        if (
            getattr(self.settings, "performance_profile", "balanced") == "fast"
            and result.confidence >= 0.92
            and self.FAST_CHAT.fullmatch(result.text.strip())
        ):
            return False, "fast-chat"

        return True, ""

    @classmethod
    def _action_hint(cls, text):
        return cls._contains_command_prefix(text) or direct_request(voice_request(text), '')
