"""Explicit user-owned facts, never model-inferred memories or secrets."""
import re

from core.confirmation import ConfirmationPrompt
from core.models import SkillResult
from core.security import redact_user_text


def parse_request(text):
    text = text.strip().rstrip('.!?')
    text = re.sub(r"[’ʼ]", "'", text)
    if re.fullmatch(r"(?:що ти пам'ятаєш(?: про мене)?|покажи (?:всю )?пам'ять)", text, re.I):
        return 'list', ''
    if re.fullmatch(r"(?:забудь усе(?: про мене)?|очисти пам'ять)", text, re.I):
        return 'clear', ''
    match = re.fullmatch(r"(запам'ятай|онови(?: пам'ять)?|забудь)[,:\s]+(.+)", text, re.I)
    if not match:
        return None
    return ('remember' if match[1].lower() == "запам'ятай" else
            'forget' if match[1].lower() == 'забудь' else 'update'), match[2].strip()


async def handle_request(request, context, llm):
    operation, content = request
    store = context.services['memory']
    if (len(content) > 1000 or redact_user_text(content) != content or
            re.search(r'парол|password|passwd|secret|токен|token|api.?key|api.?ключ|приватн.{0,8}ключ', content, re.I)):
        return SkillResult(True, 'Секрети не зберігаю як звичайні факти пам’яті.', {'command_type': 'memory_rejected'})
    if operation == 'list':
        items = [item for item in store.all() if not item.get('sensitive')]
        return SkillResult(True, '\n'.join(f"{i['key']}: {i['value']}" for i in items[:20]) or
                           'Збережених фактів поки немає.', {'command_type': 'memory_list'})
    key = value = ''
    if operation in {'remember', 'update'}:
        content = re.sub(r'^що\s+', '', content, flags=re.I)
        pair = re.split(r'\s*:\s*|\s+це\s+', content, maxsplit=1)
        key, value = (pair if len(pair) == 2 else (content, content))
        if not key or not value:
            return SkillResult(True, 'Назвіть факт повністю: запам’ятай тема: значення.')
        existing = [item for item in store.all() if item['key'].casefold() == key.casefold()]
        if operation == 'update' and not existing:
            return SkillResult(True, 'Не знайшов точну тему. Скажіть «що ти пам’ятаєш», потім «онови тема: значення».')
        reset = bool(existing)
        description = f'Зберегти факт «{key}: {value}»'
    else:
        query = re.sub(r'^(?:інформацію\s+)?про\s+', '', content, flags=re.I)
        items = store.all() if operation == 'clear' else [item for item in store.all()
            if query.casefold() in (item['key'] + ' ' + item['value']).casefold()]
        if not items and operation != 'clear':
            return SkillResult(True, 'Відповідних записів пам’яті не знайшов.')
        reset = True
        description = 'Забути всі збережені факти' if operation == 'clear' else (
            'Забути записи: ' + '; '.join(item['key'] for item in items))
    if reset:
        description += '. Також почати новий контекст розмови; старі журнали й архіви залишаться'
    if not await context.confirm(ConfirmationPrompt(description, description, short_question=True)):
        return SkillResult(True, 'Зміни пам’яті скасовано.', {'command_type': 'memory_cancelled'})
    # Reset first: failure must not claim that old context was removed.
    if reset:
        llm.new_conversation()
    if operation in {'remember', 'update'}:
        store.remember(key, value, category='user_explicit')
    else:
        ids = {item['id'] for item in items}
        store.remove_ids(ids)
    return SkillResult(True, 'Пам’ять оновлено.' + (
        ' Починаємо новий контекст розмови. Журнали й архіви не видалено.' if reset else ''),
        {'command_type': 'memory_' + operation, 'memory_context_reset': reset})
