from __future__ import annotations

import asyncio
import importlib
import platform
from datetime import datetime, timezone
from typing import Any

from core.atomic_json import AtomicJSONFile


class DiagnosticsService:
    REQUIRED_MODULES = (
        "vosk",
        "sounddevice",
        "pyttsx3",
        "psutil",
        "cryptography",
        "httpx",
    )

    def __init__(
        self,
        settings,
        secret_store,
        listener,
        speaker,
        llm,
        pentest,
    ):
        self.settings = settings
        self.secret_store = secret_store
        self.listener = listener
        self.speaker = speaker
        self.llm = llm
        self.pentest = pentest

    async def run(self) -> tuple[str, dict[str, Any]]:
        checks: list[dict[str, Any]] = []

        self._add(
            checks,
            "performance-profile",
            True,
            (
                f"{self.settings.performance_profile}; "
                f"Whisper beam={self.settings.stt_whisper_beam_size}; "
                f"preload={self.settings.stt_whisper_preload}"
            ),
        )

        for module in self.REQUIRED_MODULES:
            try:
                importlib.import_module(module)
                self._add(checks, f"module:{module}", True, "доступний")
            except Exception as exc:
                self._add(checks, f"module:{module}", False, str(exc))

        await self._check_stt(checks)
        await self._check_audio_devices(checks)
        tts_ok, tts_detail = await self.speaker.test_voice_generation()
        self._add(checks, "tts-generation", tts_ok, tts_detail)
        self._add(
            checks,
            "tts-worker",
            self.speaker.healthy,
            "працює" if self.speaker.healthy else "не запущений",
        )
        self._check_storage(checks)
        self._add(
            checks,
            "secret-store",
            not self.secret_store.exists or self.secret_store.unlocked,
            "розблоковано"
            if self.secret_store.unlocked
            else "сховище відсутнє або заблоковане",
        )
        self._add(
            checks,
            "pentest-engine",
            self.pentest.enabled,
            "доступний" if self.pentest.enabled else "вимкнений у конфігурації",
            required=False,
        )

        statuses = await self.llm.check_all()
        for name, status in statuses.items():
            self._add(
                checks,
                f"llm:{name}",
                status.available,
                status.detail,
                required=False,
            )
        available_llms = [
            name for name, status in statuses.items() if status.available
        ]
        self._add(
            checks,
            "llm:any",
            bool(available_llms),
            ", ".join(available_llms) if available_llms else "немає доступних провайдерів",
        )
        recovery_file = self.settings.paths.project_root / "RECOVERY_KEY.txt"
        self._add(
            checks,
            "recovery-key-local-copy",
            not recovery_file.exists(),
            (
                "локальної копії немає"
                if not recovery_file.exists()
                else "перенесіть ключ на окремий захищений носій і видаліть локальну копію"
            ),
            required=False,
            severity="warning",
        )

        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "checks": checks,
            "ok": all(check["ok"] for check in checks if check["required"]),
        }
        path = self.settings.paths.data_dir / "diagnostics.json"
        AtomicJSONFile(path, {}).save(report)

        failed = [
            check["name"]
            for check in checks
            if check["required"] and not check["ok"]
        ]
        warnings = [
            check["name"]
            for check in checks
            if not check["required"] and not check["ok"]
        ]
        if failed:
            summary = (
                f"Самодіагностику завершено. Успішно: {len(checks) - len(failed)} "
                f"із {len(checks)}. Проблеми: {', '.join(failed)}."
            )
        else:
            summary = f"Самодіагностику завершено. Усі {len(checks)} перевірок успішні."
        summary += (
            f" Доступні LLM: {', '.join(available_llms)}."
            if available_llms
            else " Доступних LLM немає."
        )
        if warnings:
            summary += f" Необов'язкові попередження: {', '.join(warnings)}."
        summary += f" Повний звіт: {path}"
        return summary, report

    async def _check_stt(self, checks: list[dict[str, Any]]) -> None:
        if self.listener is None:
            self._add(checks, "stt-model", False, "текстовий режим")
            return
        try:
            await asyncio.to_thread(self.listener._get_model)
            self._add(checks, "stt-model", True, str(self.listener.model_path))
        except Exception as exc:
            self._add(checks, "stt-model", False, str(exc))

        whisper = getattr(self.listener, "whisper", None)
        if whisper is not None:
            ok, detail = whisper.status()
            self._add(checks, "stt-whisper", ok, detail, required=False)

    async def _check_audio_devices(self, checks: list[dict[str, Any]]) -> None:
        try:
            microphone_ok, detail = await asyncio.to_thread(
                self.listener.probe_input,
            )
            self._add(
                checks,
                "microphone",
                microphone_ok,
                detail,
            )
        except Exception as exc:
            self._add(checks, "microphone", False, str(exc))

        try:
            import sounddevice as sd

            output_device = await asyncio.to_thread(
                sd.query_devices,
                self.settings.output_device,
                "output",
            )
            output_channels = int(output_device.get("max_output_channels", 0))
            self._add(
                checks,
                "audio-output",
                output_channels > 0,
                f"{output_device.get('name', 'невідомий')}; каналів: {output_channels}",
            )
        except Exception as exc:
            self._add(checks, "audio-output", False, str(exc))

    def _check_storage(self, checks: list[dict[str, Any]]) -> None:
        path = self.settings.paths.data_dir / ".diagnostics-write-test"
        try:
            path.write_text("ok", encoding="utf-8")
            ok = path.read_text(encoding="utf-8") == "ok"
            self._add(checks, "data-storage", ok, str(self.settings.paths.data_dir))
        except Exception as exc:
            self._add(checks, "data-storage", False, str(exc))
        finally:
            path.unlink(missing_ok=True)

    @staticmethod
    def _add(
        checks: list[dict[str, Any]],
        name: str,
        ok: bool,
        detail: str,
        *,
        required: bool = True,
        severity: str = "error",
    ) -> None:
        checks.append(
            {
                "name": name,
                "ok": bool(ok),
                "detail": detail,
                "required": bool(required),
                "severity": severity if not ok else "info",
            }
        )
