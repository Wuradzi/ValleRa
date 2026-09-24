from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values

from config import default_config, merge_config, validate_config
from core.console import console_getpass, console_input as input, console_print as print
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


def run_first_start_wizard(project_root: Path) -> None:
    print("=== Початкове налаштування ValleRa ===")
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
        recovery_file = project_root / "RECOVERY_KEY.txt"
        _atomic_write(
            recovery_file,
            "КЛЮЧ ВІДНОВЛЕННЯ VALLeRA\n\n" + recovery_key + "\n",
        )
        try:
            recovery_file.chmod(0o600)
        except OSError:
            pass
        print(f"Ключ відновлення записано у {recovery_file}")
        print("ВАЖЛИВО: перенесіть його на окремий захищений носій і видаліть локальну копію.")

    print("Налаштування завершено.")
