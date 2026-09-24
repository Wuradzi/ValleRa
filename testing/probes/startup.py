"""Measure local startup without microphone, playback, private history or LLM API.

Default: cached Whisper files only (no network). --resolve-online permits a
Hugging Face download when the cache is incomplete, as in the normal app.
Complete cached models are used without a network refresh in either mode.
--network additionally enables LLM metadata healthchecks and update checking;
it never generates an LLM response. The application index is written to a temp dir.
--stt-sample synthesizes a fixed neutral phrase to a temporary WAV, then measures
two Whisper transcriptions. No playback and no microphone capture.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
import wave
from pathlib import Path

from core.metrics import MetricsCollector  # noqa: E402
from core.performance import PerformanceRecorder  # noqa: E402


async def measure_sample(speaker, worker, perf, directory):
    path = Path(directory) / "sample.wav"
    with perf.span("sample.tts_to_wav"):
        await asyncio.to_thread(
            speaker._generate_with_windows_speech,
            "Привіт, Валера. Розкажи коротку цікаву історію про подорож.",
            path,
        )
    with wave.open(str(path), "rb") as audio:
        rate = audio.getframerate()
        duration = audio.getnframes() / rate
        if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or duration <= 0:
            raise ValueError("Expected a nonempty mono PCM16 test sample")
        pcm = audio.readframes(audio.getnframes())
    successful = True
    for turn in (1, 2):
        started = time.perf_counter()
        with perf.span(f"sample.whisper_turn_{turn}") as measurement:
            result = await asyncio.to_thread(worker.transcribe, pcm, rate)
            if not result.text:
                successful = False
                measurement.status = "unavailable"
        seconds = time.perf_counter() - started
        fields = {"run_id": perf.run_id, "turn": turn, "audio_seconds": round(duration, 3),
                  "recognition_seconds": round(seconds, 3), "realtime_factor": round(seconds / duration, 3),
                  "recognized": bool(result.text)}
        perf.metrics.record("performance_sample", **fields)
        print(json.dumps(fields), flush=True)
    return successful


async def main(args):
    from config import load_settings
    from core.security import scrub_sensitive_environment

    settings = load_settings()
    output = settings.paths.logs_dir / ("performance-" + time.strftime("%Y%m%d-%H%M%S") + ".jsonl")
    perf = PerformanceRecorder(MetricsCollector(output))
    with perf.span("startup.app_imports"):
        from core.app import ValleRaApp
        from services.apps.indexer import ApplicationIndexer
        from services.llm.manager import LLMManager
        from core.listen import VoskListener
        from core.speak import Speaker
    import psutil

    process = psutil.Process()
    metadata = {"run_id": perf.run_id, "measurement": "local-startup",
                "resolve_online": args.resolve_online, "llm_checks": args.network,
                "stt_sample": args.stt_sample, "cpu_count": psutil.cpu_count(),
                "profile": settings.performance_profile, "whisper_model": settings.stt_whisper_model,
                "whisper_device": settings.stt_whisper_device, "whisper_compute_type": settings.stt_whisper_compute_type,
                "whisper_beam": settings.stt_whisper_beam_size, "whisper_cpu_threads": settings.stt_whisper_cpu_threads,
                "ram_total_mb": round(psutil.virtual_memory().total / 1048576),
                "ram_available_mb": round(psutil.virtual_memory().available / 1048576),
                "rss_mb": round(process.memory_info().rss / 1048576, 1)}
    perf.metrics.record("performance_environment", **metadata)
    print(json.dumps(metadata, ensure_ascii=True), flush=True)
    with perf.span("startup.probe_construct"):
        speaker = Speaker(settings)
        speaker.performance = perf
        listener = VoskListener(settings)
        listener.performance = listener.whisper.performance = perf
        listener.whisper.settings.benchmark_local_files_only = not args.resolve_online
        llm = LLMManager(settings) if args.network else None
        if llm:
            llm.performance = perf
    scrub_sensitive_environment()

    with tempfile.TemporaryDirectory(prefix="valera-perf-") as temporary:
        app = object.__new__(ValleRaApp)
        app.settings, app.listener, app.speaker, app.performance = settings, listener, speaker, perf
        indexer = ApplicationIndexer(Path(temporary) / "applications.json", settings.application_aliases)
        jobs = {
            "app_index": perf.measure("startup.app_index", asyncio.to_thread(indexer.rebuild)),
            "tts": perf.measure("startup.tts_prepare", speaker.prepare()),
            "stt": perf.measure("startup.stt_total", app._prepare_stt()),
        }
        if llm:
            jobs["llm"] = perf.measure("startup.llm_checks", llm.check_all())
            jobs["updates"] = perf.measure("startup.update_check", app._check_updates())
        started = time.perf_counter()
        try:
            results = await asyncio.gather(*jobs.values(), return_exceptions=True)
            perf.record("startup.backends_wall", (time.perf_counter() - started) * 1000)
            failed = [name for name, result in zip(jobs, results) if isinstance(result, BaseException)]
            for name, result in zip(jobs, results):
                if isinstance(result, BaseException):
                    print(f"[PERF] {name}: failed ({type(result).__name__})", flush=True)
            ok, _ = listener.whisper.status()
            # A second prepare request is a true warm-worker comparison.
            if ok and listener.whisper.enabled and listener.whisper._process is not None:
                await perf.measure("startup.whisper_warm", asyncio.to_thread(listener.whisper.prepare))
            sample_ok = True
            if args.stt_sample:
                if failed or not ok or not listener.whisper.enabled:
                    sample_ok = False
                    print("[PERF] sample skipped: local backends unavailable", flush=True)
                else:
                    if listener.whisper._process is None:
                        ok, _ = await perf.measure("sample.whisper_prepare", asyncio.to_thread(listener.whisper.prepare))
                        if not ok:
                            return 1
                    sample_ok = await measure_sample(speaker, listener.whisper, perf, temporary)
            successful = not failed and ok and sample_ok
            print(json.dumps({"report": str(output), "ok": successful,
                              "rss_mb": round(process.memory_info().rss / 1048576, 1)}, ensure_ascii=True), flush=True)
            return 0 if successful else 1
        finally:
            cleanup = [speaker.close(), asyncio.to_thread(listener.close)]
            if llm is not None:
                cleanup.append(llm.close())
            await asyncio.gather(*cleanup, return_exceptions=True)
