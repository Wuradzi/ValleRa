from __future__ import annotations

import os
import shutil
import stat
import threading
import time
import logging
from collections import deque
from datetime import datetime
from pathlib import Path, PureWindowsPath
from uuid import uuid4

from send2trash import send2trash

logger = logging.getLogger(__name__)


def local_drive_roots():
    """Discover local fixed/removable Windows volumes, never mapped network drives."""
    from services.platform import resolve_platform
    return resolve_platform().drive_roots()


class FileService:
    def __init__(self, allowed_directories: list[str], *, all_local_drives=False, budget_seconds=15.0):
        self.allowed = [
            Path(path).expanduser().resolve()
            for path in allowed_directories
            if path and (os.name == 'nt' or not PureWindowsPath(path).drive)
        ]
        self.search_roots = list(dict.fromkeys(self.allowed + (local_drive_roots() if all_local_drives else [])))
        self.budget_seconds = budget_seconds
        self.search_status = {}
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._cursor = None
        self._query = None
        self._expires = 0
        self._skipped = 0
        self._progress_lock = threading.Lock()
        self._progress = None
        self._cancel_search = threading.Event()

    def search_snapshot(self):
        with self._progress_lock:
            return dict(self._progress) if self._progress is not None else None

    def cancel_search(self, request_id):
        with self._progress_lock:
            if not self._progress or not self._progress['active'] or self._progress['id'] != request_id:
                return False
            self._cancel_search.set()
            self._progress.update(stage='cancelling', cancel_requested=True)
            return True

    def is_allowed(self, path: Path) -> bool:
        resolved = path.expanduser().resolve()
        return any(resolved == root or root in resolved.parents for root in self.search_roots)

    def is_writable(self, path: Path) -> bool:
        resolved = path.expanduser().resolve()
        return any(resolved == root or root in resolved.parents for root in self.allowed)

    def close(self):
        self._stopped.set()
        if self._lock.acquire(blocking=False):
            try:
                self._close_cursor()
            finally:
                self._lock.release()

    def _close_cursor(self):
        if self._cursor is not None:
            self._cursor.close()
            self._cursor = None

    def _walk(self):
        visited = set()
        # User folders first. Breadth-first traversal avoids one deep subtree
        # consuming every request; no junction/symlink traversal or content reads.
        pending = deque(self.search_roots)
        while pending and not self._stopped.is_set():
            directory = pending.popleft()
            yield None
            key = os.path.normcase(str(directory))
            if key in visited:
                continue
            visited.add(key)
            try:
                attributes = getattr(directory.lstat(), "st_file_attributes", 0)
                if (directory.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
                        or not self.is_allowed(directory)):
                    self._skipped += 1
                    continue
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if self._stopped.is_set():
                            return
                        yield None  # Allow a deadline check even for directories/nonmatching files.
                        try:
                            if entry.is_symlink():
                                self._skipped += 1
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
                                if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
                                    self._skipped += 1
                                    continue
                                pending.append(Path(entry.path))
                            elif entry.is_file(follow_symlinks=False):
                                yield Path(entry.path)
                        except OSError:
                            self._skipped += 1
            except OSError:
                self._skipped += 1

    def search(
        self,
        query: str = "",
        extension: str | None = None,
        modified_after: datetime | None = None,
        limit: int = 50,
    ) -> list[Path]:
        if limit <= 0:
            return []
        query, extension = query.casefold(), extension.casefold() if extension else None
        key = (query, extension, modified_after, limit)
        result = []
        with self._lock:
            started = time.monotonic()
            if self._stopped.is_set():
                return []
            with self._progress_lock:
                self._cancel_search = threading.Event()
                self._progress = dict(id=uuid4().hex, active=True, stage='searching', started_at=started,
                                      cancel_requested=False, checked=0, matches=0)
            if key != self._query or self._cursor is None or time.monotonic() >= self._expires:
                self._close_cursor()
                self._query, self._skipped = key, 0
                self._cursor = self._walk()
            deadline = time.monotonic() + self.budget_seconds
            complete = False
            while not self._stopped.is_set() and not self._cancel_search.is_set() and time.monotonic() < deadline and len(result) < limit:
                try:
                    path = next(self._cursor)
                except StopIteration:
                    complete = True
                    self._close_cursor()
                    break
                except BaseException:
                    self._close_cursor()
                    with self._progress_lock:
                        self._progress.update(active=False, stage='failed')
                    raise
                if path is None:
                    continue
                with self._progress_lock:
                    self._progress['checked'] += 1
                if query and query.lower() not in path.name.lower():
                    continue
                if extension and path.suffix.lower() != extension.lower():
                    continue
                try:
                    modified = path.stat().st_mtime
                    if modified_after and datetime.fromtimestamp(modified) < modified_after:
                        continue
                    if not self.is_allowed(path):
                        continue
                    result.append((modified, path))
                    with self._progress_lock:
                        self._progress['matches'] = len(result)
                except (OSError, ValueError):
                    self._skipped += 1
            with self._progress_lock:
                cancelled = self._cancel_search.is_set() or self._stopped.is_set()
                if cancelled:
                    self._close_cursor()
                    result.clear()
                self._progress.update(active=False, stage='cancelled' if cancelled else 'completed' if complete else 'partial')
            self._expires = time.monotonic() + 300
            self.search_status = {"complete": complete and not cancelled, "resumable": self._cursor is not None,
                                  "cancelled": cancelled,
                                  "skipped": self._skipped, "roots": [str(root) for root in self.search_roots],
                                  "duration_ms": round((time.monotonic() - started) * 1000)}
            logger.info("File search page matches=%s complete=%s resumable=%s skipped=%s duration_ms=%s",
                        len(result), complete, self.search_status["resumable"], self._skipped, self.search_status["duration_ms"])
        result.sort(key=lambda item: item[0], reverse=True)
        return [path for _, path in result]

    def largest(self, limit: int = 10) -> list[Path]:
        files = self.search(limit=10000)
        ranked = []
        for path in files:
            try:
                ranked.append((path.stat().st_size, str(path), path))
            except OSError:
                continue
        return [item[2] for item in sorted(ranked, reverse=True)[:limit]]

    def open(self, path: Path) -> None:
        path = path.expanduser().resolve(strict=True)
        if not path.is_file() or not self.is_allowed(path):
            raise PermissionError("Файл поза дозволеними каталогами")
        from services.platform import resolve_platform
        resolve_platform().open_file(path)

    def copy(self, source: Path, destination: Path) -> Path:
        target = destination / source.name if destination.is_dir() else destination
        if not self.is_allowed(source) or not self.is_writable(target):
            raise PermissionError("Шлях поза дозволеними каталогами")
        return Path(shutil.copy2(source, destination))

    def move(self, source: Path, destination: Path) -> Path:
        target = destination / source.name if destination.is_dir() else destination
        if not self.is_writable(source) or not self.is_writable(target):
            raise PermissionError("Шлях поза дозволеними каталогами")
        return Path(shutil.move(str(source), str(destination)))

    def delete_to_trash(self, path: Path) -> None:
        if not self.is_writable(path):
            raise PermissionError("Шлях поза дозволеними каталогами")
        send2trash(str(path))
