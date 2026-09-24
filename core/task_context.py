"""Short-lived, local selections. The model never supplies executable paths."""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path

from core.models import SkillResult

ORDINALS = {
    "перший": 1, "перша": 1, "першу": 1, "перше": 1, "один": 1,
    "другий": 2, "друга": 2, "другу": 2, "друге": 2, "два": 2,
    "третій": 3, "третя": 3, "третю": 3, "третє": 3, "три": 3,
    "четвертий": 4, "четверта": 4, "четверту": 4, "четверте": 4, "чотири": 4,
    "п'ятий": 5, "п’ятий": 5, "п'ята": 5, "п’ята": 5, "п'яте": 5, "п’яте": 5, "п'ять": 5, "п’ять": 5,
    "п'яту": 5, "п’яту": 5,
}
CHOICE = re.compile(r"^(?:(?:відкрий|обери|вибери)\s+)?(?:номер\s+)?"
                    r"(\d{1,2}|" + "|".join(ORDINALS) + r")(?:\s+(?:файл|документ|варіант|програму|вікно))?$", re.I)
CANCEL = {"скасувати", "скасуй", "не треба", "ні"}
AFFIRM = {"так", "гаразд", "добре", "відкрий", "відкривай", "так відкрий", "відкрий файл", "відкрий його", "підтверджую"}
DOCUMENT_TYPES = {".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".txt", ".md", ".csv", ".rtf", ".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def file_stamp(path):
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
        return (str(resolved), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    except OSError:
        return None


def selection_text(text: str) -> str:
    text = re.sub(r"^команда\b[\s:,-]*", "", text.strip(), flags=re.I)
    return " ".join(text.lower().strip(" \t.!?,;").split())


@dataclass
class Selection:
    kind: str
    entries: list
    expires_at: float
    stamps: list | None = None


class TaskContext:
    def __init__(self, ttl_seconds=120, clock=time.monotonic):
        self.ttl_seconds, self.clock = ttl_seconds, clock
        self.pending: Selection | None = None

    def is_selection_reply(self, text: str) -> bool:
        return self.pending is not None and bool(
            CHOICE.fullmatch(selection_text(text)) or selection_text(text) in CANCEL | AFFIRM
        )

    def clear(self):
        self.pending = None

    def offer(self, kind: str, entries: list, *, command_type: str) -> SkillResult:
        if kind not in {"file", "app", "window"} or not entries:
            raise ValueError("Invalid selection")
        entries = [Path(item) if kind == "file" else dict(item) for item in entries[:5]]
        self.pending = Selection(kind, entries, self.clock() + self.ttl_seconds,
                                 [file_stamp(path) for path in entries] if kind == "file" else None)
        labels = [item.name if kind == "file" else item["name"] for item in entries]
        choices = [{"label": label, "location": str(item) if kind == "file" else label}
                   for label, item in zip(labels, entries)]
        if kind == "window":
            for choice, item in zip(choices, entries):
                choice["location"] = f"{Path(item['exe']).name}, PID {item['pid']}"
        listing = "; ".join(f"{index}. {label[:70]}" for index, label in enumerate(labels, 1))
        question = ("Відкрити цей файл? Скажіть так або ні." if kind == "file" and len(entries) == 1
                    else "Оберіть вікно за номером або скажіть скасувати." if kind == "window"
                    else "Який відкрити? Назвіть номер або скажіть скасувати.")
        return SkillResult(True, f"Знайдено: {listing}. {question}",
                           {"command_type": command_type, "task_choices": choices, "success": True, "status": "found"})

    async def consume(self, text: str, context) -> SkillResult | None:
        pending = self.pending
        if pending is None:
            return None
        normalized = selection_text(text)
        match = CHOICE.fullmatch(normalized)
        if normalized in CANCEL:
            self.clear()
            return SkillResult(True, "Вибір скасовано.", {"command_type": "task_cancelled"})
        affirmative = normalized in AFFIRM
        if not match and not affirmative:
            # A different request closes the old permission window.
            self.clear()
            return None
        if self.clock() >= pending.expires_at:
            self.clear()
            return SkillResult(True, "Час вибору минув. Повторіть команду пошуку.", {"command_type": "task_expired"})
        if affirmative and len(pending.entries) != 1:
            return SkillResult(True, "Знайдено кілька варіантів. Назвіть номер потрібного; поки нічого не відкриваю.",
                               {"command_type": "task_invalid_choice"})
        word = match.group(1) if match else "1"
        index = int(word) if word.isdigit() else ORDINALS[word]
        if not 1 <= index <= len(pending.entries):
            return SkillResult(True, f"Оберіть номер від 1 до {len(pending.entries)} або скажіть скасувати.",
                               {"command_type": "task_invalid_choice"})
        item = pending.entries[index - 1]
        self.clear()  # Consume once, including errors/cancellation. Never replay.
        if pending.kind == "file":
            if pending.stamps and (pending.stamps[index - 1] is None or file_stamp(item) != pending.stamps[index - 1]):
                return SkillResult(True, "Файл змінився або зник після пошуку. Повторіть пошук; нічого не відкрито.",
                                   {"command_type": "file_open", "accepted": False, "success": False})
            return await self.open_file(item, context)
        if pending.kind == "window":
            from skills.windows.skill import execute_window_target
            return await execute_window_target(item, context)
        return await self.open_app(item, context)

    @staticmethod
    async def open_file(path: Path, context) -> SkillResult:
        files = context.services["files"]
        if not await asyncio.to_thread(lambda: path.is_file() and files.is_allowed(path)):
            return SkillResult(True, "Файл більше не доступний у дозволених каталогах. Повторіть пошук.",
                               {"command_type": "file_open", "accepted": False})
        stamp = file_stamp(path)
        if path.suffix.lower() not in DOCUMENT_TYPES:
            if not await context.confirm(f"відкриття файлу «{path}»; цей тип може запускати програму"):
                return SkillResult(True, "Відкриття скасовано.", {"command_type": "task_cancelled"})
        try:
            # Recheck after any confirmation, including symlink replacement.
            if stamp is None or file_stamp(path) != stamp or not files.is_allowed(path):
                raise OSError("unavailable")
            accepted = await asyncio.to_thread(files.open, path)
            if accepted is False:
                raise OSError("not accepted")
        except (OSError, ValueError):
            return SkillResult(True, "Система не прийняла відкриття файлу. Перевірте його наявність і програму для цього типу.",
                               {"command_type": "file_open", "accepted": False, "success": False, "status": "failed"})
        return SkillResult(True, f"Команду відкриття {path.name} передано системі.",
                           {"command_type": "file_open", "accepted": True, "status": "submitted", "verified": False,
                            "verification": "shell_accepted_only"})

    @staticmethod
    async def open_app(app: dict, context) -> SkillResult:
        apps = context.services["apps"]
        matches = await asyncio.to_thread(apps.find, app["name"])
        current = next((item for item in matches if item.get("command") == app.get("command")), None)
        if current is None:
            return SkillResult(True, "Список програм змінився. Повторіть команду відкриття.",
                               {"command_type": "open_application", "accepted": False})
        accepted = bool(await asyncio.to_thread(apps.open, current))
        from skills.apps.skill import launch_result
        return await launch_result(apps, accepted, current["name"], current)
