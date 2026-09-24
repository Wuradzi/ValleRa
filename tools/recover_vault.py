from __future__ import annotations

import getpass
from pathlib import Path

from services.storage.secret_store import SecretStore


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    vault = SecretStore(root / "data" / "secrets.json")
    if not vault.exists:
        print("Сховище секретів не знайдено.")
        return 1

    recovery_key = getpass.getpass("Ключ відновлення: ")
    if not vault.unlock_with_recovery_key(recovery_key):
        print("Неправильний ключ відновлення.")
        return 2

    print("Сховище успішно розблоковано ключем відновлення.")
    print("Для зміни майстер-пароля потрібен окремий модуль міграції ключа.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
