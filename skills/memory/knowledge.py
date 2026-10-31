"""Explicit knowledge-base phrases: add, list, forget, clear. Changes need confirmation."""
import re

from core.command_intent import SENSITIVE_REQUEST
from core.confirmation import ConfirmationPrompt
from core.models import SkillResult
from core.security import redact_user_text
from services.storage.knowledge_store import MAX_TEXT, MAX_TITLE, KnowledgeError

_ADD = re.compile(r"(?:додай (?:у|в|до) баз[иу] знань|запам'ятай як довідку)[,:\s]+(.+)", re.I | re.S)
_LIST = re.compile(r"(?:що (?:є )?(?:у|в) базі знань|покажи базу знань)", re.I)
_FORGET = re.compile(r"(?:забудь|видали) довідку(?: про)?\s+(.+)", re.I)
_CLEAR = re.compile(r"(?:очисти|видали) базу знань", re.I)
_SECRET = re.compile(r"парол|password|passwd|secret|токен|token|api.?key|api.?ключ|приватн.{0,8}ключ", re.I)


def parse_knowledge_request(text):
    """(operation, title, body) or None; never matches personal-memory phrases."""
    value = re.sub(r"[’ʼ]", "'", text.strip()).rstrip(".!?")
    match = _ADD.fullmatch(value)
    if match:
        content = match[1].strip()
        title, sep, body = content.partition(":")
        return ("add", title.strip(), body.strip()) if sep and body.strip() else ("add", content[:60], content)
    if _LIST.fullmatch(value):
        return ("list", "", "")
    match = _FORGET.fullmatch(value)
    if match:
        return ("forget", match[1].strip(), "")
    if _CLEAR.fullmatch(value):
        return ("clear", "", "")
    return None


def _unsafe(text):
    return bool(SENSITIVE_REQUEST.search(text) or _SECRET.search(text) or redact_user_text(text) != text)


async def _confirmed(context, description):
    return await context.confirm(ConfirmationPrompt(description, description, short_question=True))


async def handle_knowledge_request(request, context):
    operation, title, body = request
    store = context.services["knowledge"]
    if operation == "list":
        titles = [entry["title"] for entry in store.entries()[:20]]
        return SkillResult(True, ("У базі знань: " + "; ".join(titles) + ".") if titles else
                           "База знань поки порожня.", {"command_type": "knowledge_list"})
    if operation == "add":
        return await _add(store, title, body, context)
    if operation == "forget":
        return await _forget(store, title, context)
    if not await _confirmed(context, "Очистити всю базу знань"):
        return SkillResult(True, "Зміни бази знань скасовано.", {"command_type": "knowledge_cancelled"})
    store.clear()
    return SkillResult(True, "Базу знань очищено.", {"command_type": "knowledge_clear"})


async def _add(store, title, body, context):
    if _unsafe(title + " " + body):
        return SkillResult(True, "Секрети не зберігаю в базі знань.", {"command_type": "knowledge_rejected"})
    if len(title) > MAX_TITLE or len(body) > MAX_TEXT:
        return SkillResult(True, f"Довідка задовга: тема до {MAX_TITLE}, текст до {MAX_TEXT} символів.",
                           {"command_type": "knowledge_rejected"})
    if not await _confirmed(context, f"Додати в базу знань довідку «{title}»"):
        return SkillResult(True, "Зміни бази знань скасовано.", {"command_type": "knowledge_cancelled"})
    try:
        store.add(title, body)
    except KnowledgeError:
        return SkillResult(True, "Не вдалося додати довідку: база знань заповнена або запис порожній.",
                           {"command_type": "knowledge_rejected"})
    return SkillResult(True, f"Додано в базу знань: «{title}».", {"command_type": "knowledge_add"})


async def _forget(store, topic, context):
    entries = store.matching(topic)
    if not entries:
        return SkillResult(True, "Не знайшов довідок із такою темою.", {"command_type": "knowledge_forget"})
    titles = "; ".join(entry["title"] for entry in entries[:10])
    if not await _confirmed(context, f"Видалити з бази знань: {titles}"):
        return SkillResult(True, "Зміни бази знань скасовано.", {"command_type": "knowledge_cancelled"})
    count = store.remove_ids({entry["id"] for entry in entries})
    return SkillResult(True, f"Видалено довідок: {count}.", {"command_type": "knowledge_forget"})
