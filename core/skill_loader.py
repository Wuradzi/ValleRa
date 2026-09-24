from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LoadedSkill:
    name: str
    description: str
    triggers: list[str]
    platforms: list[str]
    module: ModuleType
    services: dict[str, Any]

    async def can_handle(self, command: str) -> bool:
        function = getattr(self.module, "can_handle", None)
        if function is None:
            return True
        if inspect.iscoroutinefunction(function):
            return bool(await function(command, self.services))
        return bool(await asyncio.to_thread(function, command, self.services))

    async def handle(self, command: str, context):
        function = self.module.handle
        if inspect.iscoroutinefunction(function):
            return await function(command, context, self.services)
        return await asyncio.to_thread(function, command, context, self.services)


class SkillLoader:
    def __init__(self, skills_dir: Path, services: dict[str, Any]):
        self.skills_dir = skills_dir
        self.services = services

    def load(self) -> list[LoadedSkill]:
        result = []
        names: set[str] = set()
        for manifest_path in sorted(self.skills_dir.glob("*/manifest.json")):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                self._validate_manifest(manifest, manifest_path)
            except Exception as exc:
                logger.warning(
                    "Некоректний manifest навички %s: %s",
                    manifest_path,
                    exc,
                )
                continue
            if not manifest.get("enabled", True):
                continue
            if manifest["name"] in names:
                logger.warning("Дублікат назви навички %s пропущено", manifest["name"])
                continue
            skill_path = manifest_path.parent / "skill.py"
            if not skill_path.is_file():
                logger.warning("Файл навички відсутній: %s", skill_path)
                continue
            spec = importlib.util.spec_from_file_location(
                f"valera_skill_{manifest_path.parent.name}",
                skill_path,
            )
            if spec is None or spec.loader is None:
                logger.warning("Не вдалося завантажити %s", skill_path)
                continue
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception:
                logger.exception("Не вдалося імпортувати навичку: %s", skill_path)
                continue
            if not callable(getattr(module, "handle", None)):
                logger.warning("Навичка %s не має функції handle", skill_path)
                continue
            result.append(
                LoadedSkill(
                    name=manifest["name"],
                    description=manifest.get("description", ""),
                    triggers=list(manifest.get("triggers", [])),
                    platforms=list(manifest.get("platforms", ["windows"])),
                    module=module,
                    services=self.services,
                )
            )
            names.add(manifest["name"])
        return result

    @staticmethod
    def _validate_manifest(manifest: Any, path: Path) -> None:
        if not isinstance(manifest, dict):
            raise ValueError(f"{path}: очікувався JSON-об'єкт")
        if not isinstance(manifest.get("name"), str) or not manifest["name"].strip():
            raise ValueError(f"{path}: поле name обов'язкове")
        for key in ("triggers", "platforms"):
            value = manifest.get(key, [] if key == "triggers" else ["windows"])
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"{path}: поле {key} має бути списком рядків")
