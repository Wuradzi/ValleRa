from __future__ import annotations

import base64
import os
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from core.atomic_json import AtomicJSONFile


class SecretStore:
    def __init__(self, path: Path):
        self.path = path
        self.file = AtomicJSONFile(path, {})
        self._fernet: Fernet | None = None

    @property
    def exists(self) -> bool:
        return self.path.exists()

    @property
    def unlocked(self) -> bool:
        return self._fernet is not None

    def create(self, password: str) -> str:
        data_key = Fernet.generate_key()
        recovery_key = secrets.token_urlsafe(32)
        master_salt = os.urandom(16)
        recovery_salt = os.urandom(16)
        state = {
            "version": 1,
            "master_salt": self._b64(master_salt),
            "recovery_salt": self._b64(recovery_salt),
            "wrapped_master": Fernet(self._derive(password, master_salt)).encrypt(data_key).decode(),
            "wrapped_recovery": Fernet(self._derive(recovery_key, recovery_salt)).encrypt(data_key).decode(),
            "items": [],
        }
        self.file.save(state)
        self._fernet = Fernet(data_key)
        return recovery_key

    def unlock_with_password(self, password: str) -> bool:
        return self._unlock(password, "master_salt", "wrapped_master")

    def unlock_with_recovery_key(self, key: str) -> bool:
        return self._unlock(key, "recovery_salt", "wrapped_recovery")

    def _unlock(self, secret: str, salt_name: str, wrapped_name: str) -> bool:
        if not self.exists:
            return False
        state = self.file.load()
        try:
            wrapper = Fernet(self._derive(secret, self._unb64(state[salt_name])))
            self._fernet = Fernet(wrapper.decrypt(state[wrapped_name].encode()))
            return True
        except (InvalidToken, KeyError, ValueError):
            return False

    def set(self, key: str, value: str) -> dict:
        fernet = self._require()
        state = self.file.load()
        now = datetime.now(timezone.utc).isoformat()
        encrypted = fernet.encrypt(value.encode()).decode()
        for item in state["items"]:
            if item["key"].lower() == key.lower():
                item.update(value_encrypted=encrypted, updated_at=now)
                self.file.save(state)
                return item

        item = {
            "id": f"secret_{uuid.uuid4().hex[:12]}",
            "key": key,
            "value_encrypted": encrypted,
            "created_at": now,
            "updated_at": now,
            "sensitive": True,
        }
        state["items"].append(item)
        self.file.save(state)
        return item

    def get(self, key: str) -> str | None:
        fernet = self._require()
        normalized = key.strip().casefold()
        for item in self.file.load()["items"]:
            if normalized == item["key"].strip().casefold():
                return fernet.decrypt(item["value_encrypted"].encode()).decode()
        return None

    def _require(self) -> Fernet:
        if self._fernet is None:
            raise PermissionError("Сховище секретів заблоковано")
        return self._fernet

    @staticmethod
    def _derive(secret: str, salt: bytes) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=600_000,
        )
        return base64.urlsafe_b64encode(kdf.derive(secret.encode()))

    @staticmethod
    def _b64(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode()

    @staticmethod
    def _unb64(value: str) -> bytes:
        return base64.urlsafe_b64decode(value.encode())
