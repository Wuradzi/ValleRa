from __future__ import annotations

import asyncio
import re

import pyperclip

from core.models import SkillResult


def can_handle(command, services):
    return command.startswith((
        "запам'ятай",
        "забудь ",
        "очисти пам",
        "покажи всю пам",
        "що ти пам'ятаєш",
        "пароль від ",
    ))


async def clear_clipboard_later(expected_value: str):
    await asyncio.sleep(30)
    if pyperclip.paste() == expected_value:
        pyperclip.copy("")


async def handle(command, context, services):
    memory = services["memory"]
    secrets = services["secrets"]
    raw_command = getattr(context, "raw_text", "") or command

    if command.startswith("очисти пам"):
        if await context.confirm("повне очищення локальної пам'яті"):
            memory.clear()
            return SkillResult(True, "Пам'ять очищено.", {"command_type": "memory_clear"})
        return SkillResult(True, "")

    if "покажи всю пам" in command or "що ти пам'ятаєш" in command:
        items = memory.all()
        if not items:
            return SkillResult(True, "Пам'ять порожня.")
        return SkillResult(
            True,
            "; ".join(f"{item['key']}: {item['value']}" for item in items[:10]),
            {"command_type": "memory_list"},
        )

    if command.startswith("забудь"):
        query = re.sub(r"^забудь\s+", "", command).strip()
        count = memory.forget(query)
        return SkillResult(True, f"Видалено записів: {count}.", {"command_type": "memory_forget"})

    if "пароль від" in command and not command.startswith("запам"):
        key = re.split(
            r"пароль від",
            raw_command,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[1].strip()
        if not await context.confirm(f"доступ до пароля {key}"):
            return SkillResult(True, "")
        value = secrets.get(key)
        if value is None:
            return SkillResult(True, "Пароль не знайдено.")
        pyperclip.copy(value)
        asyncio.create_task(clear_clipboard_later(value))
        return SkillResult(
            True,
            "Пароль скопійовано в буфер обміну на 30 секунд.",
            {"command_type": "secret_read"},
        )

    if command.startswith("запам"):
        content = re.sub(
            r"^запам['’]ятай[,\s]*",
            "",
            raw_command,
            flags=re.IGNORECASE,
        ).strip()
        if re.search(r"пароль від", content, re.IGNORECASE):
            if context.source == "voice":
                return SkillResult(
                    True,
                    "Секрети не приймаються голосом. Введіть цю команду текстом.",
                    {"command_type": "secret_write_rejected"},
                )
            match = re.match(
                r"пароль від\s+(.+?)\s+(?:це|:)\s+(.+)",
                content,
                re.IGNORECASE,
            )
            if not match:
                return SkillResult(True, "Скажіть: запам'ятай пароль від сервісу це значення.")
            key, value = match.groups()
            if not await context.confirm(f"збереження пароля для {key}"):
                return SkillResult(True, "")
            secrets.set(key, value)
            return SkillResult(True, "Секрет збережено.", {"command_type": "secret_write"})

        match = re.match(
            r"(?:що\s+)?(.+?)\s+(?:це|:)\s+(.+)",
            content,
            re.IGNORECASE,
        )
        if not match:
            return SkillResult(True, "Скажіть: запам'ятай, що ключ це значення.")
        key, value = match.groups()
        memory.remember(key, value)
        return SkillResult(True, "Запам'ятав.", {"command_type": "memory_write"})

    return SkillResult(False)
