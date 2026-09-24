"""Offline Vosk endpoint/block-size comparison on hash-checked synthetic PCM.

No microphone, playback, network, LLM or local actions. Audio is decoded as
fast as possible: endpoint delay is on the AUDIO timeline, not wall latency.
"""
from __future__ import annotations

import json
import math
import time
from uuid import uuid4

import numpy as np

from testing.probes.tuning import read_samples
from testing.probes.whisper import SAMPLES, score
from services.audio.endpoint import QuietEndpoint


def final_words(model, pcm, rate):
    from vosk import KaldiRecognizer
    recognizer = KaldiRecognizer(model, rate)
    recognizer.SetWords(True)
    recognizer.AcceptWaveform(pcm)
    return json.loads(recognizer.FinalResult()).get("result", [])


def pause_offset(words, rate):
    """Insert at a Vosk-estimated inter-word boundary near the middle."""
    if len(words) < 3:
        raise ValueError("Not enough word boundaries for a pause fixture")
    pairs = list(zip(words[:-1], words[1:]))
    middle = len(pairs) // 2
    first, second = pairs[middle]
    return round((first["end"] + second["start"]) * 0.5 * rate) * 2


def signal_end(pcm, rate):
    # A fixed, deliberately low energy threshold marks the last non-silent
    # sample in these clean fixtures. Not a production VAD or speech gate.
    signal = np.frombuffer(pcm, dtype=np.int16).astype(np.int32)
    active = np.flatnonzero(np.abs(signal) > 32)
    if not active.size:
        raise ValueError("Empty synthetic speech signal")
    return (int(active[-1]) + 1) / rate


def decode(model, pcm, rate, block_ms, silence_ms=0):
    from vosk import KaldiRecognizer
    recognizer = KaldiRecognizer(model, rate)
    recognizer.SetWords(True)
    block = max(800, int(rate * block_ms / 1000)) * 2
    padded = pcm + b"\0\0" * (rate * 4)
    started = time.perf_counter()
    consumed = 0
    payload = {}
    endpoint = QuietEndpoint(rate, silence_ms) if silence_ms else None
    endpoint_kind = "native"
    for offset in range(0, len(padded), block):
        data = padded[offset:offset + block]
        consumed += len(data)
        if endpoint is not None:
            endpoint.feed(data)
        if recognizer.AcceptWaveform(data):
            candidate = json.loads(recognizer.Result())
            if candidate.get("text"):
                payload = candidate
                break
        elif endpoint is not None and endpoint.ready(json.loads(recognizer.PartialResult()).get("partial", "")):
            payload = json.loads(recognizer.FinalResult())
            endpoint_kind = "quiet"
            break
    return {"consumed_seconds": consumed / (2 * rate), "text": payload.get("text", ""),
            "endpoint_kind": endpoint_kind,
            "decode_seconds": time.perf_counter() - started}


def run(args):
    from config import load_settings
    from core.listen import VoskListener
    from core.security import scrub_sensitive_environment
    settings = load_settings()
    scrub_sensitive_environment()
    samples = read_samples(args.corpus)
    listener = VoskListener(settings)
    report = settings.paths.logs_dir / ("endpoint-comparison-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6] + ".jsonl")

    def record(event, **fields):
        row = {"event": event, **fields}
        with report.open("a", encoding="utf-8") as output:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(json.dumps({k: v for k, v in row.items() if k != "text"}), flush=True)

    if args.verify_report is not None:
        verify_refinement(args, settings, samples, listener, record)
        print(f"[ENDPOINT] report: {report}", flush=True)
        return
    record("environment", synthetic=True, timeline="audio_not_wall", blocks_ms=args.blocks,
           pauses_ms=args.pauses, silence_ms=args.silences, vosk_model=settings.stt_model_path)
    try:
        model = listener._get_model()
        for name, _ in SAMPLES:
            pcm, rate, _ = samples[name]
            boundary = pause_offset(final_words(model, pcm, rate), rate)
            for pause_ms in args.pauses:
                fixture = pcm[:boundary] + b"\0\0" * round(rate * pause_ms / 1000) + pcm[boundary:]
                end = signal_end(fixture, rate)
                baseline = None
                for block_ms in args.blocks:
                    for silence_ms in args.silences:
                        result = decode(model, fixture, rate, block_ms, silence_ms)
                        if baseline is None:
                            baseline = result["text"]
                        record("sample", fixture=name, pause_added_ms=pause_ms, block_ms=block_ms,
                               silence_ms=silence_ms, signal_end_seconds=round(end, 4),
                               endpoint_delay_ms=round((result["consumed_seconds"] - end) * 1000, 2),
                               truncated=result["consumed_seconds"] < end,
                               text_matches_baseline=result["text"] == baseline, **result)
    finally:
        listener.close()
    print(f"[ENDPOINT] report: {report}", flush=True)


def verify_refinement(args, settings, samples, listener, record):
    """Compare only fully captured pairs; existing native truncations stay visible.

This checks Whisper output, not microphone/Vosk confidence or physical sound.
No synthesis, network, API calls or changes to the saved corpus are involved.
    """
    from services.audio.whisper_process import ProcessWhisperRecognizer

    rows = [json.loads(line) for line in args.verify_report.read_text(encoding="utf-8").splitlines()]
    if not any(row.get("event") == "environment" and row.get("synthetic") is True
               and row.get("vosk_model") == settings.stt_model_path for row in rows):
        raise ValueError("Expected a matching synthetic Vosk endpoint report")
    measurements = {(row["fixture"], row["pause_added_ms"], row["block_ms"], row["silence_ms"]): row
                    for row in rows if row.get("event") == "sample"}
    worker = ProcessWhisperRecognizer(settings)
    worker.settings.benchmark_local_files_only = True
    record("environment", synthetic=True, check="whisper_output_not_live_turn",
           source_report=str(args.verify_report), model=settings.stt_whisper_model)
    try:
        if not worker.prepare()[0]:
            raise RuntimeError("Whisper preparation failed")
        from testing.probes.whisper import WARMUP
        warm_pcm, warm_rate, _ = samples[WARMUP[0]]
        worker.transcribe(warm_pcm, warm_rate)
        model = listener._get_model()
        for name, reference in SAMPLES:
            pcm, rate, _ = samples[name]
            boundary = pause_offset(final_words(model, pcm, rate), rate)
            for pause_ms in args.pauses:
                fixture = pcm[:boundary] + b"\0\0" * round(rate * pause_ms / 1000) + pcm[boundary:]
                for block_ms in args.blocks:
                    baseline = None
                    for silence_ms in args.silences:
                        row = measurements[name, pause_ms, block_ms, silence_ms]
                        duration = row["consumed_seconds"]
                        if not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0 < duration <= len(fixture) / (2 * rate) + 4:
                            raise ValueError("Invalid recorded endpoint duration")
                        if row["truncated"]:
                            record("refinement_skipped", fixture=name, pause_ms=pause_ms,
                                   silence_ms=silence_ms, reason="truncated_audio")
                            continue
                        padded = fixture + b"\0\0" * (rate * 4)
                        started = time.perf_counter()
                        result = worker.transcribe(padded[:round(duration * rate) * 2], rate)
                        if not worker.status()[0]:
                            raise RuntimeError("Whisper inference failed")
                        if silence_ms == 0:
                            baseline = result.text
                        record("refinement", fixture=name, pause_ms=pause_ms, silence_ms=silence_ms,
                               block_ms=block_ms, text=result.text, confidence=result.confidence,
                               meets_threshold=result.confidence >= settings.stt_whisper_min_confidence,
                               matches_baseline=result.text == baseline, **score(reference, result.text),
                               seconds=time.perf_counter() - started)
    finally:
        worker.close()
        listener.close()
