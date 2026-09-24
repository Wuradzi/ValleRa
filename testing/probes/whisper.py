"""Compare cached multilingual Whisper models on fixed synthetic Ukrainian audio.

No microphone, playback, LLM calls, command execution, or config/history changes.
--download-only explicitly fetches missing public Systran models, then exits.
Otherwise offline only. Audio and transcripts are synthetic and saved in logs.
--no-hints clears only this benchmark's STT prompt/hotwords for a control run.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import statistics
import threading
import time
import unicodedata
import wave
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

SAMPLES = (
    ("story", "Привіт, Валера. Розкажи коротку цікаву історію про подорож."),
    ("explanation", "Поясни простими словами, чому восени жовтіє листя на деревах."),
    ("conversation", "Я сьогодні трохи втомився. Давай просто поговоримо про музику."),
    ("browser", "Команда, відкрий браузер."),
    ("weather", "Команда, знайди погоду в місті Луцьк на завтра."),
    ("telegram", "Команда, відкрий телеграм."),
)
WARMUP = ("warmup", "Це коротка перевірка розпізнавання української мови.")
MODELS = ("small", "base", "tiny")


def normalize(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    text = text.replace("’", "'").replace("ʼ", "'").replace("`", "'")
    return " ".join(re.findall(r"[^\W_]+(?:'[^\W_]+)*", text))


def edit_distance(reference, hypothesis):
    previous = list(range(len(hypothesis) + 1))
    for index, expected in enumerate(reference, 1):
        current = [index]
        for column, observed in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[column] + 1,
                               previous[column - 1] + (expected != observed)))
        previous = current
    return previous[-1]


def score(reference, hypothesis):
    expected, observed = normalize(reference), normalize(hypothesis)
    words = expected.split()
    if not words:
        raise ValueError("Empty reference")
    errors = edit_distance(words, observed.split())
    return {"word_errors": errors, "reference_words": len(words),
            "wer_percent": round(errors / len(words) * 100, 2),
            "exact": expected == observed,
            "command_prefix_correct": (words[0] == "команда") == (observed.split()[:1] == ["команда"])}


def summarize(rows):
    summaries = []
    for name in MODELS:
        samples = [row for row in rows if row.get("event") == "sample" and row["model"] == name]
        loads = [row for row in rows if row.get("event") == "load" and row["model"] == name]
        if not samples:
            continue
        audio = sum(row["audio_seconds"] for row in samples)
        seconds = sum(row["seconds"] for row in samples)
        errors = sum(row["word_errors"] for row in samples)
        words = sum(row["reference_words"] for row in samples)
        resources = [row for row in rows if row.get("event") == "resources" and row["model"] == name]
        summaries.append({"model": name, "samples": len(samples), "exact": sum(row["exact"] for row in samples),
                          "median_seconds": round(statistics.median(row["seconds"] for row in samples), 3),
                          "mean_seconds": round(seconds / len(samples), 3), "rtf": round(seconds / audio, 3),
                          "wer_percent": round(errors / words * 100, 2), "word_errors": errors, "reference_words": words,
                          "prefix_errors": sum(not row["command_prefix_correct"] for row in samples),
                          "median_load_seconds": round(statistics.median(row["seconds"] for row in loads), 3),
                          "worker_peak_rss_mb": max((row["worker_peak_rss_mb"] for row in resources), default=None)})
    return summaries


def load_audio(path):
    with wave.open(str(path), "rb") as audio:
        rate, frames = audio.getframerate(), audio.getnframes()
        if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or frames <= 0:
            raise ValueError("Expected nonempty mono PCM16")
        pcm = audio.readframes(frames)
    return pcm, rate, frames / rate


class ResourceSampler:
    def __init__(self, worker):
        self.worker = worker
        self.peak_rss = self.peak_private = 0
        self.min_available = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True, name="benchmark-resources")

    def start(self):
        self._thread.start()

    def _sample(self):
        import psutil
        while not self._stop.is_set():
            available = psutil.virtual_memory().available
            self.min_available = min(self.min_available or available, available)
            try:
                process = self.worker._process
                if process is not None and process.pid is not None:
                    memory = psutil.Process(process.pid).memory_info()
                    self.peak_rss = max(self.peak_rss, memory.rss)
                    self.peak_private = max(self.peak_private, getattr(memory, "private", 0))
            except (psutil.Error, ValueError):
                pass
            self._stop.wait(0.2)

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)
        return {"worker_peak_rss_mb": round(self.peak_rss / 1048576, 1),
                "worker_peak_private_mb": round(self.peak_private / 1048576, 1),
                "min_available_ram_mb": round((self.min_available or 0) / 1048576, 1)}


def cached_models(settings, names, download):
    # Public weight files only; do not use an implicit Hugging Face credential.
    # Do not import faster_whisper in the parent: native libraries inflate RAM
    # and would make the benchmark unlike the application's isolated worker.
    from huggingface_hub import snapshot_download
    paths = {}
    for name in names:
        started = time.perf_counter()
        options = {"repo_id": f"Systran/faster-whisper-{name}",
                   "cache_dir": str(settings.paths.models_dir / "faster-whisper"), "token": False,
                   "allow_patterns": ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]}
        try:
            source = snapshot_download(**options, local_files_only=True)
            if not all((Path(source) / file).is_file() for file in ("model.bin", "config.json", "tokenizer.json")):
                raise FileNotFoundError("Incomplete model cache")
        except (OSError, ValueError):
            if not download:
                raise RuntimeError(f"{name}: missing cached files; run --download-only first") from None
            print(f"[COMPARE] downloading {name} (public model weights)", flush=True)
            source = snapshot_download(**options)
        paths[name] = str(Path(source).resolve())
        print(f"[COMPARE] {name}: files ready in {time.perf_counter() - started:.2f}s", flush=True)
    return paths


async def compare(args, settings, paths):
    import psutil
    from core.listen import VoskListener
    from core.speak import Speaker
    from services.audio.whisper_process import ProcessWhisperRecognizer

    directory = settings.paths.logs_dir / ("whisper-comparison-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6])
    directory.mkdir(parents=True)
    rows = []

    def record(event, **fields):
        row = {"event": event, **fields}
        rows.append(row)
        with (directory / "results.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(json.dumps(row, ensure_ascii=True), flush=True)

    record("environment", cpu_count=psutil.cpu_count(), ram_total_mb=round(psutil.virtual_memory().total / 1048576),
           ram_available_mb=round(psutil.virtual_memory().available / 1048576), profile=settings.performance_profile,
           device=settings.stt_whisper_device, compute_type=settings.stt_whisper_compute_type,
           cpu_threads=settings.stt_whisper_cpu_threads, beam=settings.stt_whisper_beam_size,
           hints_enabled=bool(settings.stt_whisper_prompt or settings.stt_whisper_hotwords),
           tts_voice_hint=settings.tts_voice_hint, tts_rate=settings.tts_rate,
           model_revisions={name: Path(path).name for name, path in paths.items()},
           synthetic=True, rounds=args.rounds, offline=True)
    speaker, listener = Speaker(settings), VoskListener(settings)
    try:
        await speaker.prepare()
        samples = {}
        for name, text in (WARMUP, *SAMPLES):
            path = directory / (name + ".wav")
            await asyncio.to_thread(speaker._generate_with_windows_speech, text, path)
            samples[name] = load_audio(path)
            record("audio", id=name, reference=text, seconds=samples[name][2],
                   pcm_sha256=hashlib.sha256(samples[name][0]).hexdigest())
        # Keep the actual Vosk model resident, as in ValleRa. Never open an input stream.
        started = time.perf_counter()
        await asyncio.to_thread(listener._get_model)
        record("vosk_resident", seconds=round(time.perf_counter() - started, 3))
        for round_number in range(1, args.rounds + 1):
            order = args.models if round_number % 2 else list(reversed(args.models))
            for name in order:
                # Snapshot path bypasses networking; other recognition options stay identical.
                isolated = replace(settings, stt_whisper_enabled=True, stt_whisper_model=paths[name])
                worker = ProcessWhisperRecognizer(isolated)
                worker.settings.benchmark_local_files_only = True
                monitor = ResourceSampler(worker)
                monitor.start()
                print(f"[COMPARE] round {round_number}/{args.rounds}, model={name}: loading", flush=True)
                try:
                    started = time.perf_counter()
                    ok, _ = await asyncio.to_thread(worker.prepare)
                    record("load", model=name, round=round_number, seconds=round(time.perf_counter() - started, 3), ok=ok)
                    if not ok:
                        raise RuntimeError(f"{name}: preparation failed")
                    pcm, rate, _ = samples[WARMUP[0]]
                    started = time.perf_counter()
                    warmup = await asyncio.to_thread(worker.transcribe, pcm, rate)
                    record("warmup", model=name, round=round_number, seconds=round(time.perf_counter() - started, 3),
                           recognized=bool(warmup.text))
                    for sample_id, reference in SAMPLES:
                        pcm, rate, duration = samples[sample_id]
                        started = time.perf_counter()
                        result = await asyncio.to_thread(worker.transcribe, pcm, rate)
                        seconds = time.perf_counter() - started
                        ok, _ = worker.status()
                        record("sample", model=name, round=round_number, id=sample_id, reference=reference,
                               hypothesis=result.text, seconds=round(seconds, 4), audio_seconds=round(duration, 4),
                               confidence=round(result.confidence, 4), runtime_ok=ok, **score(reference, result.text))
                        if not ok:
                            raise RuntimeError(f"{name}: inference failed")
                finally:
                    record("resources", model=name, round=round_number, **monitor.stop())
                    await asyncio.to_thread(worker.close)
        for summary in summarize(rows):
            record("summary", **summary)
        print(f"[COMPARE] Report and synthetic audio: {directory}", flush=True)
        return 0
    finally:
        await asyncio.gather(speaker.close(), asyncio.to_thread(listener.close))


def main(args):
    if os.name != "nt" and not args.download_only:
        raise ValueError("Synthetic voice benchmark currently requires Windows Speech")
    from config import load_settings
    from core.security import scrub_sensitive_environment
    settings = load_settings()
    if args.no_hints:
        settings = replace(settings, stt_whisper_prompt="", stt_whisper_hotwords="")
    scrub_sensitive_environment()
    paths = cached_models(settings, args.models, args.download_only)
    if args.download_only:
        return 0
    return asyncio.run(compare(args, settings, paths))

