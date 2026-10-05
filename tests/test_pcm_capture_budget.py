"""Deterministic microphone/clock simulation; no real microphone or ASR."""
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import queue

import numpy as np

from config import Settings, ProjectPaths
from services.audio.pcm_capture import capture_pcm


class CaptureBudgetTests(TestCase):
    def capture(self, frames, cancel_at=None, overflow=False):
        clock = [0.0]
        source = iter(frames)
        rate = 16000

        class Blocks:
            def get(self, timeout):
                clock[0] += .1
                amplitude = next(source, 0)
                return np.full(1600, amplitude, dtype=np.int16).tobytes(), clock[0]

            def empty(self):
                return True

            def put_nowait(self, value):
                pass

        def stream(**kwargs):
            if overflow:
                kwargs['callback'](b'', 0, None, True)
            return nullcontext()

        settings = Settings(ProjectPaths.from_root(Path.cwd()))
        with patch('services.audio.pcm_capture.queue.Queue', return_value=Blocks()), \
             patch('services.audio.pcm_capture.time.monotonic', side_effect=lambda: clock[0]), \
             patch('services.audio.pcm_capture.time.perf_counter', side_effect=lambda: clock[0]):
            result = capture_pcm(SimpleNamespace(RawInputStream=stream), None, rate, 15, settings,
                                 lambda: cancel_at is not None and clock[0] >= cancel_at, nullcontext)
        return result, clock[0]

    def test_wait_for_onset_does_not_spend_speech_budget(self):
        result, elapsed = self.capture([0] * 120 + [3000] * 40 + [0] * 20)
        self.assertFalse(result.truncated)
        self.assertGreater(elapsed, 15)
        self.assertLess(elapsed, 19)
        self.assertLess(len(result.pcm) / 32000, 6)
        self.assertEqual(result.pcm[:9600], bytes(9600))  # 300ms pre-roll
        self.assertAlmostEqual(result.speech_started_at, 12)

    def test_speech_limit_still_truncates(self):
        result, elapsed = self.capture([0] * 100 + [3000] * 300)
        self.assertTrue(result.truncated)
        self.assertEqual(result.endpoint_reason, 'capture_limit')
        self.assertLess(elapsed, 25.2)

    def test_silence_is_bounded_and_empty(self):
        result, elapsed = self.capture([0] * 400)
        self.assertEqual(result.pcm, b'')
        self.assertLess(elapsed, 15.2)

    def test_short_turn_has_no_new_endpoint_delay(self):
        result, elapsed = self.capture([3000] * 5 + [0] * 20)
        self.assertFalse(result.truncated)
        self.assertLess(elapsed, 1.4)

    def test_cancel_and_overflow_preserve_safety(self):
        result, elapsed = self.capture([3000] * 100, cancel_at=1)
        self.assertTrue(result.interrupted)
        self.assertTrue(result.truncated)
        self.assertLess(elapsed, 1.2)
        result, _ = self.capture([3000] * 5 + [0] * 20, overflow=True)
        self.assertTrue(result.truncated)

    def test_device_stops_delivering_frames_timeout_still_works(self):
        # queue.Empty must not reset either deadline.
        settings = Settings(ProjectPaths.from_root(Path.cwd()))
        clock = iter([0, 0, 16])
        with patch('services.audio.pcm_capture.queue.Queue') as factory, \
             patch('services.audio.pcm_capture.time.monotonic', side_effect=lambda: next(clock)):
            factory.return_value.get.side_effect = queue.Empty
            result = capture_pcm(SimpleNamespace(RawInputStream=lambda **kw: nullcontext()),
                                 None, 16000, 15, settings, lambda: False, nullcontext)
        self.assertEqual(result.pcm, b'')
