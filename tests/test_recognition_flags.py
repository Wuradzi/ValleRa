import itertools
import unittest

from core.models import RecognitionResult, TurnEnvelope
from core.recognition_policy import RecognitionPolicy


class RecognitionFlagContracts(unittest.TestCase):
    def test_projection_preserves_all_legacy_combinations(self):
        for values in itertools.product((False, True), repeat=5):
            fragmented, incomplete, truncated, unreliable, scored = values
            for explicit in (None, False, True):
                with self.subTest(values=values, explicit=explicit):
                    result = RecognitionResult('fixture', .9, fragmented=fragmented,
                        incomplete=incomplete, capture_truncated=truncated,
                        recognition_unreliable=unreliable, confidence_available=scored,
                        utterance_incomplete=explicit)
                    flags = RecognitionPolicy.turn_flags(result)
                    self.assertEqual(flags, {
                        'confidence_available': scored,
                        'utterance_incomplete': incomplete if explicit is None else explicit,
                        'capture_truncated': truncated,
                        'recognition_unreliable': unreliable,
                        'action_eligible': scored and not fragmented,
                        'clarification_required': incomplete,
                    })
                    turn = TurnEnvelope(turn_id='fixture', session_id='test', source='voice',
                        text=result.text, transcript=result.text, stt_engine=result.engine,
                        confidence=result.confidence, **flags)
                    self.assertEqual(turn.fragmented, not flags['action_eligible'])

    def test_unscored_complete_chat_is_not_linguistically_incomplete(self):
        result = RecognitionResult('fixture', 0, engine='groq', fragmented=True,
                                   confidence_available=False, utterance_incomplete=False)
        flags = RecognitionPolicy.turn_flags(result)
        self.assertFalse(flags['action_eligible'])
        self.assertFalse(flags['clarification_required'])
        self.assertFalse(flags['utterance_incomplete'])
        self.assertFalse(flags['recognition_unreliable'])

    def test_projection_does_not_mutate_recognition(self):
        result = RecognitionResult('fixture', .9, incomplete=True, utterance_incomplete=False)
        flags = RecognitionPolicy.turn_flags(result)
        flags['utterance_incomplete'] = True
        self.assertFalse(result.utterance_incomplete)
        self.assertTrue(result.incomplete)
