"""Reference text corpus of local decisions (audit #7); see testing/phrase_corpus.json."""
import json
import unittest
from pathlib import Path

from core.action_proposal import needs_proposal
from core.confirmation import ConfirmationService
from core.natural_turn import (application_name_reply, direct_request, request_scope,
                               short_clarification, voice_request, window_request)
from core.processor import COMMAND_PREFIX, DIALOGUE_CONTROLS, STOP_SPEECH, CommandProcessor
from skills.memory.personal import parse_request

CORPUS = Path(__file__).resolve().parents[1] / "testing" / "phrase_corpus.json"
REQUIRED_CATEGORIES = {"stop", "dialogue_control", "confirmation", "memory", "action_request",
                       "window", "non_action", "bare_reference", "scoped_reply", "command_mode"}
DECISION = {True: "yes", False: "no", None: "unknown"}


def _request(phrase):
    text = phrase["text"]
    return voice_request(text) if phrase["source"] == "voice" else text


def _window(phrase):
    window = window_request(_request(phrase))
    return list(window) if window is not None else None


def _memory(phrase):
    parsed = parse_request(phrase["text"])
    return list(parsed) if parsed is not None else None


FIELDS = {
    "command_prefix": lambda p: bool(COMMAND_PREFIX.match(p["text"])),
    "stop": lambda p: bool(STOP_SPEECH.fullmatch(p["text"])),
    "control": lambda p: DIALOGUE_CONTROLS.get(CommandProcessor._normalize(p["text"]).rstrip(".!?")),
    "direct": lambda p: direct_request(_request(p), ""),
    "scope": lambda p: request_scope(_request(p)),
    "window": _window,
    "needs_proposal": lambda p: needs_proposal(p["text"]),
    "short_clarification": lambda p: bool(short_clarification(p["text"])),
    "app_name": lambda p: application_name_reply(p["text"]),
    "confirm": lambda p: DECISION[ConfirmationService._decision(p["text"])],
    "confirm_voice_low": lambda p: DECISION[ConfirmationService._decision(p["text"], 0.7)],
    "memory": _memory,
}


class PhraseCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
        cls.phrases = cls.corpus["phrases"]

    def test_corpus_is_well_formed(self):
        ids = [phrase["id"] for phrase in self.phrases]
        self.assertEqual(len(ids), len(set(ids)), "duplicate phrase id")
        self.assertEqual(set(self.corpus["fields"]), set(FIELDS))
        self.assertEqual({phrase["category"] for phrase in self.phrases}, REQUIRED_CATEGORIES)
        for phrase in self.phrases:
            with self.subTest(phrase=phrase["id"]):
                self.assertIn(phrase["source"], {"voice", "text"})
                self.assertTrue(phrase["expect"])
                self.assertLessEqual(set(phrase["expect"]), set(FIELDS))

    def test_local_decisions_match_reference(self):
        for phrase in self.phrases:
            for field, expected in phrase["expect"].items():
                with self.subTest(phrase=phrase["id"], field=field):
                    self.assertEqual(FIELDS[field](phrase), expected, phrase["text"])

    def test_non_actions_never_pass_direct_gate(self):
        # Safety floor independent of individual expectations.
        for phrase in self.phrases:
            if phrase["category"] in {"non_action", "confirmation"} or phrase["expect"].get("needs_proposal"):
                with self.subTest(phrase=phrase["id"]):
                    self.assertFalse(direct_request(_request(phrase), ""), phrase["text"])


if __name__ == "__main__":
    unittest.main()
