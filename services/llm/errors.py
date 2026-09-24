"""Safe, user-facing failure categories; never include an API response body."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import re

import httpx


class ResponseError(RuntimeError):
    """A completed response without usable text, or an interrupted generation."""

    def __init__(self, kind: str = "empty_response", reason: str = "UNSPECIFIED"):
        self.kind = kind
        self.reason = reason
        self.diagnostics: dict[str, int] = {}
        self.empty_retry_safe = False
        super().__init__(kind)


@dataclass(frozen=True)
class Failure:
    kind: str
    message: str
    retryable: bool = False
    allow_fallback: bool = True
    status: int | None = None
    reason: str = "UNSPECIFIED"
    retry_after_seconds: float | None = None


def _seconds(value) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _rate_limit_metadata(exc: Exception) -> tuple[float | None, str]:
    """Read only structured retry/quota fields, never free-form error text."""
    delays = []
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    retry_after = headers.get("Retry-After") or headers.get("retry-after")
    delay = _seconds(retry_after)
    if delay is None and isinstance(retry_after, str):
        try:
            date = parsedate_to_datetime(retry_after)
            if date.tzinfo is not None:
                delay = max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            pass
    if delay is not None:
        delays.append(delay)

    body = getattr(exc, "details", None)
    if isinstance(body, dict):
        body = body.get("error", body)
        body = body.get("details", []) if isinstance(body, dict) else []
    reason = "UNSPECIFIED"
    for detail in body if isinstance(body, list) else []:
        if not isinstance(detail, dict):
            continue
        if detail.get("@type") == "type.googleapis.com/google.rpc.RetryInfo":
            duration = detail.get("retryDelay", "")
            if isinstance(duration, str) and re.fullmatch(r"\d+(?:\.\d{1,9})?s", duration):
                delay = _seconds(duration[:-1])
                if delay is not None:
                    delays.append(delay)
        elif detail.get("@type") == "type.googleapis.com/google.rpc.QuotaFailure":
            violations = detail.get("violations", [])
            for violation in violations if isinstance(violations, list) else []:
                if not isinstance(violation, dict):
                    continue
                quota_id = violation.get("quotaId", "")
                if isinstance(quota_id, str):
                    if "PerDay" in quota_id:
                        reason = "DAILY_QUOTA"
                    elif "PerMinute" in quota_id and reason != "DAILY_QUOTA":
                        reason = "MINUTE_QUOTA"
    return max(delays) if delays else None, reason


def classify_failure(exc: Exception) -> Failure:
    if isinstance(exc, ResponseError):
        messages = {
            "empty_response": "Модель повернула відповідь без тексту.",
            "response_blocked": "Сервіс обмежив відповідь на цю репліку.",
            "output_limit": "Модель вичерпала ліміт довжини відповіді.",
            "unsupported_response": "Модель завершила відповідь без придатного тексту.",
        }
        return Failure(
            exc.kind, messages[exc.kind], allow_fallback=False, reason=exc.reason,
        )

    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    status = int(code) if isinstance(code, (int, str)) and str(code).isdigit() else None
    if status == 429:
        delay, reason = _rate_limit_metadata(exc)
        message = (
            "Сервіс повідомив про вичерпання добової квоти."
            if reason == "DAILY_QUOTA"
            else "Сервіс повідомив про ліміт запитів або квоти."
        )
        return Failure("rate_limit", message, status=status, reason=reason, retry_after_seconds=delay)
    if status in {401, 403}:
        return Failure("access_denied", "Сервіс відхилив доступ. Перевірте API-ключ і дозволи.", status=status)
    if status in {400, 404, 422}:
        return Failure("invalid_request", "Сервіс відхилив запит. Перевірте налаштування моделі.", status=status)
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)) or isinstance(
        exc.__cause__, (TimeoutError, httpx.TimeoutException),
    ):
        return Failure("timeout", "Не вдалося дочекатися відповіді моделі.", retryable=True)
    if status in {408, 500, 502, 503, 504}:
        return Failure("server_error", "Сервіс мовної моделі тимчасово не відповідає.", retryable=True, status=status)
    if isinstance(exc, (ConnectionError, OSError, httpx.TransportError)):
        return Failure("connection_error", "Перервався зв'язок із мовною моделлю.", retryable=True)
    return Failure("provider_error", "Не вдалося отримати відповідь мовної моделі.")
