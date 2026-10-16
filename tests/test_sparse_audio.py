import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from core.stt_listener import SpeechListener
from services.audio.pcm_capture import CapturedAudio, sparse_activity


class SparseAudioTests(unittest.TestCase):
    def capture(self, active=None, span=None, interrupted=False):
        return CapturedAudio(bytes(3200), 16000, False, interrupted, 20, 18,
                             active_audio_ms=active, speech_span_ms=span)

    def test_long_sparse_capture_is_rejected(self):
        self.assertTrue(sparse_activity(self.capture(360, 10920)))
        self.assertTrue(sparse_activity(self.capture(300, 3000)))
        self.assertTrue(sparse_activity(self.capture(500, 5000)))

    def test_short_replies_and_real_speech_are_preserved(self):
        for active, span in ((300, 300), (340, 640), (500, 3000),
                             (501, 10000), (3000, 10000), (250, 2999),
                             (None, None), (None, 10000), (float('nan'), 10000)):
            with self.subTest(active=active, span=span):
                self.assertFalse(sparse_activity(self.capture(active, span)))

    def test_rejected_capture_never_submits_backend_job(self):
        listener = SpeechListener.__new__(SpeechListener)
        listener.profile = SimpleNamespace(backend='groq')
        listener._cancelled = Mock(return_value=False)
        listener._refinement_wait = Mock()
        result = listener._recognize_capture(self.capture(360, 10920))
        listener._refinement_wait.run.assert_not_called()
        self.assertEqual(result.text, '')
        self.assertTrue(result.recognition_unreliable)
        self.assertTrue(result.fragmented)
        self.assertFalse(result.confidence_available)

    def test_cancellation_wins_over_sparse_rejection(self):
        listener = SpeechListener.__new__(SpeechListener)
        listener._cancelled = Mock(return_value=True)
        self.assertEqual(listener._recognize_capture(self.capture(360, 10920)).engine, 'interrupted')
