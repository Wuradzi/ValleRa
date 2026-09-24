"""Offline paced audio -> real STT -> scripted stream -> real SAPI WAV.

No microphone, playback, network, vault, personal history or local actions.
The stream is artificial: this does NOT measure Gemini or first audible sound.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from core.speak import Speaker  # noqa: E402
from testing.probes.tuning import read_samples
from testing.probes.performance import summarize_turns


class PacedInput:
    """Replace only RawInputStream, delivering PCM with its real duration."""

    def __init__(self, pcm, sample_rate):
        self.pcm, self.sample_rate = pcm, sample_rate

    def stream(self, *, callback, blocksize, samplerate, **kwargs):
        if samplerate != self.sample_rate:
            raise ValueError("Unexpected sample rate")
        stop = threading.Event()
        block_bytes = blocksize * 2
        period = blocksize / samplerate

        def deliver():
            deadline = time.perf_counter()
            offset = 0
            while not stop.is_set():
                deadline += period
                if stop.wait(max(0, deadline - time.perf_counter())):
                    return
                data = self.pcm[offset:offset + block_bytes].ljust(block_bytes, b"\0")
                offset += block_bytes
                callback(data, blocksize, None, None)

        class Stream:
            def __enter__(self):
                self.worker = threading.Thread(target=deliver, name="synthetic-audio", daemon=True)
                self.worker.start()
                return self

            def __exit__(self, *exc):
                stop.set()
                self.worker.join(timeout=2)

        return Stream()


class FileSpeaker(Speaker):
    """Use the production queue and SAPI worker, but never an audio device."""

    def _speak_sync(self, text):
        path = self.settings.paths.cache_dir / "turn-fixture.wav"
        self._generate_with_windows_speech(text, path)
        if not self._is_valid_wav(path):
            raise RuntimeError("Synthetic TTS did not produce audio")
        if self._current_timing is not None:
            self._current_timing.mark("tts_file_ready")


class ScriptedProvider:
    def __init__(self, scenario):
        self.scenario = scenario

    async def chat_stream(self, messages):
        pieces = (
            ["Звісно. ", "Можемо ", "спокійно ", "поговорити ", "про ", "музику."]
            if self.scenario == "early_sentence" else
            ["Можемо ", "спокійно ", "поговорити ", "про музику ", "або про ", "подорожі."]
        )
        for index, piece in enumerate(pieces):
            await asyncio.sleep(0.2 if index == 0 else 0.15)
            yield piece


async def run(args):
    from config import load_settings
    from core.listen import VoskListener
    from core.metrics import MetricsCollector
    from core.performance import CURRENT_TURN, PerformanceRecorder
    from core.processor import COMMAND_PREFIX, CommandProcessor
    from core.security import scrub_sensitive_environment
    from services.llm.manager import LLMManager

    if os.name != "nt":
        raise RuntimeError("This silent SAPI benchmark requires Windows")
    settings = load_settings()
    scrub_sensitive_environment()
    samples = read_samples(args.corpus)
    report = settings.paths.logs_dir / ("turn-latency-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6] + ".jsonl")
    metrics = MetricsCollector(report)
    perf = PerformanceRecorder(metrics)
    metrics.record("benchmark", synthetic=True, llm="scripted", tts="file_only", microphone=False,
                   model=settings.stt_whisper_model, threads=settings.stt_whisper_cpu_threads)
    listener = VoskListener(settings)
    listener.performance = listener.whisper.performance = perf
    listener.whisper.settings.benchmark_local_files_only = True
    with tempfile.TemporaryDirectory(prefix="valera-turn-") as directory:
        paths = replace(settings.paths, cache_dir=Path(directory), data_dir=Path(directory))
        isolated = replace(settings, paths=paths)
        speaker = FileSpeaker(isolated)
        speaker.performance = perf
        manager = LLMManager(isolated)
        manager.performance = perf
        manager.available_order = ["scripted"]
        manager.active_name = "scripted"

        async def forbidden(*args, **kwargs):
            raise AssertionError("Local actions and confirmation are forbidden in this benchmark")

        processor = CommandProcessor(isolated, SimpleNamespace(route=forbidden), manager, speaker, metrics,
                                     {"state": {"mode": "chat"}, "performance": perf,
                                      "memory": SimpleNamespace(relevant=lambda _: [])})
        try:
            await asyncio.to_thread(listener._get_model)
            ok, _ = await asyncio.to_thread(listener.whisper.prepare)
            if not ok:
                raise RuntimeError("Offline Whisper preparation failed")
            pcm, rate, _ = samples["warmup"]
            await asyncio.to_thread(listener.whisper.transcribe, pcm, rate)
            await speaker.prepare()
            await speaker.start()
            for scenario in ("early_sentence", "final_sentence"):
                manager.providers = {"scripted": ScriptedProvider(scenario)}
                for name in ("story", "explanation", "conversation"):
                    pcm, rate, _ = samples[name]
                    replay = PacedInput(pcm, rate)
                    with patch("sounddevice.RawInputStream", replay.stream), patch.object(
                        listener, "_input_candidates", return_value=[(0, rate)]
                    ):
                        result = await asyncio.to_thread(listener.listen_once, 15, None)
                    if (not result.text or result.timing is None or result.engine == "conflict"
                            or result.confidence < settings.stt_chat_confidence_threshold
                            or COMMAND_PREFIX.match(result.text.strip())):
                        raise RuntimeError("Synthetic chat fixture not accepted; benchmark aborted")
                    metrics.record("benchmark_turn", turn_id=result.timing.turn_id, fixture=name, scenario=scenario)
                    token = CURRENT_TURN.set(result.timing)
                    try:
                        result.timing.mark("dispatch")
                        answer = await processor.process(result.text, forbidden, "voice")
                        if not answer.data.get("response_spoken"):
                            raise RuntimeError("Streaming pipeline failed")
                    finally:
                        CURRENT_TURN.reset(token)
                    await speaker.wait_until_idle()
                    _, turns = summarize_turns(report)
                    if turns[-1]["tts_error"] or turns[-1]["file_ready_ms"] is None:
                        raise RuntimeError("Silent TTS pipeline failed")
                    print(f"[TURN] {scenario}/{name}: {turns[-1]}", flush=True)
        finally:
            await speaker.close()
            await asyncio.to_thread(listener.close)
    print(f"[TURN] report: {report}", flush=True)

