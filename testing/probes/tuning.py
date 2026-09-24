"""Offline CPU/word-timing experiment using existing synthetic comparison WAVs.

Runs sequentially, retains Vosk in RAM, never opens microphone or plays audio.
The experimental word-timestamp override is private to the test worker.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import statistics
import time
from dataclasses import replace
from uuid import uuid4

from testing.probes.whisper import SAMPLES, WARMUP, ResourceSampler, load_audio, score


def read_samples(directory):
    rows = [json.loads(line) for line in (directory / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    if not any(row.get("event") == "environment" and row.get("synthetic") is True for row in rows):
        raise ValueError("Expected synthetic benchmark corpus")
    originals = {row["id"]: row for row in rows if row.get("event") == "audio"}
    samples = {}
    for name, reference in (WARMUP, *SAMPLES):
        pcm, rate, duration = load_audio(directory / (name + ".wav"))
        expected = originals[name]
        if expected["reference"] != reference or hashlib.sha256(pcm).hexdigest() != expected["pcm_sha256"]:
            raise ValueError(f"Synthetic fixture mismatch: {name}")
        samples[name] = pcm, rate, duration
    return samples


async def run(args):
    import psutil
    from config import load_settings
    from core.listen import VoskListener
    from core.security import scrub_sensitive_environment
    from services.audio.whisper_process import ProcessWhisperRecognizer

    settings = load_settings()
    scrub_sensitive_environment()
    samples = read_samples(args.corpus)
    report = settings.paths.logs_dir / ("whisper-tuning-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6] + ".jsonl")
    rows = []

    def record(event, **fields):
        row = {"event": event, **fields}
        rows.append(row)
        with report.open("a", encoding="utf-8") as output:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(json.dumps({key: value for key, value in row.items() if key != "hypothesis"}), flush=True)

    record("environment", model=settings.stt_whisper_model, beam=settings.stt_whisper_beam_size,
           cpu_physical=psutil.cpu_count(logical=False), cpu_logical=psutil.cpu_count(),
           ram_total_mb=round(psutil.virtual_memory().total / 1048576),
           ram_available_mb=round(psutil.virtual_memory().available / 1048576),
           device=settings.stt_whisper_device, compute_type=settings.stt_whisper_compute_type,
           hints=bool(settings.stt_whisper_prompt or settings.stt_whisper_hotwords),
           corpus=str(args.corpus.resolve()), rounds=args.rounds, synthetic=True,
           thresholds={"chat": settings.stt_chat_confidence_threshold, "command": settings.stt_command_confidence_threshold,
                       "whisper": settings.stt_whisper_min_confidence})
    listener = VoskListener(settings)
    variants = [(threads, words == "on") for threads in args.threads for words in args.word_modes]
    try:
        await asyncio.to_thread(listener._get_model)
        for round_number in range(1, args.rounds + 1):
            for threads, words in variants if round_number % 2 else reversed(variants):
                variant = f"threads-{threads}-words-{int(words)}"
                print(f"[TUNE] round {round_number}: {variant}", flush=True)
                worker = ProcessWhisperRecognizer(replace(settings, stt_whisper_cpu_threads=threads, stt_whisper_enabled=True))
                worker.settings.benchmark_local_files_only = True
                worker.settings.benchmark_word_timestamps = words
                monitor = ResourceSampler(worker)
                monitor.start()
                try:
                    started = time.perf_counter()
                    ok, _ = await asyncio.to_thread(worker.prepare)
                    record("load", variant=variant, round=round_number, seconds=round(time.perf_counter() - started, 4),
                           ok=ok, stages=worker.last_timings)
                    if not ok:
                        raise RuntimeError("Whisper preparation failed")
                    pcm, rate, _ = samples[WARMUP[0]]
                    warm = await asyncio.to_thread(worker.transcribe, pcm, rate)
                    if not warm.text or not worker.status()[0]:
                        raise RuntimeError("Warmup failed")
                    for name, reference in SAMPLES:
                        pcm, rate, duration = samples[name]
                        started = time.perf_counter()
                        result = await asyncio.to_thread(worker.transcribe, pcm, rate)
                        seconds = time.perf_counter() - started
                        threshold = (settings.stt_command_confidence_threshold if name in {"browser", "weather", "telegram"}
                                     else settings.stt_chat_confidence_threshold)
                        record("sample", variant=variant, round=round_number, id=name, seconds=round(seconds, 4),
                               audio_seconds=round(duration, 4), hypothesis=result.text, confidence=round(result.confidence, 4),
                               passes_turn_threshold=result.confidence >= threshold,
                               passes_whisper_threshold=result.confidence >= settings.stt_whisper_min_confidence,
                               **score(reference, result.text))
                        if not worker.status()[0]:
                            raise RuntimeError("Whisper inference failed")
                finally:
                    record("resources", variant=variant, round=round_number, **monitor.stop())
                    await asyncio.to_thread(worker.close)
        for threads, words in variants:
            variant = f"threads-{threads}-words-{int(words)}"
            measurements = [row for row in rows if row["event"] == "sample" and row["variant"] == variant]
            record("summary", variant=variant, count=len(measurements),
                   mean_seconds=round(statistics.mean(row["seconds"] for row in measurements), 4),
                   median_seconds=round(statistics.median(row["seconds"] for row in measurements), 4),
                   wer_percent=round(100 * sum(row["word_errors"] for row in measurements) / sum(row["reference_words"] for row in measurements), 2),
                   min_confidence=min(row["confidence"] for row in measurements),
                   below_turn_threshold=sum(not row["passes_turn_threshold"] for row in measurements),
                   below_whisper_threshold=sum(not row["passes_whisper_threshold"] for row in measurements),
                   prefix_errors=sum(not row["command_prefix_correct"] for row in measurements))
        print(f"[TUNE] report: {report}", flush=True)
    finally:
        await asyncio.to_thread(listener.close)
