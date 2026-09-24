from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import psutil


class SingleInstance:
    _RECENT_INCOMPLETE_LOCK_SECONDS = 2.0
    _CREATE_TIME_TOLERANCE_SECONDS = 1.0
    _ACQUIRE_ATTEMPTS = 4

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self.handle: int | None = None
        self._token: str | None = None

    def acquire(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        for _ in range(self._ACQUIRE_ATTEMPTS):
            token = uuid.uuid4().hex
            try:
                handle = os.open(
                    self.lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
            except FileExistsError:
                if self._lock_owner_is_running():
                    return False
                try:
                    self.lock_path.unlink()
                except FileNotFoundError:
                    continue
                except OSError:
                    # If the stale lock cannot be removed, fail conservatively
                    # instead of allowing two application instances.
                    return False
                continue

            payload = {
                "pid": os.getpid(),
                "created_at": self._current_process_create_time(),
                "token": token,
            }
            try:
                encoded = json.dumps(payload, separators=(",", ":")).encode("ascii")
                os.write(handle, encoded)
                os.fsync(handle)
            except BaseException:
                os.close(handle)
                self.lock_path.unlink(missing_ok=True)
                raise

            self.handle = handle
            self._token = token
            return True

        return False

    def release(self) -> None:
        if self.handle is not None:
            os.close(self.handle)
            self.handle = None

        if self._token is not None and self._lock_contains_token(self._token):
            self.lock_path.unlink(missing_ok=True)
        self._token = None

    def _lock_owner_is_running(self) -> bool:
        owner = self._read_lock()
        if owner is None:
            return self._lock_is_recent()

        pid = owner.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            return False

        try:
            process = psutil.Process(pid)
            recorded_create_time = owner.get("created_at")
            if isinstance(recorded_create_time, (int, float)):
                actual_create_time = process.create_time()
                if (
                    abs(actual_create_time - float(recorded_create_time))
                    > self._CREATE_TIME_TOLERANCE_SECONDS
                ):
                    return False
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return False
        except psutil.AccessDenied:
            # An inaccessible process still exists, so another instance may own
            # this lock. Refuse a second launch rather than guessing.
            return True

    def _read_lock(self) -> dict[str, Any] | None:
        try:
            raw = self.lock_path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            return None

        # Compatibility with lock files written by older ValleRa versions.
        try:
            return {"pid": int(raw)}
        except ValueError:
            pass

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    def _lock_is_recent(self) -> bool:
        try:
            age = time.time() - self.lock_path.stat().st_mtime
        except OSError:
            return False
        return age < self._RECENT_INCOMPLETE_LOCK_SECONDS

    def _lock_contains_token(self, token: str) -> bool:
        owner = self._read_lock()
        return owner is not None and owner.get("token") == token

    @staticmethod
    def _current_process_create_time() -> float:
        try:
            return psutil.Process(os.getpid()).create_time()
        except psutil.Error:
            # This should be exceptional, but the PID still makes the lock useful.
            return time.time()
