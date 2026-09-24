from __future__ import annotations

import re

from core.models import SkillResult


def can_handle(command, services):
    return bool(
        re.match(
            r"^(?:запиши|створи|прочитай|редагуй|знайди|познач|видали)\s+.*нотат",
            command,
        )
    )


async def handle(command, context, services):
    notes = services["notes"]
    raw_command = getattr(context, "raw_text", "") or command

    if "прочитай останню" in command:
        note = notes.latest()
        return SkillResult(True, note["text"] if note else "Нотаток немає.", {"command_type": "note_read"})

    match = re.match(
        r"(?:запиши|створи)\s+нотатк\w*\s+(.+?)\s*:\s*(.+)",
        raw_command,
        re.IGNORECASE,
    )
    if match:
        title, text = match.groups()
        notes.add(title, text)
        return SkillResult(True, f"Нотатку «{title}» збережено.", {"command_type": "note_create"})

    match = re.match(
        r"редагуй\s+нотатк\w*\s+(.+?)\s*:\s*(.+)",
        raw_command,
        re.IGNORECASE,
    )
    if match:
        title, text = match.groups()
        ok = notes.edit(title, text)
        return SkillResult(True, "Нотатку оновлено." if ok else "Нотатку не знайдено.", {"command_type": "note_edit"})

    if command.startswith("знайди"):
        query = re.split(r"нотат", raw_command, maxsplit=1, flags=re.IGNORECASE)[-1]
        query = query.strip(" киуКИУ")
        found = notes.search(query)
        response = "; ".join(f"{item['title']}: {item['text']}" for item in found[:5])
        return SkillResult(True, response or "Нотаток не знайдено.", {"command_type": "note_search"})

    if command.startswith("познач"):
        query = re.split(r"нотат", raw_command, maxsplit=1, flags=re.IGNORECASE)[-1]
        query = re.sub(r"як виконану", "", query, flags=re.IGNORECASE).strip(" киуКИУ")
        return SkillResult(True, "Позначено." if notes.mark_completed(query) else "Нотатку не знайдено.")

    if command.startswith("видали"):
        query = re.split(r"нотат", raw_command, maxsplit=1, flags=re.IGNORECASE)[-1]
        query = query.strip(" киуКИУ")
        if not await context.confirm(f"видалення нотатки {query}"):
            return SkillResult(True, "")
        count = notes.delete(query)
        return SkillResult(True, f"Видалено нотаток: {count}.", {"command_type": "note_delete"})

    return SkillResult(True, "Уточніть дію з нотаткою.")
