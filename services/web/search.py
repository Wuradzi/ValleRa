from __future__ import annotations

import asyncio
import html
import re
import math
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
from urllib.parse import urlsplit

from core.security import sanitized_environment
from services.web.intents import query_terms

FAILURE_KINDS = frozenset({
    "timeout", "address_error", "tls_error", "no_results", "connection_error",
    "search_error", "worker_error", "busy",
})


class SearchError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason if reason in FAILURE_KINDS else "search_error"
        super().__init__(self.reason)


def search_failure_reason(exc):
    """Classify wrapped DDGS failures without logging queries/response bodies."""
    if isinstance(exc, SearchError):
        return exc.reason
    if isinstance(exc, TimeoutError) or type(exc).__name__ == "TimeoutException":
        return "timeout"
    message = str(exc).lower()[:8000]
    if "os error 10049" in message or "winerror 10049" in message:
        return "address_error"
    if "certificate" in message or "tls" in message:
        return "tls_error"
    if "no results found" in message:
        return "no_results"
    if "connecterror" in message or "tcp connect error" in message:
        return "connection_error"
    return "search_error"


class WebSearchService:
    TIMEOUT_SECONDS = 10

    def __init__(self, *, backend="auto"):
        if backend not in {"auto", "duckduckgo"}:
            raise ValueError("Unsupported search backend")
        self.backend = backend
        self._lock = asyncio.Lock()

    async def search(self, query: str, max_results: int = 5) -> list[dict]:
        if not isinstance(query, str) or not query.strip() or len(query) > 600:
            raise ValueError("Invalid search query")
        if type(max_results) is not int or not 1 <= max_results <= 5:
            raise ValueError("Search result limit must be between 1 and 5")
        # Do not accumulate requests/processes while a slow engine is working.
        if self._lock.locked():
            raise SearchError("busy")
        async with self._lock:
            process = None
            try:
                async with asyncio.timeout(self.TIMEOUT_SECONDS):
                    process = await asyncio.create_subprocess_exec(
                        sys.executable, "-X", "utf8", "-m", "services.web.search",
                        cwd=str(Path(__file__).resolve().parents[2]),
                        env=sanitized_environment(),
                        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                    payload = json.dumps({"query": query, "max_results": max_results,
                                          "backend": self.backend}).encode("utf-8")
                    output, _ = await process.communicate(payload)
                    if process.returncode != 0 or len(output) > 200000:
                        raise SearchError("worker_error")
                    try:
                        result = json.loads(output)
                        if not isinstance(result, dict):
                            raise ValueError
                        if "error" in result:
                            raise SearchError(result["error"])
                        rows = result["rows"]
                        if not isinstance(rows, list) or len(rows) > max_results or any(
                            not isinstance(row, dict) or any(
                                not isinstance(row.get(key), str) for key in ("title", "body", "href")
                            ) for row in rows
                        ):
                            raise ValueError
                        return rows
                    except (ValueError, KeyError, TypeError) as exc:
                        raise SearchError("worker_error") from exc
            finally:
                # Cancelling a thread cannot stop DDGS/native networking. Kill
                # and reap only our own child before releasing the request slot.
                if process is not None and process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.wait()

    @staticmethod
    def _search(query: str, max_results: int, backend="auto") -> list[dict]:
        from ddgs import DDGS

        return [
            {
                "title": str(item.get("title", ""))[:1000],
                "body": str(item.get("body", ""))[:4000],
                "href": str(item.get("href", ""))[:2000],
            }
            for item in DDGS(timeout=8).text(
                query,
                region="ua-uk",
                safesearch="moderate",
                max_results=max_results,
                backend=backend,
            )
        ][:max_results]

    @staticmethod
    def _short_text(text: str, limit: int) -> str:
        text = html.unescape(re.sub(r"<[^>]+>", " ", str(text)))
        text = re.sub(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", " ", text)
        text = " ".join(text.split())
        if len(text) > limit:
            text = text[:limit].rsplit(" ", 1)[0].rstrip(".,;: ") + "…"
        return text

    @classmethod
    def clean_results(cls, results: list[dict], query: str = "", *, limit: int = 3) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 5:
            raise ValueError("Search result limit must be between 1 and 5")
        cleaned = []
        seen = set()
        for item in results[:20]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("href", "")).strip()
            try:
                parsed = urlsplit(url)
                if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username:
                    continue
            except ValueError:
                continue
            if url in seen:
                continue
            seen.add(url)
            cleaned.append(
                {
                    "title": cls._short_text(item.get("title", ""), 100),
                    "body": cls._short_text(item.get("body", ""), 220),
                    "href": url,
                    "domain": parsed.hostname,
                    "_rank_text": cls._short_text(item.get("title", ""), 1000) + " " + cls._short_text(item.get("body", ""), 4000),
                }
            )
        terms = query_terms(query)
        if terms:
            # Rare query terms distinguish the requested object/condition from
            # broad topic matches. This is retrieval, not semantic validation;
            # the summary's coverage check still evaluates the whole question.
            weights = {term: 1 + math.log((len(cleaned) + 1) / (1 + sum(
                term in item["_rank_text"].casefold() for item in cleaned))) for term in terms}
            cleaned.sort(key=lambda item: -sum(
                weights[term] * (2 * (term in item["title"].casefold()) + (term in item["_rank_text"].casefold()))
                for term in terms))
        for item in cleaned:
            item.pop('_rank_text')
        return cleaned[:limit]

    @staticmethod
    def summarize(results: list[dict]) -> str:
        if not results:
            return "Пошук не дав придатних результатів. Уточніть запит."
        first = results[0]
        return (
            f"Знайдено ресурс: {first['title'] or first['domain']}. "
            f"Короткий уривок пошуку: {first['body'] or 'Опис відсутній'}. "
            "Посилання на джерела виведено в консолі."
        )


def worker_main():
    """One bounded public search; no config/vault/history or shell commands."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    logging.disable(logging.CRITICAL)
    try:
        request = json.loads(sys.stdin.buffer.read(10000))
        query, limit = request["query"], request["max_results"]
        if not isinstance(query, str) or not query.strip() or len(query) > 600:
            raise ValueError
        if type(limit) is not int or not 1 <= limit <= 5:
            raise ValueError
        backend = request.get("backend", "auto")
        if backend not in {"auto", "duckduckgo"}:
            raise ValueError
        result = {"rows": WebSearchService._search(query, limit, backend)}
    except Exception as exc:
        result = {"error": search_failure_reason(exc)}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    worker_main()
