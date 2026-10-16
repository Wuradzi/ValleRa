"""Explicit local read requests: choose a file, correlate consent, then snapshot."""
import asyncio
import re
import time
from pathlib import Path

from core.confirmation import confirm_action
from core.models import SkillResult
from core.task_context import CHOICE, ORDINALS, selection_text, file_stamp
from services.files.document_context import DocumentContext, DocumentError, SUPPORTED, read_document

READ = re.compile(r'^(?:будь ласка[, ]+)?прочитай\s+(?:файл|документ)(?:\s+(.+?))?[.!?]*$', re.I)
CLEAR = {'забудь файл', 'прибери файл з контексту'}
CANCEL = {'скасуй', 'скасувати', 'відміна', 'не треба', 'ні'}


class DocumentReading:
    def __init__(self):
        self.document = DocumentContext()
        self.pending = None

    def clear(self):
        self.pending = None
        self.document.clear()

    def accepts(self, text):
        if self.pending and time.monotonic() >= self.pending['expires']:
            self.pending = None
        return bool(READ.fullmatch(text) or selection_text(text) in CLEAR or self.pending)

    async def handle(self, text, context):
        normalized = selection_text(text)
        if normalized in CLEAR:
            self.clear()
            return SkillResult(True, 'Файл прибрано з активного контексту. Попередні відповіді в історії не видалені.')
        if normalized in CANCEL:
            self.pending = None
            return SkillResult(True, 'Читання скасовано.')
        match = READ.fullmatch(text)
        pending, self.pending = self.pending, None
        files = context.services['files']
        if match:
            query = (match.group(1) or '').strip(' «»"')
        elif pending and pending['kind'] == 'name':
            # Do not consume a new instruction as a file name.
            from core.natural_turn import direct_request
            if len(text) > 150 or direct_request(text, ''):
                return None
            query = text.strip(' «»"')
        elif pending:
            choice = CHOICE.fullmatch(normalized)
            if not choice:
                return None
            word = choice.group(1)
            index = int(word) if word.isdigit() else ORDINALS[word]
            if not 1 <= index <= len(pending['entries']):
                self.pending = pending
                return SkillResult(True, 'Назвіть номер із запропонованого списку або скажіть відміна.')
            path, stamp = pending['entries'][index - 1]
            return await self._read(path, stamp, context)
        else:
            return None
        if not query:
            self.pending = {'kind': 'name', 'expires': time.monotonic() + 120}
            return SkillResult(True, 'Який файл прочитати? Назвіть ім’я з розширенням або введіть локальний шлях.')
        path = Path(query)
        if path.is_absolute():
            # No UNC/network paths, including on Windows.
            if query.startswith(('\\\\', '//')) or not files.is_allowed(path):
                return SkillResult(True, 'Потрібен локальний файл у дозволених каталогах.')
            found = [path]
        else:
            found = await asyncio.to_thread(files.search, query, None, limit=5)
        status = getattr(files, 'search_status', {})
        if isinstance(status, dict) and status.get('cancelled'):
            return SkillResult(True, 'Пошук скасовано; файл не прочитано.')
        found = [Path(p) for p in found if Path(p).suffix.lower() in SUPPORTED]
        if not found:
            return SkillResult(True, 'Підтримуваного документа не знайдено. Спробуйте точну назву або локальний шлях. '
                               'Доступні текстові формати, DOCX і ODT; PDF, скани та DOC поки не читаю.')
        entries = [(p, file_stamp(p)) for p in found[:5]]
        if len(entries) == 1:
            return await self._read(*entries[0], context)
        self.pending = {'kind': 'choice', 'entries': entries, 'expires': time.monotonic() + 120}
        return SkillResult(True, 'Який файл прочитати? ' + '; '.join(
            f'{i}. {p.name} ({p.parent})' for i, (p, _) in enumerate(entries, 1)),
            {'command_type': 'document_choice'})

    async def _read(self, path, stamp, context):
        cancelled = await confirm_action(context,
            f'Прочитати локальний файл {path} і передавати його уривки мовній моделі, '
            'зокрема хмарній, у цій розмові протягом 30 хвилин. Відповіді можуть залишитися в історії.',
            f'Прочитати «{path.name}» і дозволити передавання тексту мовній моделі, зокрема хмарній?')
        if cancelled is not None:
            return cancelled
        try:
            text = await asyncio.to_thread(read_document, path, context.services['files'], stamp)
        except (OSError, DocumentError):
            return SkillResult(True, 'Файл недоступний, змінився, завеликий або має непідтримуваний вміст. '
                               'Текст не додано. Потрібен текстовий документ до 2 МіБ і 120000 символів.')
        if asyncio.current_task().cancelling():
            raise asyncio.CancelledError
        self.document.set(path.name, text)
        return SkillResult(True, f'Прочитано «{path.name}». Текст доступний у поточній розмові на 30 хвилин. '
                           'Можеш запитати, як він стосується нашої теми, або сказати «забудь файл».',
                           {'command_type': 'document_read', 'success': True})
