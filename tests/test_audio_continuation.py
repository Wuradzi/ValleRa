"""Synthetic PCM + fake Vosk; no models, microphone, API or real actions."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

from config import ProjectPaths, Settings
from core.listen import VoskListener
from core.models import RecognitionResult
from services.audio.endpoint import QuietEndpoint
from testing.probes.recorded import replay


class ContinuationTests(unittest.TestCase):
    def test_short_names_replies_and_complete_commands_are_not_fragments(self):
        for text in ('Telegram', 'інше вікно', 'так', 'ні', 'не треба', 'а чому ні',
                     'розгорни вікно браузера', 'що ти там', 'я хочу це'):
            with self.subTest(text=text):
                endpoint = self.endpoint()
                self.assertFalse(endpoint.possible_fragment(text))
                endpoint.ready(text)
                endpoint.feed(b'\0\0' * 16000)
                self.assertTrue(endpoint.ready(text))
                self.assertFalse(endpoint.fragment_held)

    def test_deadline_survives_lost_endpoint_condition_and_no_new_audio(self):
        endpoint = self.endpoint()
        endpoint.ready('я думав про', now=10)
        endpoint.feed(b'\0\0' * 19200)
        self.assertFalse(endpoint.ready('я думав про', now=11.2))
        # No new PCM, unstable/empty hypothesis: deadline still fires.
        self.assertFalse(endpoint.wait_expired(11.59))
        self.assertTrue(endpoint.ready('', now=11.61))
        self.assertAlmostEqual(endpoint.wait_actual_ms, 410)

    def test_refinement_reassesses_final_selected_text_not_stale_vosk_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            listener = VoskListener(Settings(ProjectPaths.from_root(Path(directory)), stt_endpoint_adaptive=True))
            listener._input_candidates = Mock(return_value=[(0, 16000)])
            listener._should_refine = Mock(return_value=(True, 'test'))
            for text, incomplete in (('Цілком завершена відповідь.', False),
                                     ('розгорни вікно браузера.', False), ('відкрий браузер', False),
                                     ('я хотів', True)):
                with self.subTest(text=text):
                    listener._listen_on_device = Mock(return_value=(
                        RecognitionResult('я хотів', .6, fragmented=True, incomplete=True), b'pcm'))
                    listener.whisper = SimpleNamespace(enabled=True, transcribe=Mock(
                        return_value=RecognitionResult(text, .9, 'whisper')))
                    result = listener.listen_once()
                    self.assertEqual(result.text, text)
                    self.assertEqual(result.fragmented, incomplete)
                    self.assertEqual(result.incomplete, incomplete)

    def endpoint(self, speech=.5):
        result = QuietEndpoint(16000, 1200, adaptive=True)
        result.feed(b'\x00\x10' * round(16000 * speech))
        return result

    def test_completed_short_chat_actions_yes_no_and_stop_have_no_extra_window(self):
        for text in ('так', 'ні', 'дякую', 'відкрий Telegram', 'закрий браузер',
                     'стоп', 'скасувати', 'Команда скасувати', 'Команда замовкни'):
            with self.subTest(text=text):
                endpoint = self.endpoint()
                endpoint.ready(text)
                endpoint.feed(b'\0\0' * 14080)  # 880 ms, below boundary
                self.assertFalse(endpoint.ready(text))
                endpoint.feed(b'\0\0' * 320)
                self.assertTrue(endpoint.ready(text))
                self.assertFalse(endpoint.fragment_held)

    def test_long_speech_survives_multiple_internal_pauses(self):
        endpoint = self.endpoint(3.5)
        for text in ('я згадував нашу розмову', 'я згадував нашу розмову про роботу'):
            endpoint.ready(text)
            endpoint.feed(b'\0\0' * 19200)
            self.assertFalse(endpoint.ready(text))
            endpoint.feed(b'\x00\x10' * 8000)
        endpoint.ready('це була довга розмова і ми домовились зустрітись завтра')
        endpoint.feed(b'\0\0' * 25600)
        self.assertTrue(endpoint.ready('це була довга розмова і ми домовились зустрітись завтра'))

    def test_unfinished_tail_has_bounded_400ms_window(self):
        endpoint = self.endpoint()
        endpoint.ready('я думав про')
        endpoint.feed(b'\0\0' * 19200)
        self.assertFalse(endpoint.ready('я думав про'))
        self.assertTrue(endpoint.fragment_held)
        endpoint.feed(b'\0\0' * 6080)
        self.assertFalse(endpoint.ready('я думав про'))
        endpoint.feed(b'\0\0' * 320)
        self.assertTrue(endpoint.ready('я думав про'))
        self.assertAlmostEqual(endpoint.wait_actual_ms, 400)

    def test_silence_and_empty_partial_never_create_turn(self):
        endpoint = QuietEndpoint(16000, 1200, adaptive=True)
        endpoint.feed(b'\0\0' * 160000)
        self.assertFalse(endpoint.ready(''))
        self.assertFalse(endpoint.ready('шум', native_silence=160000))

    def run_capture(self, pcm, finals, partial, tail='', interrupt_at=None):
        with tempfile.TemporaryDirectory() as directory:
            listener = VoskListener(Settings(ProjectPaths.from_root(Path(directory)),
                stt_endpoint_silence_ms=1200, stt_endpoint_adaptive=True))
            listener._get_model = Mock()
            listener.whisper.enabled = False
            counter = [0]
            def accept(data):
                counter[0] += 1
                if counter[0] == interrupt_at:
                    listener.interrupt()
                return counter[0] in finals
            recognizer = Mock()
            recognizer.AcceptWaveform.side_effect = accept
            recognizer.Result.side_effect = lambda: json.dumps(finals[counter[0]], ensure_ascii=False)
            recognizer.PartialResult.side_effect = lambda: json.dumps({'partial': partial(counter[0])})
            recognizer.FinalResult.return_value = json.dumps({'text': tail, 'result': []})
            with patch('vosk.KaldiRecognizer', return_value=recognizer):
                result, captured = replay(listener, pcm, 16000)
            listener.close()
            return result, captured

    @staticmethod
    def payload(text, end):
        return {'text': text, 'result': [{'word': text, 'end': end, 'conf': .96}]}

    def test_native_complete_and_fragment_dispatch_use_same_thresholds(self):
        pcm = b'\x00\x10' * 8000 + b'\0\0' * 48000
        for text, maximum, guarded in (('так', 1.5, False), ('ні', 1.5, False),
                                      ('відкрий Telegram', 1.5, False), ('інша думка', 1.5, False),
                                      ('я хотів', 2.25, True)):
            with self.subTest(text=text):
                result, captured = self.run_capture(pcm, {5: self.payload(text, .5)}, lambda _: '')
                self.assertEqual(result.text, text)
                self.assertEqual(result.fragmented, guarded)
                self.assertLessEqual(len(captured) / 32000, maximum)

    def test_very_short_native_yes_does_not_wait_for_capture_timeout(self):
        pcm = b'\x00\x10' * 4000 + b'\0\0' * 48000
        result, captured = self.run_capture(pcm, {5: self.payload('так', .25)}, lambda _: '')
        self.assertEqual(result.text, 'так')
        self.assertFalse(result.fragmented)
        self.assertLessEqual(len(captured) / 32000, 1.25)

    def test_final_text_not_partial_determines_action_eligibility(self):
        pcm = b'\x00\x10' * 8000 + b'\0\0' * 48000
        result, _ = self.run_capture(pcm, {}, lambda _: 'відкрий браузер', tail='відкрий')
        self.assertEqual(result.text, 'відкрий')
        self.assertTrue(result.fragmented)
        self.assertTrue(result.incomplete)

    def test_native_fragment_merges_literal_continuation_and_negation(self):
        pcm = (b'\x00\x10' * 8000 + b'\0\0' * 20000 +
               b'\x00\x10' * 12000 + b'\0\0' * 48000)
        result, captured = self.run_capture(pcm,
            {5: self.payload('я хотів', .5), 14: self.payload('не відкривай Telegram', 2.5)},
            lambda n: 'не відкривай Telegram' if 8 <= n < 14 else '')
        self.assertEqual(result.text, 'я хотів не відкривай Telegram')
        self.assertFalse(result.fragmented)
        self.assertGreater(len(captured), 32000 * 2.5)

    def test_quiet_fragment_merges_without_rewriting_final_text(self):
        pcm = (b'\x00\x10' * 8000 + b'\0\0' * 20000 +
               b'\x00\x10' * 12000 + b'\0\0' * 48000)
        result, _ = self.run_capture(pcm, {},
            lambda n: 'інша думка про' if n < 8 else 'інша думка про нашу розмову',
            tail='інша думка про нашу розмову')
        self.assertEqual(result.text, 'інша думка про нашу розмову')
        self.assertFalse(result.fragmented)

    def test_long_native_pause_waits_for_continuation(self):
        pcm = (b'\x00\x10' * 56000 + b'\0\0' * 24000 +
               b'\x00\x10' * 8000 + b'\0\0' * 40000)
        result, _ = self.run_capture(pcm,
            {18: self.payload('я довго думав про нашу розмову', 3.5),
             26: self.payload('але вирішив зачекати', 5.5)},
            lambda n: 'але вирішив зачекати' if 22 <= n < 26 else '')
        self.assertEqual(result.text, 'я довго думав про нашу розмову але вирішив зачекати')
        self.assertFalse(result.fragmented)

    def test_interrupt_at_final_endpoint_wins_over_dispatch(self):
        pcm = b'\x00\x10' * 8000 + b'\0\0' * 48000
        result, _ = self.run_capture(pcm, {6: self.payload('так', .5)}, lambda _: '', interrupt_at=6)
        self.assertEqual(result.engine, 'interrupted')
        self.assertEqual(result.text, '')

    def test_interrupt_discards_pending_fragment_and_pcm_is_not_reused(self):
        pcm = b'\x00\x10' * 8000 + b'\0\0' * 48000
        result, _ = self.run_capture(pcm, {5: self.payload('я хотів', .5)}, lambda _: '', interrupt_at=8)
        self.assertEqual(result.engine, 'interrupted')
        self.assertEqual(result.text, '')

    def test_capture_deadline_does_not_dispatch_unfinished_action(self):
        pcm = b'\x00\x10' * (16000 * 16)
        result, _ = self.run_capture(pcm, {}, lambda _: 'відкрий', tail='відкрий')
        self.assertEqual(result.text, 'відкрий')
        self.assertTrue(result.fragmented)
