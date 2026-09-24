"""Two neutral Gemini turns. No microphone, private history, or local actions.

Run explicitly to check the configured API/model; consumes normal API quota.
Only timing and allowlisted error metadata are printed, never API bodies.
"""

from __future__ import annotations

import json
import asyncio
import statistics
import time
from datetime import datetime, timezone
from uuid import uuid4

from config import load_settings  # noqa: E402
from core.security import scrub_sensitive_environment  # noqa: E402
from services.llm.errors import classify_failure  # noqa: E402
from services.llm.manager import CHAT_MODE_PROMPT, SYSTEM_PROMPT  # noqa: E402
from services.llm.providers import GeminiProvider  # noqa: E402


async def check_web_summary(settings, provider):
    from types import SimpleNamespace
    from services.llm.manager import LLMManager
    from services.web.answers import WebAnswerService

    manager = LLMManager(settings)
    manager.providers = {"gemini": provider}
    manager.available_order = ["gemini"]
    manager.active_name = "gemini"

    async def synthetic_page(url, query=""):
        return {"href": url, "title": "Синтетичний рецепт для перевірки",
                "text": "Для простого овочевого борщу потрібні буряк, капуста, морква, картопля, цибуля і вода. "
                        "Нарізану картоплю варять у воді, потім додають капусту. "
                        "Буряк, моркву та цибулю тушкують окремо, з'єднують із рештою овочів і доводять до готовності.",
                "published": None, "retrieved": "2026-09-11T00:00:00+00:00"}

    service = WebAnswerService(manager, reader=SimpleNamespace(read=synthetic_page))
    started = time.perf_counter()
    result = await service.answer("Як приготувати простий овочевий борщ за цим описом?", [
        {"href": "https://example.com/synthetic-recipe", "title": "Тестовий рецепт", "body": ""}])
    ok = bool(result.data.get("grounded"))
    print(json.dumps({"check": "web-summary", "ok": ok,
                      "failure": result.data.get("summary_failure"),
                      "claims": len(result.data.get("claims", [])),
                      "seconds": round(time.perf_counter() - started, 3)}), flush=True)
    return 0 if ok else 1


async def main(args) -> int:
    settings = load_settings()
    provider = GeminiProvider(
        settings.llm_models["gemini"], settings.api_keys["gemini"], settings.llm_timeout_seconds,
    )
    scrub_sensitive_environment()
    try:
        return await _run(args, settings, provider)
    finally:
        await provider.close()


async def _run(args, settings, provider):
    if not provider.api_key:
        print(json.dumps({"ok": False, "kind": "not_configured"}), flush=True)
        return 1
    if getattr(args, "connection_comparison", False):
        return await check_connections(settings, provider)
    if getattr(args, "live_web", False):
        return await check_live_web(settings, provider, extended=getattr(args, "extended", False), cases=getattr(args, "cases", None))
    if getattr(args, "web_summary", False):
        return await check_web_summary(settings, provider)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(assistant_name=settings.assistant_name)},
        {"role": "system", "content": CHAT_MODE_PROMPT},
    ]
    prompts = [
        "Привіт. Як тебе звати? Відповідай одним коротким реченням.",
        "Скільки буде два плюс два? Відповідай одним коротким реченням.",
    ]
    for turn, prompt in enumerate(prompts, start=1):
        messages.append({"role": "user", "content": prompt})
        started = time.perf_counter()
        chunks = []
        first_chunk = None
        try:
            async for chunk in provider.chat_stream(messages):
                if first_chunk is None:
                    first_chunk = time.perf_counter() - started
                chunks.append(chunk)
        except Exception as exc:
            failure = classify_failure(exc)
            print(json.dumps({
                "turn": turn, "ok": False, "kind": failure.kind, "reason": failure.reason,
                "status": failure.status, "seconds": round(time.perf_counter() - started, 3),
            }), flush=True)
            return 1  # Do not retry or work around a blocked request.
        answer = "".join(chunks)
        messages.append({"role": "assistant", "content": answer})
        print(json.dumps({
            "turn": turn, "ok": True, "characters": len(answer),
            "first_chunk_s": round(first_chunk, 3) if first_chunk is not None else None,
            "seconds": round(time.perf_counter() - started, 3),
        }), flush=True)
    return 0


class ConnectionTrace:
    """Allowlisted transport timing only. Never retain trace info/headers/URLs."""
    EVENTS = frozenset({'connection.connect_tcp', 'connection.start_tls',
                       'http11.receive_response_headers', 'http2.receive_response_headers'})

    def __init__(self):
        self.started = {}
        self.events = []

    async def trace(self, name, info):
        event, _, status = name.rpartition('.')
        if event not in self.EVENTS:
            return
        now = time.perf_counter()
        if status == 'started':
            self.started[event] = now
        elif status in {'complete', 'failed'} and event in self.started:
            self.events.append({'event': event, 'status': status,
                                'ms': round((now - self.started.pop(event)) * 1000, 2)})

    async def attach(self, request):
        request.extensions['trace'] = self.trace


async def check_connections(settings, provider):
    """Bounded live diagnostic, production streaming path; config untouched."""
    from google import genai
    from google.genai import types
    import httpx

    class ObservedProvider(GeminiProvider):
        def __init__(self, pooled):
            super().__init__(provider.model, provider.api_key, provider.timeout)
            self.pooled = pooled
            self.cached = None
            self.trace = ConnectionTrace()
            self.creation_ms = None

        def _client(self, timeout_seconds):
            self.creation_ms = 0.0
            if self.cached is not None:
                return self.cached
            started = time.perf_counter()
            client = genai.Client(api_key=self.api_key, http_options=types.HttpOptions(
                timeout=timeout_seconds * 1000, retry_options=types.HttpRetryOptions(attempts=1),
                async_client_args={'event_hooks': {'request': [self.attach_trace]},
                                   'limits': httpx.Limits(keepalive_expiry=60.0)}))
            self.creation_ms = round((time.perf_counter() - started) * 1000, 2)
            if self.pooled:
                self.cached = client
            return client

        async def attach_trace(self, request):
            await self.trace.attach(request)

        async def _release_client(self, client):
            if not self.pooled:
                await GeminiProvider._close_client(client)

        async def close(self):
            if self.cached is not None:
                client, self.cached = self.cached, None
                await GeminiProvider._close_client(client)

    fresh, pooled = ObservedProvider(False), ObservedProvider(True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = settings.paths.logs_dir / f'gemini-connections-{stamp}-{uuid4().hex[:8]}.json'
    report = {'kind': 'live_gemini_connection_comparison', 'status': 'running', 'model': provider.model,
              'requests_max': 6, 'retries': 0, 'keepalive_expiry_seconds': 60, 'results': [],
              'limitations': ['Six sequential fixed neutral requests, one run, no private history.',
                              'connect_tcp includes name resolution; headers wait includes network and server.',
                              'Fresh and pooled use identical 60s idle lifetime; production configuration unchanged.',
                              'No acoustic, microphone, STT or TTS timing. Not a statistical benchmark.']}
    print(f'[CONNECTIONS] report: {path}', flush=True)
    messages = [{'role': 'user', 'content': 'Скільки буде два плюс два? Відповідай одним коротким реченням українською.'}]
    try:
        for index, selected in enumerate((fresh, pooled, pooled, fresh, fresh, pooled), 1):
            if index > 1:
                await asyncio.sleep(5)  # Bounded pacing, no retry on quota errors.
            reused = selected.cached is not None
            selected.trace = ConnectionTrace()
            row = {'request': index, 'mode': 'pooled' if selected.pooled else 'fresh',
                   'reused_client': reused, 'ok': False, 'first_text_ms': None}
            report['results'].append(row)
            started = time.perf_counter()
            stream = selected.chat_stream(messages)
            try:
                async with asyncio.timeout(45):
                    async for chunk in stream:
                        if chunk.strip() and row['first_text_ms'] is None:
                            row['first_text_ms'] = round((time.perf_counter() - started) * 1000, 2)
                row['ok'] = row['first_text_ms'] is not None
            except Exception as exc:
                failure = classify_failure(exc)
                row['failure'] = {'kind': failure.kind, 'status': failure.status, 'reason': failure.reason}
            finally:
                await stream.aclose()
                row.update(total_ms=round((time.perf_counter() - started) * 1000, 2),
                           client_creation_ms=selected.creation_ms, transport=selected.trace.events)
                print(json.dumps(row), flush=True)
                path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            if not row['ok']:
                report['status'] = 'stopped_on_failure'
                return 1
        report['summary'] = {}
        for label, selection in (('fresh', [r for r in report['results'] if r['mode'] == 'fresh']),
                                 ('pooled_warm', [r for r in report['results'] if r['reused_client']])):
            report['summary'][label] = {'n': len(selection), 'median_first_text_ms':
                round(statistics.median(r['first_text_ms'] for r in selection), 2)}
        report['status'] = 'completed'
        print(json.dumps(report['summary']), flush=True)
        return 0
    finally:
        try:
            await pooled.close()
        finally:
            if report['status'] == 'running':
                report['status'] = 'interrupted'
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


async def check_live_web(settings, provider, *, extended=False, cases=None):
    """Real production search/reader/summary path; fixed public questions only.

Records public excerpts actually given to the model for manual assessment.
No microphone, TTS, vault prompts, conversation history or local actions.
Success here means delivery, NOT independently verified factual correctness.
    """
    from services.llm.manager import LLMManager
    from services.web.answers import WebAnswerService
    from services.web.reader import PublicPageReader, read_failure_reason
    from services.web.search import WebSearchService
    from skills.web.skill import search_web

    queries = [
        ("mechanism", "Що таке кулькова ручка і як вона працює? Поясни будову та принцип подачі чорнила."),
        ("history", "Що відбувалося з містом Вавилон у Середньовіччі? Відрізни цей період від його давнього розквіту."),
        ("explanation", "Чому вода в горах кипить за нижчої температури і як це впливає на приготування їжі?"),
        ("comparison", "Чим оперативна пам'ять RAM відрізняється від SSD? Поясни призначення та збереження даних після вимкнення живлення."),
    ]
    if extended:
        queries.extend([
            ("tides", "Чому виникають припливи й відпливи та яку роль відіграють Місяць і Сонце?"),
            ("graphics", "Чим векторна графіка відрізняється від растрової? Поясни масштабування та приклади застосування."),
        ])
    if cases:
        queries = [(name, question) for name, question in queries if name in cases]
    manager = LLMManager(settings)
    manager.providers = {"gemini": provider}
    manager.available_order = ["gemini"]
    manager.active_name = "gemini"
    reader = PublicPageReader()
    pages = []
    read_errors = []

    class ObservedReader:
        async def read(self, url, query=""):
            try:
                page = await reader.read(url, query=query)
            except Exception as exc:
                read_errors.append({"url": url, "kind": read_failure_reason(exc)})
                raise
            pages.append(dict(page))
            return page

    service = WebAnswerService(manager, ObservedReader())
    services = {"web_answers": service, "web": WebSearchService()}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = settings.paths.logs_dir / f"live-web-{stamp}-{uuid4().hex[:8]}.json"
    results = []
    for case, query in queries:
        pages.clear()
        read_errors.clear()
        started = time.perf_counter()
        result = await search_web(query, services)
        row = {"case": case, "query": query, "seconds": round(time.perf_counter() - started, 3),
               "response": result.response, "data": result.data, "pages": list(pages), "read_errors": list(read_errors)}
        results.append(row)
        report.write_text(json.dumps({"live": True, "model": provider.model,
                                      "quality_manually_assessed": False, "results": results},
                                     ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"check": case, "delivered": bool(result.data.get("grounded")),
                          "seconds": row["seconds"], "sources_read": result.data.get("sources_read", 0),
                          "coverage": result.data.get("coverage"),
                          "additional_search": result.data.get("additional_search"),
                          "page_reading": result.data.get("page_reading"),
                          "search_failure": result.data.get("search_failure"),
                          "failure": result.data.get("summary_failure"), "report": str(report)},
                         ensure_ascii=False), flush=True)
        if manager._cooling_down("gemini"):
            break  # Honour quota; no retry or switch to spend other providers' quota.
    return 0 if len(results) == len(queries) and all(r["data"].get("grounded") for r in results) else 1
