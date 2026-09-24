from __future__ import annotations

import os
import re
from collections.abc import Mapping


SENSITIVE_ENV_MARKERS = (
    "API_KEY",
    "APIKEY",
    "PASSWORD",
    "PASSWD",
    "SECRET",
    "TOKEN",
    "PRIVATE_KEY",
    "ACCESS_KEY",
)
CONFIGURED_SECRET_ENV_NAMES = {
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
}

_SECRET_COMMAND = re.compile(
    r"(пароль\s+від\s+.+?\s+(?:це|:)\s*)(.+)$",
    re.IGNORECASE,
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"\b([A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD))\s*=\s*([^\s,;]+)",
    re.IGNORECASE,
)


def redact_user_text(text: str) -> str:
    redacted = _SECRET_COMMAND.sub(r"\1[REDACTED]", text)
    return _CREDENTIAL_ASSIGNMENT.sub(r"\1=[REDACTED]", redacted)


def is_sensitive_env_name(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in SENSITIVE_ENV_MARKERS)


def scrub_sensitive_environment() -> None:
    for name in CONFIGURED_SECRET_ENV_NAMES:
        os.environ.pop(name, None)


def sanitized_environment(
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not is_sensitive_env_name(name)
    }
    if extra:
        environment.update({str(name): str(value) for name, value in extra.items()})
    return environment
