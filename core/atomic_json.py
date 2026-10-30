from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class AtomicJSONFile:
    def __init__(self, path: Path, default: Any, backup_count: int = 5):
        self.path = path
        self.default = default
        self.backup_count = backup_count
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Any:
        with self._lock:
            if not self.path.exists():
                return self._clone_default()
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                restored = self._restore_latest_backup()
                return restored if restored is not None else self._clone_default()

    def save(self, data: Any, *, forget_history: bool = False) -> None:
        """Atomic write. forget_history=True is for user-requested deletion:
        the previous state is not backed up and existing .bakN copies are removed,
        so deleted records do not survive in rotating backups."""
        with self._lock:
            serialized = json.dumps(data, ensure_ascii=False, indent=2)
            json.loads(serialized)
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            temp.write_text(serialized + "\n", encoding="utf-8")

            if self.path.exists() and not forget_history:
                self._rotate_backups()
                shutil.copy2(self.path, self._backup_path(1))
            os.replace(temp, self.path)
            if forget_history:
                self.purge_backups()

    def purge_backups(self) -> list[Path]:
        """Remove every .bakN copy of this file; returns copies that could not be removed."""
        with self._lock:
            pattern = re.compile(re.escape(self.path.name) + r"\.bak\d+")
            remaining = []
            for backup in self.path.parent.glob(self.path.name + ".bak*"):
                if not pattern.fullmatch(backup.name):
                    continue
                try:
                    backup.unlink(missing_ok=True)
                except OSError as exc:
                    remaining.append(backup)
                    logger.warning("Backup was not removed: %s (%s)", backup.name, type(exc).__name__)
            return remaining

    def _clone_default(self) -> Any:
        return json.loads(json.dumps(self.default, ensure_ascii=False))

    def _backup_path(self, index: int) -> Path:
        return self.path.with_suffix(self.path.suffix + f".bak{index}")

    def _rotate_backups(self) -> None:
        oldest = self._backup_path(self.backup_count)
        oldest.unlink(missing_ok=True)
        for index in range(self.backup_count - 1, 0, -1):
            source = self._backup_path(index)
            if source.exists():
                os.replace(source, self._backup_path(index + 1))

    def _restore_latest_backup(self) -> Any | None:
        for index in range(1, self.backup_count + 1):
            backup = self._backup_path(index)
            if not backup.exists():
                continue
            try:
                data = json.loads(backup.read_text(encoding="utf-8"))
                shutil.copy2(backup, self.path)
                return data
            except (json.JSONDecodeError, OSError):
                continue
        return None
