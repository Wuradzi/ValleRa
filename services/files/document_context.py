"""Bounded, read-only document snapshots. No execution, crawling or persistence."""
from __future__ import annotations

import io
import os
import re
import time
import zipfile
import zlib
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

from core.security import redact_user_text

TEXT_TYPES = {'.txt', '.md', '.markdown', '.rst', '.csv', '.tsv', '.json', '.yaml', '.yml',
              '.xml', '.html', '.htm', '.py', '.js', '.ts', '.css', '.sql', '.c', '.h', '.cpp'}
SUPPORTED = TEXT_TYPES | {'.docx', '.odt'}
MAX_BYTES = 2 * 1024 * 1024
MAX_CHARS = 120000


class DocumentError(ValueError):
    pass


class VisibleHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'template'}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'template'}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {'p', 'div', 'tr', 'li', 'h1', 'h2', 'h3'}:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def extract_document(data: bytes, suffix: str) -> str:
    if suffix not in SUPPORTED or len(data) > MAX_BYTES:
        raise DocumentError('Непідтримуваний тип або файл більший за 2 МіБ.')
    if suffix in {'.docx', '.odt'}:
        member = 'word/document.xml' if suffix == '.docx' else 'content.xml'
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                info = archive.getinfo(member)
                if info.file_size > MAX_BYTES or info.flag_bits & 1:
                    raise DocumentError('Документ завеликий або зашифрований.')
                xml = archive.read(member)
            if b'\x00' in xml or b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
                raise DocumentError('XML із деклараціями сутностей не підтримується.')
            root = ElementTree.fromstring(xml)
            text = '\n'.join(''.join(node.itertext()) for node in root.iter()
                             if node.tag.rsplit('}', 1)[-1] in {'p', 'h'})
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, RuntimeError, zlib.error) as exc:
            raise DocumentError('Не вдалося прочитати структуру документа.') from exc
    else:
        try:
            encoding = 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
            text = data.decode(encoding)
        except UnicodeError as exc:
            raise DocumentError('Потрібне кодування UTF-8 або UTF-16 з BOM.') from exc
        if '\x00' in text:
            raise DocumentError('Це не підтримуваний текстовий файл.')
        if suffix in {'.html', '.htm'}:
            parser = VisibleHTML()
            parser.feed(text)
            text = ''.join(parser.parts)
    text = text.strip()
    if not text:
        raise DocumentError('У документі не знайдено доступного тексту.')
    if len(text) > MAX_CHARS:
        raise DocumentError('Текст перевищує 120000 символів. Оберіть менший документ.')
    return redact_user_text(text)


def read_document(path: Path, files, expected_stamp):
    from core.task_context import file_stamp
    if (path.is_symlink() or not path.is_file() or not files.is_allowed(path)
            or expected_stamp is None or file_stamp(path) != expected_stamp):
        raise DocumentError('Файл змінився або недоступний. Повторіть вибір.')
    with path.open('rb') as stream:
        info = os.fstat(stream.fileno())
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != expected_stamp[1:]:
            raise DocumentError('Файл змінився перед читанням.')
        data = stream.read(MAX_BYTES + 1)
    if file_stamp(path) != expected_stamp or not files.is_allowed(path):
        raise DocumentError('Файл змінився під час читання. Повторіть вибір.')
    return extract_document(data, path.suffix.lower())


class DocumentContext:
    """One consented snapshot, expires after 30 minutes; excerpts adapt to the topic."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.clear()

    def clear(self):
        self.name, self.text, self.expires = '', '', 0

    def set(self, name, text):
        self.name, self.text, self.expires = name, text, self.clock() + 1800

    def context(self, topic):
        if self.clock() >= self.expires:
            self.clear()
        if not self.text:
            return None
        # Bounded blocks retain source order. A lexical hint is not a claim of relevance.
        blocks = [self.text[i:i + 1500] for i in range(0, len(self.text), 1500)]
        terms = set(re.findall(r'\w{4,}', topic.casefold()))
        ranked = sorted(range(len(blocks)), key=lambda i: (
            -sum(term in blocks[i].casefold() for term in terms), i))[:8]
        chosen = sorted(ranked)
        excerpt = '\n'.join(f'[Фрагмент {i + 1}] {blocks[i]}' for i in chosen)
        return {'name': self.name, 'text': excerpt, 'partial': len(chosen) < len(blocks),
                'total_chars': len(self.text), 'source': 'consented_local_snapshot'}
