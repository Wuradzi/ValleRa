"""Request-local numeric telemetry; never retain URLs, headers or text."""
from __future__ import annotations

from contextvars import ContextVar
import logging
from time import perf_counter

logger = logging.getLogger(__name__)
CURRENT_REQUEST = ContextVar("gemini_request_timing", default=None)


class RequestTiming:
    EVENTS = frozenset({"connection.connect_tcp", "connection.start_tls",
                        "http11.receive_response_headers", "http2.receive_response_headers"})

    def __init__(self, messages):
        self.started = perf_counter()
        self.fields = {"messages": len(messages),
                       "input_chars": sum(len(item.get("content", "")) for item in messages)}
        self.events = {}
        self.pending = {}

    def mark(self, name):
        self.fields.setdefault(name + "_ms", round((perf_counter() - self.started) * 1000, 2))

    async def trace(self, name, info):
        event, _, status = name.rpartition(".")
        if event not in self.EVENTS:
            return
        if status == "started":
            self.pending[event] = perf_counter()
        elif status in {"complete", "failed"} and event in self.pending:
            duration = round((perf_counter() - self.pending.pop(event)) * 1000, 2)
            self.events[event + "." + status] = duration

    def observe(self, response):
        usage = getattr(response, "usage_metadata", None)
        for field in ("prompt_token_count", "candidates_token_count", "thoughts_token_count"):
            value = getattr(usage, field, None)
            if type(value) is int and value >= 0:
                self.fields[field] = value

    def finish(self, status):
        self.mark("total")
        logger.info("Gemini request status=%s timing=%s transport=%s", status, self.fields, self.events)


async def attach_timing(request):
    timing = CURRENT_REQUEST.get()
    if timing is not None:
        request.extensions["trace"] = timing.trace


async def traced(timing, awaitable):
    # Keep the ContextVar inside one coroutine, never across an async-generator
    # yield: aclose() may be called by a different task/context.
    token = CURRENT_REQUEST.set(timing)
    try:
        return await awaitable
    finally:
        CURRENT_REQUEST.reset(token)
