from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta

from core.models import SkillResult


def search_result(files, found, services):
    status = getattr(files, "search_status", {})
    status = status if isinstance(status, dict) else {}
    resumable = status.get("resumable", False)
    if found:
        result = (services["tasks"].offer("file", found, command_type="file_search") if services.get("tasks") is not None
                  else SkillResult(True, "; ".join(str(path) for path in found[:5]), {"command_type": "file_search"}))
    else:
        response = "У переглянутій частині файлів із такою назвою поки не знайдено." if resumable else "Файлів із такою назвою не знайдено в доступних каталогах."
        result = SkillResult(True, response, {"command_type": "file_search"})
    if resumable:
        result.response += " Пошук ще неповний. Повторіть той самий запит протягом п'яти хвилин, щоб продовжити обхід."
    elif status.get("skipped", 0):
        result.response += " Частину шляхів пропущено через недоступність або посилання."
    result.data["search_status"] = status
    result.data["status"] = "partial" if resumable else "found" if found else "not_found"
    return result


def can_handle(command, services):
    return bool(
        re.match(
            r"^(?:знайди|покажи|відкрий|видали)\s+.*(?:файл|pdf|документ)",
            command,
        )
    )


async def handle(command, context, services):
    files = services["files"]
    raw_command = getattr(context, "raw_text", "") or command

    if "найбільш" in command:
        found = await asyncio.to_thread(files.largest, 10)
        rows = []
        for path in found:
            try:
                rows.append(f"{path.name} — {path.stat().st_size // 1024 // 1024} МБ")
            except OSError:
                continue
        response = "; ".join(rows)
        status = getattr(files, "search_status", {})
        if isinstance(status, dict) and status.get("resumable"):
            response = "Найбільші серед цієї частини переглянутих файлів: " + (response or "збігів поки немає")
            response += ". Обхід неповний; повторіть той самий запит для продовження."
        return SkillResult(True, response or "Файлів не знайдено.", {"command_type": "file_largest"})

    extension = ".pdf" if "pdf" in command else None
    match = re.search(
        r"(?:словом|назвою)\s+(.+)",
        raw_command,
        re.IGNORECASE,
    )
    basic = re.fullmatch(
        r"(?:знайди|покажи|відкрий|видали)\s+(?:файл(?:и)?|документ(?:и)?|pdf(?:\s+файл(?:и)?)?)"
        r"(?:\s+(?:(?:про|за назвою|з назвою|зі словом)\s+)?(.+))?",
        raw_command.strip(" .!?,;"), re.I,
    )
    if match:
        query = match.group(1).strip()
    elif basic:
        query = (basic.group(1) or "").strip()
    elif "учора" in command or "останн" in command:
        query = ""
    else:
        return SkillResult(False)
    modified_after = None
    if "учора" in command:
        modified_after = datetime.now() - timedelta(days=1)
    elif "останн" in command:
        modified_after = datetime.now() - timedelta(days=30)

    found = await asyncio.to_thread(files.search, query, extension, modified_after, limit=5)
    if not found:
        return search_result(files, found, services)

    if command.startswith("відкрий"):
        if services.get("tasks") is not None:
            status = getattr(files, "search_status", {})
            if isinstance(status, dict) and status.get("resumable"):
                return search_result(files, found, services)
            if len(found) > 1:
                return services["tasks"].offer("file", found, command_type="file_choice")
            return await services["tasks"].open_file(found[0], context)
        try:
            await asyncio.to_thread(files.open, found[0])
        except Exception as exc:
            return SkillResult(True, f"Не вдалося відкрити {found[0].name}: {exc}")
        return SkillResult(
            True,
            f"Команду відкриття {found[0].name} передано системі.",
            {"command_type": "file_open"},
        )

    if command.startswith("видали"):
        if not files.is_writable(found[0]):
            return SkillResult(True, "Файл знайдено, але він поза каталогами, дозволеними для змін. Доступ пошуку не дає права видалення.",
                               {"command_type": "file_delete", "accepted": False, "success": False})
        if not await context.confirm(f"переміщення файлу {found[0].name} у кошик"):
            return SkillResult(True, "")
        await asyncio.to_thread(files.delete_to_trash, found[0])
        return SkillResult(True, "Файл переміщено у кошик.", {"command_type": "file_delete"})

    return search_result(files, found, services)


async def find_files(query, extension, context):
    found = await asyncio.to_thread(context.services["files"].search, query, extension or None, limit=5)
    return search_result(context.services["files"], found, context.services)
