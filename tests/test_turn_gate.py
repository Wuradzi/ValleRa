"""TurnGate equals the inline formulas it replaced (audit 2026-10-29 #3, step 2).

The reference functions below are copied from core/processor.py before the
extraction and enumerate every combination of the trust flags.
"""
import itertools
import unittest
from dataclasses import replace

from core.action_policy import turn_permission
from core.models import TurnEnvelope
from core.turn_gate import TurnGate

FLAGS = ("action_eligible", "confidence_available", "clarification_required",
         "recognition_unreliable", "capture_truncated", "utterance_incomplete")


def legacy(turn, allow_actions, incomplete_input, stop_phrase, natural):
    caller = allow_actions
    if turn is not None:
        allow_actions = allow_actions and turn.action_eligible and turn.confidence_available
        incomplete_input = incomplete_input or turn.clarification_required
    reliable = not incomplete_input and (turn is None or not (
        turn.recognition_unreliable or turn.capture_truncated or turn.utterance_incomplete))
    permitted = caller and reliable and (allow_actions or (
        turn is not None and turn.stt_engine == "groq" and not turn.confidence_available))
    proposal = allow_actions and not incomplete_input and not (
        turn is not None and (turn.recognition_unreliable or turn.capture_truncated
                              or turn.utterance_incomplete))
    outcome = turn_permission(allow_actions, incomplete_input,
                              stop=stop_phrase and (turn is None or turn.confidence_available))
    groq = bool(caller and turn is not None and turn.source == "voice"
                and turn.stt_engine == "groq" and not turn.confidence_available
                and not turn.recognition_unreliable and not turn.capture_truncated
                and not turn.utterance_incomplete and not incomplete_input and natural)
    return permitted, proposal, outcome, groq, incomplete_input


def modern(turn, allow_actions, incomplete_input, stop_phrase, natural):
    gate = TurnGate.evaluate(turn, allow_actions, incomplete_input)
    return (gate.explicit_local_request, gate.proposal_quality,
            gate.outcome(stop_phrase=stop_phrase), gate.groq_candidate(natural), gate.incomplete)


class TurnGateEquivalenceTests(unittest.TestCase):
    def test_all_flag_combinations_match_previous_inline_logic(self):
        base = TurnEnvelope(turn_id="t", session_id="s", source="voice", text="x",
                            transcript="x", stt_engine="vosk", confidence=0.9)
        checked = 0
        for engine, source in itertools.product(("vosk", "groq", "faster-whisper"), ("voice", "text")):
            for values in itertools.product((False, True), repeat=len(FLAGS)):
                turn = replace(base, stt_engine=engine, source=source, **dict(zip(FLAGS, values)))
                for extra in itertools.product((False, True), repeat=4):
                    with self.subTest(engine=engine, source=source, flags=values, extra=extra):
                        self.assertEqual(modern(turn, *extra), legacy(turn, *extra))
                    checked += 1
        for extra in itertools.product((False, True), repeat=4):
            self.assertEqual(modern(None, *extra), legacy(None, *extra))
        self.assertEqual(checked, 6 * 64 * 16)

    def test_unscored_groq_is_never_action_eligible(self):
        turn = TurnEnvelope(turn_id="t", session_id="s", source="voice", text="x", transcript="x",
                            stt_engine="groq", confidence=0.0, confidence_available=False)
        gate = TurnGate.evaluate(turn, True, False)
        self.assertFalse(gate.allow_actions)
        self.assertFalse(gate.proposal_quality)
        self.assertTrue(gate.explicit_local_request)


if __name__ == "__main__":
    unittest.main()
