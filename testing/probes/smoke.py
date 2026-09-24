"""Opt-in smoke checks. No microphone capture, playback, or local commands.

--audio/--stt generate temporary synthetic speech. --network sends only the
fixed public test prompts below, never the user's conversation history.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
import wave
from contextlib import AsyncExitStack
from pathlib import Path

from config import load_settings  # noqa: E402
from core.security import scrub_sensitive_environment  # noqa: E402
from core.speak import Speaker  # noqa: E402
from core.speech_text import SpeechBuffer  # noqa: E402
from services.audio.whisper_process import ProcessWhisperRecognizer  # noqa: E402
from services.llm.manager import LLMManager  # noqa: E402
from services.web.weather import WeatherService  # noqa: E402
from services.web.search import WebSearchService, search_failure_reason  # noqa: E402


def emit_report(name, **fields):
    print(json.dumps({"check": name, **fields}, ensure_ascii=True), flush=True)


async def main(args):
    async with AsyncExitStack() as cleanup:
        return await _run(args, cleanup)


async def _run(args, cleanup):
    if getattr(args, "read_pages", False):
        return await check_pages()
    if getattr(args, "search_only", False):
        return await check_search(args)
    successful = True

    def report(name, **fields):
        nonlocal successful
        if fields.get("ok") is False or "error" in fields or "skipped" in fields:
            successful = False
        emit_report(name, **fields)

    settings = load_settings()
    llm = LLMManager(settings) if args.network else None
    if llm is not None:
        cleanup.push_async_callback(llm.close)
    scrub_sensitive_environment()
    if args.audio or args.stt:
        speaker = Speaker(settings)
        worker = ProcessWhisperRecognizer(settings) if args.stt else None
        if worker is not None:
            worker.settings.benchmark_local_files_only = True
        try:
            if args.audio:
                started = time.perf_counter()
                await speaker.prepare()
                report("tts-prepare", seconds=round(time.perf_counter() - started, 3))
            with tempfile.TemporaryDirectory(prefix="valera-smoke-") as directory:
                path = Path(directory) / "sample.wav"
                phrase = "Команда, знайди погоду в місті Луцьк."
                started = time.perf_counter()
                await asyncio.to_thread(speaker._generate_with_windows_speech, phrase, path)
                valid = Speaker._is_valid_wav(path)
                report("tts-generation", ok=valid, seconds=round(time.perf_counter() - started, 3))
                if not valid:
                    return 1
                first_pid = speaker._tts_process.pid
                started = time.perf_counter()
                await asyncio.to_thread(speaker._generate_with_windows_speech, phrase, path)
                report(
                    "tts-warm",
                    ok=Speaker._is_valid_wav(path),
                    same_process=speaker._tts_process.pid == first_pid,
                    seconds=round(time.perf_counter() - started, 3),
                )
                if worker is not None:
                    started = time.perf_counter()
                    ok, _ = await asyncio.to_thread(worker.prepare)
                    report("whisper-load", ok=ok, seconds=round(time.perf_counter() - started, 3))
                    with wave.open(str(path), "rb") as audio:
                        rate = audio.getframerate()
                        pcm = audio.readframes(audio.getnframes())
                        report(
                            "sample",
                            seconds=round(audio.getnframes() / rate, 3),
                            channels=audio.getnchannels(),
                        )
                    for turn in (1, 2):
                        started = time.perf_counter()
                        result = await asyncio.to_thread(worker.transcribe, pcm, rate)
                        report(
                            "whisper-turn",
                            ok=bool(result.text),
                            turn=turn,
                            text=result.text,
                            confidence=round(result.confidence, 3),
                            seconds=round(time.perf_counter() - started, 3),
                        )
                    task = asyncio.create_task(asyncio.to_thread(worker.transcribe, pcm, rate))
                    await asyncio.sleep(0.1)
                    started = time.perf_counter()
                    await asyncio.to_thread(worker.close)
                    await asyncio.wait_for(task, 3)
                    report("whisper-cancel", seconds=round(time.perf_counter() - started, 3))
        finally:
            if worker is not None:
                await asyncio.to_thread(worker.close)
            await speaker.close()

    if args.network:
        started = time.perf_counter()
        try:
            data = await WeatherService().get("Луцьк")
            report(
                "weather",
                ok=True,
                response=WeatherService.summarize(data, "Луцьк"),
                seconds=round(time.perf_counter() - started, 3),
            )
        except Exception as exc:
            report("weather", ok=False, error=type(exc).__name__)
        try:
            rows = WebSearchService.clean_results(
                await WebSearchService().search("Raspberry Pi official website")
            )
            report("search", ok=bool(rows), count=len(rows), domains=[row["domain"] for row in rows])
        except Exception as exc:
            report("search", error=type(exc).__name__)
        provider = llm.providers["gemini"]
        if not provider.api_key:
            report("gemini-stream", skipped="no configured key")
        else:
            started = time.perf_counter()
            buffer = SpeechBuffer()
            first_token = first_phrase = None
            try:
                async for chunk in provider.chat_stream(
                    [
                        {
                            "role": "user",
                            "content": "Привіт. Відповідай українською двома короткими реченнями без Markdown: як ти можеш допомогти в розмові?",
                        }
                    ]
                ):
                    if first_token is None:
                        first_token = time.perf_counter() - started
                    if buffer.feed(chunk) and first_phrase is None:
                        first_phrase = time.perf_counter() - started
                if buffer.feed("", final=True) and first_phrase is None:
                    first_phrase = time.perf_counter() - started
                report(
                    "gemini-stream",
                    ok=True,
                    first_token_s=first_token,
                    first_phrase_s=first_phrase,
                    full_response_s=time.perf_counter() - started,
                )
            except Exception as exc:
                report("gemini-stream", ok=False, error=type(exc).__name__)
    return 0 if successful else 1


async def check_search(args):
    """Bounded reliability probe, no settings/key/history loading or LLM calls."""
    queries = [
        ("mechanism", "Що таке кулькова ручка і як вона працює? Поясни будову та принцип подачі чорнила."),
        ("history", "Що відбувалося з містом Вавилон у Середньовіччі? Відрізни цей період від його давнього розквіту."),
        ("explanation", "Чому вода в горах кипить за нижчої температури і як це впливає на приготування їжі?"),
        ("comparison", "Чим оперативна пам'ять RAM відрізняється від SSD? Поясни призначення та збереження даних після вимкнення живлення."),
    ]
    service = WebSearchService(backend=args.backend)
    outcomes = []
    for round_number in range(1, args.rounds + 1):
        for case, query in queries:
            started = time.perf_counter()
            try:
                rows = service.clean_results(await service.search(query), query=query, limit=5)
                row = {"ok": bool(rows), "count": len(rows),
                       "results": rows}
            except Exception as exc:
                row = {"ok": False, "error": search_failure_reason(exc)}
            outcomes.append(row)
            emit_report("search-only", case=case, round=round_number, backend=args.backend,
                        seconds=round(time.perf_counter() - started, 3), **row)
    emit_report("search-delivery", passed=sum(r["ok"] for r in outcomes), total=len(outcomes),
                quality_manually_assessed=False)
    return 0 if all(r["ok"] for r in outcomes) else 1


async def check_pages():
    """Fixed public retrieval cases; no settings, keys or generated answer."""
    from services.web.reader import PublicPageReader, read_failure_reason
    cases = (
        ('university', 'https://lntu.edu.ua/uk/struktura/cafedries/kafedra-kiberbezpeky', 'кафедра кібербезпеки'),
        ('software', 'https://docs.python.org/3/library/asyncio.html', 'asyncio tasks coroutines'),
        ('hardware', 'https://www.raspberrypi.com/products/raspberry-pi-5/', 'Raspberry Pi 5 specifications'),
        ('science', 'https://science.nasa.gov/earth/facts/', 'Earth atmosphere water'),
    )
    passed = 0
    for case, url, query in cases:
        started = time.perf_counter()
        try:
            page = await PublicPageReader().read(url, query=query)
            passed += 1
            emit_report('read-pages', case=case, requested_url=url, ok=True,
                        seconds=round(time.perf_counter() - started, 3), **page)
        except Exception as exc:
            emit_report('read-pages', case=case, requested_url=url, ok=False,
                        seconds=round(time.perf_counter() - started, 3), error=read_failure_reason(exc))
    emit_report('read-pages-summary', passed=passed, total=len(cases), live_api=False)
    return 0 if passed == len(cases) else 1
