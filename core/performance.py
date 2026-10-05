"""Local timing only: no prompts, audio, credentials, or exception bodies."""

from __future__ import annotations

import logging
import time
import threading
from collections import deque
import statistics
import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import uuid4

logger = logging.getLogger(__name__)
CURRENT_TURN = ContextVar("performance_turn", default=None)


class TurnTiming:
    """One input's software latency; explicitly carried across worker queues."""

    def __init__(self, recorder, *, endpoint_at=None, speech_end_at=None):
        self.recorder = recorder
        self.turn_id = uuid4().hex[:12]
        self.endpoint_at = time.perf_counter() if endpoint_at is None else endpoint_at
        self.speech_end_at = speech_end_at
        self._seen = set()
        self._lock = threading.Lock()
        self.values = {}
        self.voice = endpoint_at is not None
        recorder.record("turn.origin_voice" if endpoint_at is not None else "turn.origin_dispatch", 0,
                        turn_id=self.turn_id)
        if speech_end_at is not None:
            self.values['endpoint'] = (self.endpoint_at - speech_end_at) * 1000
            self.recorder.record("turn.endpoint_wait_estimate", (self.endpoint_at - speech_end_at) * 1000,
                                 turn_id=self.turn_id)

    def mark(self, milestone, *, at=None):
        if not self.recorder.enabled:
            return
        with self._lock:
            if milestone in self._seen:
                return
            self._seen.add(milestone)
        now = time.perf_counter() if at is None else at
        self.values[milestone] = (now - self.endpoint_at) * 1000
        self.recorder.record("turn.endpoint_to_" + milestone, (now - self.endpoint_at) * 1000,
                             turn_id=self.turn_id)
        if milestone == "tts_submit" and self.speech_end_at is not None:
            self.recorder.record("turn.speech_end_estimate_to_tts_submit", (now - self.speech_end_at) * 1000,
                                 turn_id=self.turn_id)

    def finish(self):
        if not self.recorder.enabled:
            return
        from services.health.runtime import latency_hud
        self.values['total'] = (time.perf_counter() - (self.speech_end_at or self.endpoint_at)) * 1000
        self.recorder.last_turn = dict(self.values)
        if self.voice:
            logger.info('%s (endpoint-relative milestones; total through processor completion, not playback end)', latency_hud(self.values))


def mark_turn(milestone):
    turn = CURRENT_TURN.get()
    if turn is not None:
        turn.mark(milestone)


@dataclass
class Measurement:
    status: str = "ok"


class PerformanceRecorder:
    def __init__(self, metrics=None, *, enabled=True):
        self.metrics = metrics
        self.enabled = enabled
        self.run_id = uuid4().hex[:12]
        self.started = time.perf_counter()
        self.last_turn = {}
        self.voice_turns = 0
        self._summary_samples = {key: deque(maxlen=512) for key in ('whisper.inference', 'turn.endpoint_wait_estimate')}

    def record(self, stage, duration_ms, *, status="ok", turn_id=None):
        if not self.enabled:
            return
        current = CURRENT_TURN.get()
        if current is not None and stage in {'llm.first_text', 'llm.gemini.first_text'} and status == 'ok':
            current.values['llm_first_text'] = duration_ms
        if stage == 'turn.origin_voice':
            self.voice_turns += 1
        if stage in self._summary_samples and status == 'ok':
            self._summary_samples[stage].append(duration_ms)
        fields = {
            "run_id": self.run_id,
            "stage": stage,
            "duration_ms": round(duration_ms, 2),
            "elapsed_ms": round((time.perf_counter() - self.started) * 1000, 2),
            "status": status,
        }
        if turn_id is not None:
            fields["turn_id"] = turn_id
        print(f"[PERF] {stage}: {duration_ms:.0f} мс ({status})", flush=True)
        if self.metrics is not None:
            try:
                self.metrics.record("performance", **fields)
            except OSError as exc:
                logger.warning("Performance metrics unavailable: %s", type(exc).__name__)

    @contextmanager
    def span(self, stage):
        started = time.perf_counter()
        result = Measurement()
        if self.enabled:
            print(f"[PERF] {stage}: початок", flush=True)
        try:
            yield result
        except BaseException as exc:
            result.status = "cancelled" if type(exc).__name__ in {"CancelledError", "KeyboardInterrupt"} else "error"
            raise
        finally:
            self.record(stage, (time.perf_counter() - started) * 1000, status=result.status)

    async def measure(self, stage, awaitable):
        with self.span(stage):
            return await awaitable

    def session_summary(self):
        result = {'voice_turns': self.voice_turns, 'runtime_seconds': round(time.perf_counter() - self.started, 1), 'sample_window': 512}
        for stage, values in self._summary_samples.items():
            if values:
                ordered = sorted(values)
                result[stage] = dict(mean=statistics.mean(values), p50=statistics.median(values),
                                     p95=ordered[max(0, math.ceil(.95 * len(ordered)) - 1)])
        return result


DISABLED_PERFORMANCE = PerformanceRecorder(enabled=False)
