from __future__ import annotations

import re
import asyncio

from core.models import SkillResult


def can_handle(command, services):
    if re.match(r"^(?:відкрий|запусти|увімкни)\s+.*\b(?:файл(?:и)?|документ(?:и)?|pdf)\b", command):
        return False
    return bool(
        re.match(r"^(?:відкрий|запусти|увімкни)(?:\s+.+)?$", command)
        or command.startswith("додай програму ")
        or command in {"переіндексуй програми", "онови список програм"}
    )


async def handle(command, context, services):
    raw_command = getattr(context, "raw_text", "") or command
    if "переіндексуй" in command:
        apps = await asyncio.to_thread(services["app_indexer"].rebuild)
        return SkillResult(True, f"Знайдено {len(apps)} програм.", {"command_type": "app_index"})

    manual = re.match(
        r".*додай програму\s+(.+?)\s+команда\s+(.+?)(?:\s+псевдоніми\s+(.+))?$",
        raw_command,
        re.IGNORECASE,
    )
    if manual:
        name, executable, aliases_raw = manual.groups()
        approved = await context.confirm(
            f"додавання програми «{name}» з командою запуску «{executable}»"
        )
        if not approved:
            return SkillResult(True, "Додавання програми скасовано.")
        aliases = [item.strip() for item in (aliases_raw or "").split(",") if item.strip()]
        await asyncio.to_thread(
            services["app_indexer"].add_manual,
            name,
            executable,
            aliases,
        )
        return SkillResult(True, f"Програму {name} додано.", {"command_type": "app_add"})

    query = re.sub(
        r"^(відкрий|запусти|увімкни)\s+",
        "",
        raw_command,
        flags=re.IGNORECASE,
    ).strip(" \t\r\n?.!,;:")
    if not query or query == raw_command:
        return SkillResult(True, "Уточніть повну назву програми в одній команді.")
    return await open_application(query, context, services)


async def open_application(query, context, services):
    """Open by indexed name only; never accept an executable from the model."""
    if query.lower() == "браузер":
        try:
            accepted = await asyncio.to_thread(services["apps"].open_default_browser)
        except Exception as exc:
            return SkillResult(True, f"Не вдалося запустити браузер: {exc}",
                               {"command_type": "open_browser", "accepted": False, "success": False})
        return await launch_result(services["apps"], accepted, "браузера", command_type="open_browser")
    matches = await asyncio.to_thread(services["apps"].find, query)
    if not matches:
        return SkillResult(
            True,
            f"Програму «{query}» не знайдено.",
            {"command_type": "app_not_found", "accepted": False, "success": False},
        )
    if len(matches) > 1 and matches[0]["score"] - matches[1]["score"] < 8:
        if services.get("tasks") is not None:
            return services["tasks"].offer("app", matches[:3], command_type="app_choice")
        names = ", ".join(app["name"] for app in matches[:3])
        return SkillResult(True, f"Знайдено кілька варіантів: {names}. Уточніть назву.")

    try:
        accepted = await asyncio.to_thread(services["apps"].open, matches[0])
    except Exception as exc:
        return SkillResult(True, f"Не вдалося запустити {matches[0]['name']}: {exc}",
                           {"command_type": "open_application", "accepted": False, "success": False})
    return await launch_result(services["apps"], accepted, matches[0]["name"], matches[0])


async def launch_result(apps, accepted, name, app=None, *, command_type="open_application"):
    data = {"command_type": command_type, "accepted": bool(accepted), "verified": False,
            "success": False, "status": "submitted" if accepted else "failed"}
    if not accepted:
        data["success"] = False
        return SkillResult(True, f"Система не підтвердила запуск {name}.", data)
    check = getattr(apps, "verify_launch", None)
    evidence = {}
    if callable(check):
        try:
            evidence = await asyncio.wait_for(asyncio.to_thread(check, app), 3)
        except Exception:
            pass  # Accepted launch with unknown evidence is not a verified success.
    if isinstance(evidence, dict) and evidence.get("window") and evidence.get("process"):
        data.update(verified=True, success=True, status="verified")
        return SkillResult(True, f"Процес і видиме вікно {name} знайдено.", data)
    response = f"Команду запуску {name} передано системі."
    if callable(check):
        response += (" Процес працює, але видимого вікна не підтверджено; можливо, програма у треї."
                     if isinstance(evidence, dict) and evidence.get("process")
                     else " Появу вікна не вдалося підтвердити; повторно не запускаю.")
    return SkillResult(True, response, data)
