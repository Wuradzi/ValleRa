from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone

from core.models import SkillResult


def can_handle(command, services):
    return command.startswith((
        "нагадай ",
        "нагадуй ",
        "покажи нагадування",
        "скасуй нагадування",
        "виконано ",
    ))


def parse_due(command: str):
    import dateparser

    return dateparser.parse(
        command,
        languages=["uk"],
        settings={
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TIMEZONE": "Europe/Kyiv",
            "TO_TIMEZONE": "UTC",
        },
    )


async def handle(command, context, services):
    store = services["reminders"]
    raw_command = getattr(context, "raw_text", "") or command

    if "покажи нагадування" in command:
        items = [item for item in store.all() if item["status"] in {"pending", "missed"}]
        response = "; ".join(f"{item['text']} — {item['due_at']}" for item in items[:10])
        return SkillResult(True, response or "Активних нагадувань немає.", {"command_type": "reminder_list"})

    if command.startswith("скасуй нагадування"):
        query = re.sub(
            r"^скасуй нагадування",
            "",
            raw_command,
            count=1,
            flags=re.IGNORECASE,
        ).strip()
        return SkillResult(True, f"Скасовано: {store.cancel(query)}.", {"command_type": "reminder_cancel"})

    if command.startswith("виконано"):
        query = re.sub(
            r"^виконано",
            "",
            raw_command,
            count=1,
            flags=re.IGNORECASE,
        ).strip()
        return SkillResult(True, "Позначено виконаним." if store.mark_completed(query) else "Не знайдено.")

    match = re.match(
        r"нагадай через\s+(\d+)\s*(хвилин\w*|годин\w*|секунд\w*)\s+(.+)",
        raw_command,
        re.IGNORECASE,
    )
    if match:
        amount, unit, text = match.groups()
        seconds = int(amount)
        if unit.startswith("хв"):
            seconds *= 60
        elif unit.startswith("год"):
            seconds *= 3600
        store.add(text, datetime.now(timezone.utc) + timedelta(seconds=seconds))
        return SkillResult(True, f"Нагадаю через {amount} {unit}.", {"command_type": "reminder_create"})

    recurrence = None
    if command.startswith("нагадуй щодня"):
        recurrence = "daily"
    elif command.startswith("нагадуй щопонеділка"):
        recurrence = "weekly"

    if command.startswith("нагадай") or command.startswith("нагадуй"):
        due = await asyncio.to_thread(parse_due, raw_command)
        if due is None:
            return SkillResult(True, "Не вдалося визначити дату. Повторіть точніше.")
        store.add(raw_command, due.astimezone(timezone.utc), recurrence=recurrence)
        return SkillResult(True, f"Нагадування створено на {due:%d.%m.%Y %H:%M}.", {"command_type": "reminder_create"})

    return SkillResult(False)
