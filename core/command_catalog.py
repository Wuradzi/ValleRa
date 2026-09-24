"""Shared data-only capability catalogue; never generates executable code."""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import json
import re


@dataclass(frozen=True)
class Capability:
    tool: str
    skill: str
    example: str
    arguments: tuple[tuple[str, str], ...] | None = None
    guidance: str = ""
    aliases: tuple[str, ...] = ()


CATALOG = (
    Capability("find_files", "files", "знайди файл <назва>", (("query", "частина назви файла"), ("extension", ".pdf")),
               "Пошук за назвою, не вмістом; extension: порожнє, .pdf, .docx, .txt, .md, .xlsx, .pptx, .odt, .png, .jpg, .jpeg, .webp, .bmp. Якщо тип невідомий — порожнє розширення; не вимагай його. Після пошуку локальна система запропонує відкрити знайдений файл. Не вигадуй шлях."),
    Capability("open_app", "apps", "відкрий <програму>", (("name", "назва встановленої програми"),),
               "Лише назва програми або браузер; не шлях, URL, аргументи чи shell-команда."),
    Capability("window_control", "windows", "розгорни вікно <програма>",
               (("name", "назва програми або заголовок вікна"), ("action", "maximize")),
               "Керування наявним вікном: action maximize (розгорни), minimize (згорни), restore (віднови). Не запускати програму замість керування вікном. Без закриття чи завершення процесів. Якщо назви немає — уточни її; не питай дозволу, його запитає виконавець."),
    Capability("weather", "web", "погода в місті <місто>", (("city", "назва міста"), ("period", "now")),
               "period: now, today, tomorrow. Місто має бути явно вказане користувачем."),
    Capability("web_search", "web", "знайди в інтернеті <запит>", (("query", "запит користувача для вебпошуку"),),
               "Зберігай тему, обмеження, заперечення та числа користувача."),
    Capability("prepare_workplace", "workplace", "режим робота", (),
               "Лише запуск збереженого робочого місця з окремим планом і підтвердженням. Не додавати програми чи параметри.",
               ("режим робота", "увімкни режим робота", "активуй режим робота", "підготуй робоче місце", "підготуй мене до роботи")),
    Capability("workplace_status", "workplace", "статус завдання", (), "Лише читання останнього локального результату робочого сценарію."),
    Capability("workplace_retry", "workplace", "повтори невдалий крок", (), "Повтор лише непідтверджених кроків останнього робочого сценарію, з новим підтвердженням."),
    Capability("workplace_configure", "workplace", "налаштуй робоче місце: <до трьох програм через кому>"),
    Capability("web_followup", "web", "уточни пошук <питання>"),
    Capability("clock", "system", "котра година"),
    Capability("shutdown", "system", "заверши роботу"),
    Capability("notes", "notes", "створи нотатку <назва>: <текст>"),
    Capability("reminders", "reminders", "нагадай <завдання та час>"),
    Capability("diagnostics", "diagnostics", "проведи самодіагностику"),
    Capability("conversation", "conversation", "нова розмова"),
    Capability("microphone", "audio", "перевір мікрофон"),
    Capability("media", "media", "зменш гучність"),
    Capability("memory", "memory", "що ти пам'ятаєш"),
    Capability("metrics", "metrics", "покажи метрики"),
    Capability("audit_status", "pentest", "статус душогуба"),
    Capability("help", "help", "список команд"),
)


def available(enabled=None):
    return tuple(item for item in CATALOG if enabled is None or item.skill in enabled)


def command_help(enabled=None):
    return "; ".join(item.example for item in available(enabled))


def chat_catalog(enabled=None):
    return ("Локальні можливості цієї сесії: " + command_help(enabled) +
            ". Це фрази після слова Команда. Не вигадуй інших команд і не оголошуй їх виконаними. "
            "Статус і повтор стосуються останнього робочого завдання, контекст живе 5 хвилин; повтор потребує підтвердження.")


def intent_catalog(enabled=None):
    rows = [json.dumps({"tool": item.tool, "arguments": dict(item.arguments)}, ensure_ascii=False) + "\n" + item.guidance
            for item in available(enabled) if item.arguments is not None]
    return "\n".join(rows + ['{"tool":"unsupported","arguments":{}}'])


INTENT_FIELDS = {item.tool: {name for name, _ in item.arguments} for item in CATALOG if item.arguments is not None}
INTENT_FIELDS["unsupported"] = set()
TOOL_SKILLS = {item.tool: item.skill for item in CATALOG if item.arguments is not None}


def local_intent_candidates(text, enabled):
    """Whole short phrases only. No fuzzy application names, payloads or permissions."""
    text = " ".join(re.findall(r"[\w'’]+", text.casefold()))
    words = text.split()
    if not 2 <= len(words) <= 5 or any(word in {"не", "ні", "без", "що", "як", "чи"} for word in words):
        return []
    scores = []
    for item in available(enabled):
        if not item.aliases:
            continue
        # Do not turn a different verb or an appended action into an activation.
        eligible = [alias for alias in item.aliases if len(alias.split()) == len(words)
                    and all(SequenceMatcher(None, word, expected).ratio() >= .6
                            for word, expected in zip(words, alias.split()))]
        if not eligible:
            continue
        score = max(SequenceMatcher(None, text, alias).ratio() for alias in eligible)
        if score >= .8:
            scores.append((score, item.tool))
    scores.sort(reverse=True)
    return [tool for score, tool in scores if score >= scores[0][0] - .08] if scores else []
