from __future__ import annotations

import asyncio
import ctypes
import subprocess
from datetime import datetime

import psutil

from core.models import SkillResult


TIME_COMMANDS = {"котра година", "який час", "скажи час"}
DATE_COMMANDS = {"яка дата", "скажи дату", "який сьогодні день"}
SYSTEM_INFO_COMMANDS = {
    "навантаження процесора",
    "стан процесора",
    "стан cpu",
    "стан системи",
}


def can_handle(command, services):
    return (
        command in TIME_COMMANDS
        or command in DATE_COMMANDS
        or command in SYSTEM_INFO_COMMANDS
        or command in {
            "вимкни комп'ютер",
            "скасуй вимкнення",
            "заблокуй екран",
            "заверши роботу",
            "заверши роботу валери",
            "вимкни валеру",
        }
    )


async def handle(command, context, services):
    now = datetime.now()
    if command in TIME_COMMANDS:
        return SkillResult(True, f"Зараз {now:%H:%M}.", {"command_type": "time"})
    if command in DATE_COMMANDS:
        return SkillResult(True, f"Сьогодні {now:%d.%m.%Y}.", {"command_type": "date"})
    if command in SYSTEM_INFO_COMMANDS:
        cpu = await asyncio.to_thread(psutil.cpu_percent, 0.5)
        ram = psutil.virtual_memory().percent
        return SkillResult(
            True,
            f"Навантаження процесора {cpu:.0f} відсотків, пам'яті {ram:.0f} відсотків.",
            {"command_type": "system_info"},
        )
    if command in {"заверши роботу", "заверши роботу валери", "вимкни валеру"}:
        return SkillResult(
            True,
            "Завершую роботу Валери.",
            {"command_type": "assistant_shutdown", "shutdown_app": True},
        )
    if command == "скасуй вимкнення":
        completed = await asyncio.to_thread(
            subprocess.run,
            ["shutdown", "/a"],
            check=False,
            capture_output=True,
        )
        response = (
            "Вимкнення скасовано."
            if completed.returncode == 0
            else "Не вдалося скасувати вимкнення."
        )
        return SkillResult(True, response, {"command_type": "shutdown_cancel"})
    if command == "вимкни комп'ютер":
        if not await context.confirm("вимкнення комп'ютера через 60 секунд"):
            return SkillResult(True, "")
        completed = await asyncio.to_thread(
            subprocess.run,
            ["shutdown", "/s", "/t", "60"],
            check=False,
            capture_output=True,
        )
        response = (
            "Комп'ютер буде вимкнено через 60 секунд."
            if completed.returncode == 0
            else "Windows не прийняла команду вимкнення."
        )
        return SkillResult(True, response, {"command_type": "shutdown"})
    if command == "заблокуй екран":
        if not await context.confirm("блокування екрана"):
            return SkillResult(True, "")
        locked = bool(await asyncio.to_thread(ctypes.windll.user32.LockWorkStation))
        return SkillResult(
            True,
            "Екран заблоковано." if locked else "Windows не підтвердила блокування екрана.",
            {"command_type": "lock"},
        )
    return SkillResult(False)
