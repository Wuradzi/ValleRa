from __future__ import annotations

import getpass
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values

from config import default_config, merge_config, validate_config
from core.console import console_getpass, console_input as input, console_print as print, console_stream
from core.logging_setup import register_secret
from services.audio.calibration import calibrate_microphone
from services.storage.secret_store import SecretStore


def choose_device(input_device: bool) -> int | None:
    try:
        import sounddevice as sd
        devices = sd.query_devices()
    except Exception as exc:
        print(f"[SETUP] Аудіопристрої недоступні: {exc}")
        return None

    label = "мікрофони" if input_device else "пристрої відтворення"
    print(f"\nДоступні {label}:")
    for index, device in enumerate(devices):
        channels = device["max_input_channels"] if input_device else device["max_output_channels"]
        if channels:
            print(f"  {index}: {device['name']}")

    available = {
        index
        for index, device in enumerate(devices)
        if device["max_input_channels" if input_device else "max_output_channels"]
    }
    value = input("Номер або Enter для системного пристрою: ").strip()
    if not value:
        return None
    if not value.isdigit() or int(value) not in available:
        print("[SETUP] Невідомий або несумісний аудіопристрій; використано системний.")
        return None
    return int(value)


def _load_existing_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Не вдалося прочитати наявний {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} повинен містити JSON-об'єкт")
    return value


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = path.with_name(f"{path.name}.{stamp}.bak")
    suffix = 1
    while destination.exists():
        destination = path.with_name(f"{path.name}.{stamp}.{suffix}.bak")
        suffix += 1
    shutil.copy2(path, destination)
    return destination


def _atomic_write(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(value, encoding="utf-8")
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


RECOVERY_CONFIRM_CHARS = 6


def _hidden_prompt(prompt: str) -> str:
    # Not console_getpass: a short suffix must not become a global log-redaction token.
    return getpass.getpass(prompt, stream=console_stream(error=True))


def recovery_export_allowed(path: Path, project_root: Path) -> str | None:
    """Return a refusal reason, or None when the key may be written to this path."""
    target = path.expanduser().resolve()
    root = project_root.resolve()
    if target == root or root in target.parents:
        return "файл у папці проєкту лежить поруч зі сховищем і знецінює ключ"
    if target.exists():
        return "файл уже існує; перезапис не виконую"
    if not target.parent.is_dir():
        return "папки для файлу не існує"
    return None


def deliver_recovery_key(
    recovery_key: str,
    project_root: Path,
    *,
    ask=None,
    ask_secret=None,
    out=None,
) -> Path | None:
    """Show the key once, require proof it was saved; never store it beside the vault."""
    ask = ask or (lambda prompt: input(prompt))
    ask_secret = ask_secret or _hidden_prompt
    out = out or console_stream()
    # The key goes to the terminal only; session logs mask it if it ever reaches them.
    register_secret(recovery_key)
    out.write(
        "\n=== КЛЮЧ ВІДНОВЛЕННЯ RAVEN (показується один раз) ===\n"
        f"{recovery_key}\n"
        "Збережіть його в менеджері паролів або на окремому носії.\n"
        "Без нього втрачений майстер-пароль не відновити.\n\n"
    )
    out.flush()
    expected = recovery_key[-RECOVERY_CONFIRM_CHARS:]
    while ask_secret(
        f"Введіть останні {RECOVERY_CONFIRM_CHARS} символів ключа для підтвердження: "
    ).strip() != expected:
        print("Не збігається. Перевірте збережену копію ключа.")

    while True:
        raw = ask(
            "Шлях до файлу з копією ключа поза папкою проєкту (Enter — не зберігати у файл): "
        ).strip().strip('"')
        if not raw:
            return None
        path = Path(raw)
        reason = recovery_export_allowed(path, project_root)
        if reason:
            print(f"Не збережено: {reason}.")
            continue
        target = path.expanduser().resolve()
        _atomic_write(target, "КЛЮЧ ВІДНОВЛЕННЯ RAVEN\n\n" + recovery_key + "\n")
        print(f"Копію ключа записано у {target}. Тримайте її окремо від комп’ютера з Raven.")
        return target


def run_first_start_wizard(project_root: Path) -> None:
    print("=== Початкове налаштування Raven ===")
    config_path = project_root / "config.json"
    env_path = project_root / ".env"
    existing_config = _load_existing_config(config_path)
    config = merge_config(existing_config) if existing_config else default_config()
    config["stt"]["input_device"] = choose_device(True)
    config["tts"]["output_device"] = choose_device(False)
    try:
        config["stt"]["noise_threshold"] = calibrate_microphone(
            config["stt"]["input_device"],
            sample_rate=config["stt"]["sample_rate"],
        )
    except Exception as exc:
        print(f"[SETUP] Калібрування пропущено: {exc}")

    voice = input("Підказка TTS-голосу [Volodymyr]: ").strip()
    if voice:
        config["tts"]["voice_hint"] = voice

    print("\nAPI-ключі можна лишити порожніми; наявні значення буде збережено.")
    existing_env = {
        key: str(value or "") for key, value in dotenv_values(env_path).items()
    }
    env = dict(existing_env)
    for key, label in (
        ("GEMINI_API_KEY", "Gemini"),
        ("GROQ_API_KEY", "Groq"),
        ("OPENAI_API_KEY", "OpenAI"),
        ("ANTHROPIC_API_KEY", "Anthropic"),
    ):
        entered = console_getpass(f"{label} API key (Enter — не змінювати): ").strip()
        if entered:
            env[key] = entered
        else:
            env.setdefault(key, "")
    ollama = input("Модель Ollama або Enter, щоб не змінювати: ").strip()
    if ollama:
        config["llm"]["models"]["ollama"] = ollama

    for directory in ("data", "cache", "logs", "models"):
        path = project_root / directory
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt" and directory in {"data", "cache"}:
            subprocess.run(["attrib", "+H", str(path)], check=False, capture_output=True)

    validate_config(config)
    # API keys are preserved through the merge and the atomic replace keeps the
    # old file intact on failure. Do not create extra plaintext copies of .env.
    backups = [path for path in (_backup(config_path),) if path]
    _atomic_write(
        config_path,
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
    )
    _atomic_write(
        env_path,
        "\n".join(
            f"{key}={json.dumps(value, ensure_ascii=False)}"
            for key, value in env.items()
        ) + "\n",
    )
    if backups:
        print("Резервні копії: " + ", ".join(str(path) for path in backups))

    vault = SecretStore(project_root / "data" / "secrets.json")
    if not vault.exists:
        while True:
            password = console_getpass("Створіть майстер-пароль: ")
            repeated = console_getpass("Повторіть майстер-пароль: ")
            if password and password == repeated:
                break
            print("Паролі не збігаються або порожні.")

        recovery_key = vault.create(password)
        deliver_recovery_key(recovery_key, project_root)

    print("Налаштування завершено.")
