from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


class ConfigError(ValueError):
    pass


@dataclass(slots=True)
class ProjectPaths:
    project_root: Path
    config_file: Path
    env_file: Path
    data_dir: Path
    cache_dir: Path
    logs_dir: Path
    models_dir: Path

    @classmethod
    def from_root(cls, root: Path) -> "ProjectPaths":
        return cls(
            project_root=root,
            config_file=root / "config.json",
            env_file=root / ".env",
            data_dir=root / "data",
            cache_dir=root / "cache",
            logs_dir=root / "logs",
            models_dir=root / "models",
        )

    def ensure(self) -> None:
        for path in (self.data_dir, self.cache_dir, self.logs_dir, self.models_dir):
            path.mkdir(parents=True, exist_ok=True)


@dataclass(slots=True)
class Settings:
    paths: ProjectPaths
    language: str = "uk"
    assistant_name: str = "Валера"
    confirmation_timeout_seconds: int = 15
    fuzzy_threshold: int = 85
    stt_model_path: str = "models/vosk-model-small-uk-v3-small"
    stt_sample_rate: int = 16000
    stt_audio_block_ms: int = 250
    stt_endpoint_silence_ms: int = 0
    stt_endpoint_adaptive: bool = False
    stt_refinement_policy: str = "legacy"
    stt_command_confidence_threshold: float = 0.55
    stt_chat_confidence_threshold: float = 0.5
    stt_post_tts_pause_seconds: float = 0.25
    stt_whisper_enabled: bool = True
    stt_whisper_model: str = "base"
    stt_whisper_device: str = "cpu"
    stt_whisper_compute_type: str = "int8"
    stt_whisper_cpu_threads: int = 2
    stt_whisper_beam_size: int = 5
    stt_whisper_silence_ms: int = 500
    stt_whisper_min_confidence: float = 0.45
    stt_whisper_skip_silence: bool = True
    stt_whisper_preload: bool = True
    # Fixed vocabulary hints caused base to append words absent from the audio.
    stt_whisper_prompt: str = ""
    stt_whisper_hotwords: str = ""
    input_device: int | None = None
    output_device: int | None = None
    noise_threshold: float = 0.015
    tts_voice_hint: str = "Volodymyr"
    tts_rate: int = 185
    tts_volume: float = 1.0
    llm_order: list[str] = field(default_factory=lambda: [
        "gemini", "groq", "openai", "anthropic", "ollama"
    ])
    llm_models: dict[str, str] = field(default_factory=dict)
    llm_timeout_seconds: int = 10
    llm_total_timeout_seconds: int = 90
    llm_failures_before_switch: int = 3
    history_limit: int = 50
    command_interpretation_enabled: bool = True
    natural_actions_enabled: bool = True
    command_interpretation_timeout_seconds: int = 12
    dynamic_code_enabled: bool = False
    pentest_enabled: bool = True
    pentest_max_hosts: int = 256
    pentest_connect_timeout_seconds: float = 1.0
    pentest_scan_timeout_seconds: float = 180.0
    pentest_concurrency: int = 100
    check_updates: bool = True
    github_repository: str = ""
    log_level: str = "INFO"
    log_retention_days: int = 30
    application_aliases: dict[str, str] = field(default_factory=dict)
    user_directories: list[str] = field(default_factory=list)
    file_search_all_local_drives: bool = False
    file_search_budget_seconds: float = 15.0
    performance_profile: str = "fast"

    @property
    def api_keys(self) -> dict[str, str]:
        return {
            "gemini": os.getenv("GEMINI_API_KEY", "").strip(),
            "groq": os.getenv("GROQ_API_KEY", "").strip(),
            "openai": os.getenv("OPENAI_API_KEY", "").strip(),
            "anthropic": os.getenv("ANTHROPIC_API_KEY", "").strip(),
        }


def default_config() -> dict[str, Any]:
    return {
        "performance": {"profile": "fast"},
        "language": "uk",
        "assistant_name": "Валера",
        "confirmation_timeout_seconds": 15,
        "fuzzy_threshold": 85,
        "stt": {
            "model_path": "models/vosk-model-small-uk-v3-small",
            "sample_rate": 16000,
            "audio_block_ms": 250,
            "endpoint_silence_ms": 0,
            "endpoint_adaptive": False,
            "refinement_policy": "legacy",
            "command_confidence_threshold": 0.55,
            "chat_confidence_threshold": 0.5,
            "post_tts_pause_seconds": 0.25,
            "input_device": None,
            "noise_threshold": 0.015,
            "whisper": {
                "enabled": True,
                "model": "base",
                "device": "cpu",
                "compute_type": "int8",
                "cpu_threads": 2,
                "beam_size": 5,
                "silence_ms": 500,
                "min_confidence": 0.45,
                "skip_silence": True,
                "preload": True,
                "prompt": "",
                "hotwords": "",
            },
        },
        "tts": {
            "voice_hint": "Volodymyr",
            "rate": 185,
            "volume": 1.0,
            "output_device": None,
        },
        "llm": {
            "order": ["gemini", "groq", "openai", "anthropic", "ollama"],
            "timeout_seconds": 10,
            "total_timeout_seconds": 90,
            "failures_before_switch": 3,
            "history_limit": 50,
            "models": {
                "gemini": "gemini-3.5-flash-lite",
                "groq": "openai/gpt-oss-20b",
                "openai": "gpt-5-mini",
                "anthropic": "claude-haiku-4-5",
                "ollama": "",
            },
        },
        "security": {
            "dynamic_code_enabled": False,
            "pentest": {
                "enabled": True,
                "max_hosts": 256,
                "connect_timeout_seconds": 1.0,
                "scan_timeout_seconds": 180.0,
                "concurrency": 100,
            },
        },
        "commands": {"natural_actions": True, "llm_interpretation": True, "interpretation_timeout_seconds": 12},
        "updates": {"check": True, "github_repository": ""},
        "logging": {"level": "INFO", "retention_days": 30},
        "application_aliases": {
            "браузер": "",
            "телега": "Telegram",
            "телеграм": "Telegram",
            "музика": "Spotify",
            "код": "Visual Studio Code",
            "хром": "Chrome",
            "гугл хром": "Chrome",
        },
        "file_search": {"all_local_drives": False, "budget_seconds": 15},
        "user_directories": [
            str(Path.home() / "Desktop"),
            str(Path.home() / "Documents"),
            str(Path.home() / "Downloads"),
            str(Path.home() / "Pictures"),
            str(Path.home() / "Videos"),
            str(Path.home() / "Music"),
        ],
    }


def _deep_merge(base: dict[str, Any], custom: dict[str, Any]) -> dict[str, Any]:
    result = base.copy()
    for key, value in custom.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def merge_config(custom: dict[str, Any]) -> dict[str, Any]:
    return _deep_merge(default_config(), custom)


def apply_performance_profile(
    config: dict[str, Any],
    profile_override: str | None = None,
) -> dict[str, Any]:
    profile = profile_override or str(config["performance"]["profile"])
    config["performance"]["profile"] = profile
    if profile == "fast":
        config["stt"]["whisper"]["beam_size"] = 1
    elif profile == "raspberry_pi":
        # Keep the low-memory profile Vosk-only; switching the desktop default
        # to base is not evidence that a second model fits the Pi 3B budget.
        config["stt"]["whisper"]["enabled"] = False
        config["stt"]["whisper"]["preload"] = False
        config["stt"]["whisper"]["beam_size"] = 1
        config["llm"]["failures_before_switch"] = 1
        config["llm"]["history_limit"] = min(
            20,
            int(config["llm"]["history_limit"]),
        )
        config["security"]["pentest"]["concurrency"] = min(
            20,
            int(config["security"]["pentest"]["concurrency"]),
        )
        config["security"]["pentest"]["max_hosts"] = min(
            64,
            int(config["security"]["pentest"]["max_hosts"]),
        )
    return config


def validate_config(config: dict[str, Any]) -> None:
    try:
        if type(config["commands"]["llm_interpretation"]) is not bool:
            raise ConfigError("commands.llm_interpretation має бути true або false")
        if type(config["commands"]["natural_actions"]) is not bool:
            raise ConfigError("commands.natural_actions має бути true або false")
        command_timeout = config["commands"]["interpretation_timeout_seconds"]
        if type(command_timeout) is not int or not 1 <= command_timeout <= 30:
            raise ConfigError("commands.interpretation_timeout_seconds має бути цілим числом від 1 до 30")
        if config["performance"]["profile"] not in {
            "balanced",
            "fast",
            "raspberry_pi",
        }:
            raise ConfigError(
                "performance.profile має бути balanced, fast або raspberry_pi"
            )
        if not str(config["assistant_name"]).strip():
            raise ConfigError("assistant_name не може бути порожнім")
        if not str(config["language"]).strip():
            raise ConfigError("language не може бути порожньою")
        if not 0 <= float(config["stt"]["command_confidence_threshold"]) <= 1:
            raise ConfigError("stt.command_confidence_threshold має бути від 0 до 1")
        if not 0 <= float(config["stt"]["chat_confidence_threshold"]) <= 1:
            raise ConfigError("stt.chat_confidence_threshold має бути від 0 до 1")
        if not 0 <= float(config["stt"]["whisper"]["min_confidence"]) <= 1:
            raise ConfigError("stt.whisper.min_confidence має бути від 0 до 1")
        if int(config["stt"]["sample_rate"]) <= 0:
            raise ConfigError("stt.sample_rate має бути додатним")
        block_ms = config["stt"]["audio_block_ms"]
        if type(block_ms) is not int or block_ms not in {50, 100, 250}:
            raise ConfigError("stt.audio_block_ms має бути 50, 100 або 250")
        endpoint_ms = config["stt"]["endpoint_silence_ms"]
        if type(config["stt"]["endpoint_adaptive"]) is not bool:
            raise ConfigError("stt.endpoint_adaptive має бути true або false")
        if config["stt"].get("refinement_policy", "legacy") not in ("legacy", "vosk_first"):
            raise ConfigError("stt.refinement_policy має бути legacy або vosk_first")
        if type(endpoint_ms) is not int or endpoint_ms not in {0, 1200, 1400, 1600}:
            raise ConfigError("stt.endpoint_silence_ms має бути 0 (штатний Vosk), 1200, 1400 або 1600")
        if int(config["stt"]["whisper"]["beam_size"]) <= 0:
            raise ConfigError("stt.whisper.beam_size має бути додатним")
        if int(config["stt"]["whisper"]["cpu_threads"]) < 0:
            raise ConfigError("stt.whisper.cpu_threads має бути невід'ємним; 0 означає автоматичний вибір")
        if int(config["confirmation_timeout_seconds"]) <= 0:
            raise ConfigError("confirmation_timeout_seconds має бути додатним")
        if int(config["llm"]["timeout_seconds"]) <= 0:
            raise ConfigError("llm.timeout_seconds має бути додатним")
        if int(config["llm"]["total_timeout_seconds"]) <= 0:
            raise ConfigError("llm.total_timeout_seconds має бути додатним")
        if not 0 <= int(config["fuzzy_threshold"]) <= 100:
            raise ConfigError("fuzzy_threshold має бути від 0 до 100")
        if int(config["security"]["pentest"]["max_hosts"]) < 1:
            raise ConfigError("security.pentest.max_hosts має бути не менше 1")
        if not 1 <= int(config["security"]["pentest"]["concurrency"]) <= 1000:
            raise ConfigError("security.pentest.concurrency має бути від 1 до 1000")
        supported = {"gemini", "groq", "openai", "anthropic", "ollama"}
        order = list(config["llm"]["order"])
        unknown = [name for name in order if name not in supported]
        if unknown:
            raise ConfigError(f"Невідомі LLM-провайдери: {', '.join(unknown)}")
        if len(order) != len(set(order)):
            raise ConfigError("llm.order не повинен містити дублікати")
        for field in ("input_device",):
            value = config["stt"].get(field)
            if value is not None and not isinstance(value, int):
                raise ConfigError(f"stt.{field} має бути числом або null")
        value = config["tts"].get("output_device")
        if value is not None and not isinstance(value, int):
            raise ConfigError("tts.output_device має бути числом або null")
        if not isinstance(config["user_directories"], list):
            raise ConfigError("user_directories має бути списком")
        if any(not isinstance(path, str) or not path.strip() for path in config["user_directories"]):
            raise ConfigError("user_directories має містити непорожні шляхи")
        if type(config["file_search"]["all_local_drives"]) is not bool:
            raise ConfigError("file_search.all_local_drives має бути true/false")
        budget = config["file_search"]["budget_seconds"]
        if type(budget) not in (int, float) or not 1 <= budget <= 60:
            raise ConfigError("file_search.budget_seconds має бути від 1 до 60")
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError(f"Некоректна структура config.json: {exc}") from exc


def load_settings(profile_override: str | None = None) -> Settings:
    root = Path(__file__).resolve().parent
    paths = ProjectPaths.from_root(root)
    paths.ensure()
    load_dotenv(paths.env_file)

    config = default_config()
    if paths.config_file.exists():
        try:
            custom = json.loads(paths.config_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"Некоректний JSON у {paths.config_file}: рядок {exc.lineno}, "
                f"стовпець {exc.colno}"
            ) from exc
        if not isinstance(custom, dict):
            raise ConfigError("Корінь config.json має бути JSON-об'єктом")
        config = merge_config(custom)
    config = apply_performance_profile(config, profile_override)
    validate_config(config)

    return Settings(
        paths=paths,
        language=config["language"],
        assistant_name=config["assistant_name"],
        confirmation_timeout_seconds=int(config["confirmation_timeout_seconds"]),
        fuzzy_threshold=int(config["fuzzy_threshold"]),
        stt_model_path=config["stt"]["model_path"],
        stt_sample_rate=int(config["stt"]["sample_rate"]),
        stt_audio_block_ms=int(config["stt"]["audio_block_ms"]),
        stt_endpoint_silence_ms=int(config["stt"]["endpoint_silence_ms"]),
        stt_endpoint_adaptive=config["stt"]["endpoint_adaptive"],
        stt_refinement_policy=str(config["stt"]["refinement_policy"]),
        stt_command_confidence_threshold=float(
            config["stt"]["command_confidence_threshold"]
        ),
        stt_chat_confidence_threshold=float(config["stt"]["chat_confidence_threshold"]),
        stt_post_tts_pause_seconds=float(config["stt"]["post_tts_pause_seconds"]),
        stt_whisper_enabled=bool(config["stt"]["whisper"]["enabled"]),
        stt_whisper_model=str(config["stt"]["whisper"]["model"]),
        stt_whisper_device=str(config["stt"]["whisper"]["device"]),
        stt_whisper_compute_type=str(
            config["stt"]["whisper"]["compute_type"]
        ),
        stt_whisper_cpu_threads=int(config["stt"]["whisper"]["cpu_threads"]),
        stt_whisper_beam_size=int(config["stt"]["whisper"]["beam_size"]),
        stt_whisper_silence_ms=int(config["stt"]["whisper"]["silence_ms"]),
        stt_whisper_min_confidence=float(
            config["stt"]["whisper"]["min_confidence"]
        ),
        stt_whisper_skip_silence=bool(
            config["stt"]["whisper"]["skip_silence"]
        ),
        stt_whisper_preload=bool(
            config["stt"]["whisper"]["preload"]
        ),
        stt_whisper_prompt=str(config["stt"]["whisper"].get("prompt", "")),
        stt_whisper_hotwords=str(
            config["stt"]["whisper"].get("hotwords", "")
        ),
        input_device=config["stt"].get("input_device"),
        output_device=config["tts"].get("output_device"),
        noise_threshold=float(config["stt"]["noise_threshold"]),
        tts_voice_hint=config["tts"]["voice_hint"],
        tts_rate=int(config["tts"]["rate"]),
        tts_volume=float(config["tts"]["volume"]),
        llm_order=list(config["llm"]["order"]),
        llm_models=dict(config["llm"]["models"]),
        llm_timeout_seconds=int(config["llm"]["timeout_seconds"]),
        llm_total_timeout_seconds=int(config["llm"]["total_timeout_seconds"]),
        llm_failures_before_switch=int(config["llm"]["failures_before_switch"]),
        history_limit=int(config["llm"]["history_limit"]),
        command_interpretation_enabled=config["commands"]["llm_interpretation"],
        natural_actions_enabled=config["commands"]["natural_actions"],
        command_interpretation_timeout_seconds=config["commands"]["interpretation_timeout_seconds"],
        dynamic_code_enabled=bool(config["security"]["dynamic_code_enabled"]),
        pentest_enabled=bool(config["security"]["pentest"]["enabled"]),
        pentest_max_hosts=int(config["security"]["pentest"]["max_hosts"]),
        pentest_connect_timeout_seconds=float(
            config["security"]["pentest"]["connect_timeout_seconds"]
        ),
        pentest_scan_timeout_seconds=float(
            config["security"]["pentest"]["scan_timeout_seconds"]
        ),
        pentest_concurrency=int(config["security"]["pentest"]["concurrency"]),
        check_updates=bool(config["updates"]["check"]),
        github_repository=config["updates"]["github_repository"],
        log_level=config["logging"]["level"],
        log_retention_days=int(config["logging"]["retention_days"]),
        application_aliases=dict(config["application_aliases"]),
        user_directories=list(config["user_directories"]),
        file_search_all_local_drives=config["file_search"]["all_local_drives"],
        file_search_budget_seconds=float(config["file_search"]["budget_seconds"]),
        performance_profile=str(config["performance"]["profile"]),
    )
