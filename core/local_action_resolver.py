"""Backend-neutral exact action candidates. Never grants or executes actions."""
import asyncio
from collections.abc import Callable

from core.command_intent import CommandIntent
from core.natural_turn import exact_open_request


async def resolve_exact_app(request: str, lookup: Callable[[str], bool] | None,
                            *, apps_enabled: bool) -> CommandIntent | None:
    name = exact_open_request(request)
    if not name or not apps_enabled or not callable(lookup):
        return None
    try:
        known = await asyncio.wait_for(asyncio.to_thread(lookup, name), timeout=.25)
    except (TimeoutError, OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    # Truthy values from adapters/mocks are not authoritative catalogue matches.
    return CommandIntent('open_app', {'name': name}) if known is True else None
