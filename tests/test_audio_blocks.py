import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from config import ConfigError, ProjectPaths, Settings, apply_performance_profile, merge_config, validate_config
from core.listen import VoskListener
from services.audio.activity import SpeechEnergyGate
from services.audio.endpoint import QuietEndpoint
from testing.probes.endpoint import pause_offset, signal_end


class EnergyGateTests(unittest.TestCase):
    def test_observation_matches_original_quarter_second_gate_across_block_sizes(self):
        rng = np.random.default_rng(42)
        for rate in (16000, 22050, 48000):
            # Quiet audio, a short burst, then sustained speech.
            signal = np.concatenate([np.zeros(rate), rng.normal(0, 75, rate),
                                     np.full(rate, 512)]).astype(np.int16)
            pcm = signal.tobytes()
            for ms in (50, 100, 250):
                with self.subTest(rate=rate, block=ms):
                    gate = SpeechEnergyGate(rate, 0.005)
                    size = max(800, int(rate * ms / 1000)) * 2
                    observed = False
                    for offset in range(0, len(pcm), size):
                        consumed = pcm[:offset + size]
                        original_size = max(800, int(rate * 0.25))
                        floats = np.frombuffer(consumed, dtype=np.int16).astype(np.float32) / 32768
                        for start in range(0, len(floats) - original_size + 1, original_size):
                            observed |= float(np.sqrt(np.mean(np.square(floats[start:start + original_size])))) >= 0.005
                        self.assertEqual(gate.feed(pcm[offset:offset + size]), observed)

    def test_short_noise_burst_does_not_pass_due_to_smaller_callbacks(self):
        pcm = np.concatenate([np.full(320, 393), np.zeros(15680)]).astype(np.int16).tobytes()
        for ms in (50, 100, 250):
            gate = SpeechEnergyGate(16000, 0.005)
            block = ms * 16 * 2
            for offset in range(0, len(pcm), block):
                self.assertFalse(gate.feed(pcm[offset:offset + block]))


class AudioBlockTests(unittest.TestCase):
    def test_adaptive_configuration_is_explicit_boolean(self):
        self.assertIs(merge_config({})['stt']['endpoint_adaptive'], False)
        for value in (True, False):
            config = apply_performance_profile(merge_config({'stt': {'endpoint_adaptive': value}}), 'fast')
            validate_config(config)
            self.assertIs(config['stt']['endpoint_adaptive'], value)
        for value in (None, 0, 1, 'true'):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                validate_config(merge_config({'stt': {'endpoint_adaptive': value}}))

    def test_adaptive_short_chat_keeps_stability_and_quiet_audio_guards(self):
        for rate in (16000, 48000):
            endpoint = QuietEndpoint(rate, 1200, adaptive=True)
            endpoint.feed(b'\x00\x10' * (rate // 2))
            self.assertFalse(endpoint.ready('як твої справи'))
            endpoint.feed(b'\0\0' * round(rate * .8))
            self.assertFalse(endpoint.ready('як твої справи'))
            endpoint.feed(b'\0\0' * round(rate * .1))
            self.assertTrue(endpoint.ready('як твої справи'))
            self.assertFalse(endpoint.ready('а як твої справи'))
            endpoint.feed(b'\x00\x10' * (rate // 2))
            self.assertFalse(endpoint.ready('а як твої справи'))

    def test_adaptive_does_not_shorten_commands_unfinished_or_long_speech(self):
        for text in ('команда відкрий браузер', 'Валера команда відкрий браузер',
                     'я хотів запитати', 'розкажи мені про', 'я хочу',
                     'розкажи мені будь ласка як працює сучасний автомобіль'):
            with self.subTest(text=text):
                endpoint = QuietEndpoint(16000, 1200, adaptive=True)
                endpoint.feed(b'\x00\x10' * 8000)
                endpoint.ready(text)
                endpoint.feed(b'\0\0' * 16000)
                self.assertFalse(endpoint.ready(text))
                endpoint.feed(b'\0\0' * 3200)
                self.assertTrue(endpoint.ready(text))
        endpoint = QuietEndpoint(16000, 1200, adaptive=True)
        endpoint.feed(b'\x00\x10' * 80000)
        endpoint.ready('тиха повільна репліка')
        endpoint.feed(b'\0\0' * 16000)
        self.assertFalse(endpoint.ready('тиха повільна репліка'))

    def test_adaptive_listener_flushes_short_chat_without_executing_partial(self):
        from testing.probes.recorded import replay
        with tempfile.TemporaryDirectory() as directory:
            listener = VoskListener(Settings(ProjectPaths.from_root(Path(directory)),
                stt_endpoint_silence_ms=1200, stt_endpoint_adaptive=True))
            listener._get_model = Mock()
            listener.whisper.status = Mock(return_value=(True, 'ready'))
            recognizer = Mock()
            recognizer.AcceptWaveform.return_value = False
            recognizer.PartialResult.return_value = json.dumps({'partial': 'привіт'})
            recognizer.FinalResult.return_value = json.dumps({'text': 'привіт валера', 'result': []})
            pcm = b'\x00\x10' * 8000 + b'\0\0' * 32000
            with patch('vosk.KaldiRecognizer', return_value=recognizer), self.assertLogs('core.listen', level='DEBUG') as logs:
                result, captured = replay(listener, pcm, 16000)
            self.assertTrue(any('kind=quiet' in line for line in logs.output))
            self.assertEqual(result.text, 'привіт валера')
            self.assertEqual(captured, pcm[:48000])  # .5 s speech + 1 s pause (250 ms blocks)
            recognizer.FinalResult.assert_called_once()
            listener.close()

    def test_endpoint_configuration_is_explicit_and_bounded(self):
        self.assertEqual(merge_config({})["stt"]["endpoint_silence_ms"], 0)
        for ms in (0, 1200, 1400, 1600):
            config = apply_performance_profile(merge_config({"stt": {"endpoint_silence_ms": ms}}), "fast")
            validate_config(config)
            self.assertEqual(config["stt"]["endpoint_silence_ms"], ms)
        for value in (True, -1, 500, "1200", None, 1200.0):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                validate_config(merge_config({"stt": {"endpoint_silence_ms": value}}))

    def test_quiet_endpoint_uses_audio_time_and_never_triggers_on_silence_alone(self):
        endpoint = QuietEndpoint(16000, 1400)
        endpoint.feed(b"\0\0" * 160000)
        self.assertFalse(endpoint.ready(""))
        self.assertFalse(endpoint.ready("галюцинація"))
        endpoint.feed(b"\0\0" * 16000)
        self.assertFalse(endpoint.ready("галюцинація"))

    def test_quiet_endpoint_requires_stable_text_and_resets_on_resumed_speech(self):
        endpoint = QuietEndpoint(16000, 1400)
        voice = b"\x00\x10" * 8000
        endpoint.feed(voice)
        self.assertFalse(endpoint.ready("перша частина"))
        endpoint.feed(b"\0\0" * 16000)
        self.assertFalse(endpoint.ready("перша частина"))
        endpoint.feed(voice)
        self.assertFalse(endpoint.ready("перша частина друга частина"))
        endpoint.feed(b"\0\0" * 24000)
        self.assertTrue(endpoint.ready("перша частина друга частина"))
        self.assertFalse(endpoint.ready("змінена гіпотеза"))

    def test_quiet_endpoint_low_volume_and_block_boundaries(self):
        for rate in (16000, 22050, 48000):
            for ms in (50, 100, 250):
                with self.subTest(rate=rate, ms=ms):
                    endpoint = QuietEndpoint(rate, 1400)
                    # Quiet speech must keep capture open, independent of the
                    # louder calibrated gate used to reject initial noise.
                    pcm = b"\x21\0" * rate * 2
                    block = round(rate * ms / 1000) * 2
                    for start in range(0, len(pcm), block):
                        endpoint.feed(pcm[start:start + block])
                        self.assertFalse(endpoint.ready("тиха мова"))
                    endpoint.feed(b"\0\0" * round(rate * 1.5))
                    self.assertTrue(endpoint.ready("тиха мова"))

    def test_listener_quiet_endpoint_flushes_final_text_and_keeps_pcm(self):
        with tempfile.TemporaryDirectory() as directory:
            listener = VoskListener(Settings(ProjectPaths.from_root(Path(directory)), stt_endpoint_silence_ms=1400))
            listener._get_model = Mock()
            listener.whisper.status = Mock(return_value=(True, "ready"))
            recognizer = Mock()
            recognizer.AcceptWaveform.return_value = False
            recognizer.PartialResult.return_value = json.dumps({"partial": "незавершена гіпотеза"})
            recognizer.FinalResult.return_value = json.dumps({"text": "остаточна репліка", "result": []})
            chunks = [b"\x00\x10" * 4000] * 2 + [b"\0\0" * 4000] * 6

            def stream(**kwargs):
                for raw in chunks:
                    kwargs["callback"](raw, 4000, None, None)
                return contextlib.nullcontext()

            with patch("vosk.KaldiRecognizer", return_value=recognizer), patch(
                "core.listen.suppress_native_stderr", contextlib.nullcontext
            ):
                result, pcm = listener._listen_on_device(SimpleNamespace(RawInputStream=stream), 0, 16000, 8, None)
            self.assertEqual(result.text, "остаточна репліка")
            self.assertEqual(pcm, b"".join(chunks))
            recognizer.FinalResult.assert_called_once()
            recognizer.Result.assert_not_called()

    def test_confirmation_grammar_never_uses_quiet_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            listener = VoskListener(Settings(ProjectPaths.from_root(Path(directory)),
                stt_endpoint_silence_ms=1200, stt_endpoint_adaptive=True))
            listener._get_model = Mock()
            listener.whisper.status = Mock(return_value=(True, "ready"))
            recognizer = Mock()
            recognizer.AcceptWaveform.return_value = True
            recognizer.Result.return_value = json.dumps({"text": "так", "result": []})

            def stream(**kwargs):
                kwargs["callback"](b"\x00\x10" * 4000, 4000, None, None)
                return contextlib.nullcontext()

            with patch("vosk.KaldiRecognizer", return_value=recognizer), patch(
                "core.listen.suppress_native_stderr", contextlib.nullcontext
            ):
                result, _ = listener._listen_on_device(SimpleNamespace(RawInputStream=stream), 0, 16000, 8, ["так", "ні"])
            self.assertEqual(result.text, "так")
            recognizer.PartialResult.assert_not_called()
            recognizer.FinalResult.assert_not_called()

    def test_block_configuration_is_bounded_and_not_overwritten_by_profiles(self):
        for ms in (50, 100, 250):
            config = apply_performance_profile(merge_config({"stt": {"audio_block_ms": ms}}), "fast")
            validate_config(config)
            self.assertEqual(config["stt"]["audio_block_ms"], ms)
        for value in (0, 1, 75, 1000, True, 100.5, "100", None):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                validate_config(merge_config({"stt": {"audio_block_ms": value}}))

    def test_listener_uses_selected_block_and_preserves_all_input_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(ProjectPaths.from_root(Path(directory)), stt_audio_block_ms=100)
            listener = VoskListener(settings)
            listener._get_model = Mock()
            recognizer = Mock()
            recognizer.AcceptWaveform.side_effect = [False, False, True]
            recognizer.Result.return_value = json.dumps({"text": "привіт", "result": [{"word": "привіт", "conf": 1}]})
            raw = b"\x00\x10" * 1600

            def stream(**kwargs):
                self.assertEqual(kwargs["blocksize"], 1600)
                for _ in range(3):
                    kwargs["callback"](raw, 1600, None, None)
                return contextlib.nullcontext()

            with patch("vosk.KaldiRecognizer", return_value=recognizer), patch(
                "core.listen.suppress_native_stderr", contextlib.nullcontext
            ):
                result, pcm = listener._listen_on_device(SimpleNamespace(RawInputStream=stream), 0, 16000, 8, None)
            self.assertEqual(pcm, raw * 3)
            self.assertEqual(result.text, "привіт")
            self.assertEqual([call.args[0] for call in recognizer.AcceptWaveform.call_args_list], [raw] * 3)

    def test_synthetic_pause_boundary_and_signal_end(self):
        words = [{"start": 0, "end": .2}, {"start": .3, "end": .5}, {"start": .6, "end": .8}]
        self.assertEqual(pause_offset(words, 16000), 17600)
        self.assertAlmostEqual(signal_end(b"\x00\x10" * 800 + b"\0\0" * 800, 16000), .05)
        with self.assertRaises(ValueError):
            signal_end(b"\0\0" * 800, 16000)
