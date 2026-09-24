import asyncio
import logging

from core.models import SkillResult

logger = logging.getLogger(__name__)


ACTIVATE = {
    "режим спілкування",
    "активуй режим спілкування",
    "активуй режим простого спілкування",
    "увімкни режим спілкування",
    "увімкни режим простого спілкування",
    "почни спілкування",
    "давай поговоримо",
}

DEACTIVATE = {
    "завершити режим спілкування",
    "завершити просте спілкування",
    "вимкни режим спілкування",
    "досить спілкування",
}

NEW_CONVERSATION = {"нова розмова", "почни нову розмову"}


def can_handle(command, services):
    return command in ACTIVATE or command in DEACTIVATE or command in NEW_CONVERSATION


async def handle(command, context, services):
    state = services["state"]
    if command in NEW_CONVERSATION:
        if state.get("mode", "chat") != "chat":
            return SkillResult(True, "Спочатку завершіть поточний спеціальний режим.")
        if not await context.confirm(
            "початок нової розмови без попередніх реплік і резюме. "
            "Стара історія залишиться в локальному архіві; збережені факти пам'яті не зміняться"
        ):
            return SkillResult(True, "Нову розмову скасовано. Поточну історію збережено.")
        try:
            await asyncio.to_thread(services["llm"].new_conversation)
        except OSError as exc:
            logger.error("New conversation storage failure type=%s", type(exc).__name__)
            return SkillResult(True, "Не вдалося почати нову розмову. Перевірте доступ до сховища.")
        if services.get("web_answers") is not None:
            services["web_answers"].clear()
        return SkillResult(
            True,
            "Починаємо нову розмову. Попередні репліки й резюме більше не додаються до діалогу. "
            "Стару історію збережено локально; збережені факти пам'яті залишилися.",
            {"command_type": "conversation_new"},
        )
    if command in ACTIVATE:
        state["mode"] = "chat"
        return SkillResult(
            True,
            "Режим спілкування активовано. Для локальної дії почніть фразу словом «Команда».",
            {"command_type": "chat_mode_enable"},
        )
    if command in DEACTIVATE:
        state["mode"] = "chat"
        return SkillResult(
            True,
            "Спілкування є основним режимом. Для локальної дії скажіть «Команда».",
            {"command_type": "chat_mode_disable"},
        )
    return SkillResult(False)
