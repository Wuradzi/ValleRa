from __future__ import annotations

import re
import asyncio
from pathlib import Path

from core.models import SkillResult
from core.confirmation import confirm_action


def can_handle(command, services):
    return command.startswith((
        "закрий ",
        "згорни ",
        "розгорни ",
        "віднови ",
        "заверши ",
        "вбий ",
    ))


def target(command: str) -> str:
    return re.sub(
        r"^(закрий|згорни|розгорни|віднови|заверши|вбий)\s+(вікно|програму|процес)?\s*",
        "",
        command,
    ).strip()


async def control_window(name, action, context):
    controller = context.services["windows"]
    aliases = []
    apps = context.services.get("apps")
    if apps is not None:
        matches = await asyncio.wait_for(asyncio.to_thread(apps.find, name), 5)
        # Aliases help Ukrainian program names, but never choose between windows.
        if matches and matches[0].get("score", 0) >= 90:
            aliases = [matches[0]["name"], *matches[0].get("aliases", [])]
    targets = await asyncio.wait_for(asyncio.to_thread(controller.candidates, name, aliases), 5)
    if not targets:
        return SkillResult(True, "Такого доступного вікна не знайдено. Програма може бути закрита або лише у треї; нічого не запускаю.",
                           {"command_type": "window_" + action, "accepted": False, "success": False, "status": "not_found"})
    entries = [{**item, "action": action} for item in targets]
    if len(entries) > 1:
        return context.services["tasks"].offer("window", entries, command_type="window_choice")
    return await execute_window_target(entries[0], context)


async def execute_window_target(target, context):
    if "windows" not in context.services.get("enabled_skills", set()):
        return SkillResult(True, "Керування вікнами вимкнено.", {"command_type": "window_unavailable", "success": False})
    action = target["action"]
    verb = {"maximize": "Розгорнути", "minimize": "Згорнути", "restore": "Відновити"}[action]
    details = f"{verb} вікно «{target['name']}», програма {Path(target['exe']).name}, PID {target['pid']}"
    cancelled = await confirm_action(context, details, f"{verb} вікно «{target['name'][:100]}»")
    if cancelled is not None:
        return cancelled
    try:
        evidence = await asyncio.wait_for(asyncio.to_thread(context.services["windows"].change_target, target, action), 4)
    except Exception:
        evidence = {"accepted": True, "verified": False, "status": "unverified"}
    data = {**evidence, "command_type": "window_" + action, "success": bool(evidence["verified"])}
    if evidence["verified"]:
        response = {"maximize": "Вікно розгорнуто.", "minimize": "Вікно згорнуто.", "restore": "Вікно відновлено."}[action]
        if action != "minimize" and not evidence.get("foreground"):
            response += " Перехід на передній план не підтверджено."
    elif not evidence["accepted"]:
        response = "Вікно змінилося або вже недоступне. Повторіть запит; інше вікно не обираю."
    else:
        response = "Стан вікна не вдалося підтвердити. Автоматично не повторюю дію."
    return SkillResult(True, response, data)


async def handle(command, context, services):
    name = target(command)
    if not name:
        return SkillResult(True, "Уточніть назву вікна або програми.")

    controller = services["windows"]
    for verb, action in (("згорни", "minimize"), ("розгорни", "maximize"), ("віднови", "restore")):
        if command.startswith(verb):
            return await control_window(name, action, context)
    if command.startswith("закрий"):
        count = controller.close(name)
        return SkillResult(True, f"Закрито вікон: {count}.", {"command_type": "window_close"})
    if command.startswith("заверши") or command.startswith("вбий"):
        if not await context.confirm(f"примусове завершення процесу {name}"):
            return SkillResult(True, "")
        count = controller.terminate_processes(name)
        return SkillResult(True, f"Завершено процесів: {count}.", {"command_type": "process_terminate"})
    return SkillResult(False)
