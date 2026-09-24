"""Saved or explicit apps -> approval -> launch -> process and window evidence."""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from core.atomic_json import AtomicJSONFile
from core.console import console_print
from core.confirmation import ConfirmationPrompt
from core.models import SkillResult
from core.security import sanitized_environment
from services.apps.workplace_windows import application_evidence, default_browser_executable

WORKPLACE = re.compile(r"^підготуй робоче місце(?:\s*:\s*|\s+|(?=[.!?]*$))(.*?)[.!?]*$", re.I)
WORK_MODE = re.compile(r'^(?:(?:увімкни|ввімкни|активуй)\s+)?режим\s+[«"“]?робота[»"”]?[.!?]*$', re.I)
CONFIGURE_WORKPLACE = re.compile(r"^налаштуй робоче місце(?:\s*:\s*|\s+|(?=[.!?]*$))(.*?)[.!?]*$", re.I)
CANCEL_WORKPLACE = re.compile(r"^команда\s*[:,]?\s*скасуй завдання[.!?]*$", re.I)
TASK_STATUS = re.compile(r"^(?:статус завдання|що вдалося|що з робочим місцем)[.!?]*$", re.I)
TASK_RETRY = re.compile(r"^(?:повтори (?:невдалий крок|невдалі кроки|незавершені кроки)|продовж робоче завдання)[.!?]*$", re.I)


class WorkplaceAgent:
    def __init__(self, indexer, profile_path=None):
        self.indexer = indexer
        self.profile_path = Path(profile_path) if profile_path is not None else None
        self.verification_timeout = 8.0
        self.current = None
        self.busy = False
        self.cancelled = asyncio.Event()
        self._last_task = None

    def clear_context(self):
        if not self.busy:
            self._last_task = None
            self.current = None

    async def followup(self, command, context):
        if self.busy:
            return SkillResult(True, "Завдання ще виконується. Можна скасувати наступні кроки.")
        previous = self._last_task
        if previous is None or time.monotonic() >= previous["expires"]:
            self._last_task = None
            return SkillResult(True, "Немає свіжого робочого завдання. Скажіть: Команда, режим робота.")
        if TASK_STATUS.fullmatch(command.strip()):
            return SkillResult(True, previous["summary"], {"command_type": "workplace_status"})
        if not TASK_RETRY.fullmatch(command.strip()):
            return SkillResult(False)
        names = previous["remaining"]
        if not names:
            return SkillResult(True, "Незавершених кроків немає. Нічого повторно не запускаю.")
        # A fresh plan re-resolves names and requires NEW approval; no replay of paths/permissions.
        return await self.run("підготуй робоче місце", context, _names=names)

    def snapshot(self):
        if self.current is None:
            return None
        return {**{k: v for k, v in self.current.items() if k != "steps"},
                "active": self.busy, "cancel_requested": self.cancelled.is_set(),
                "steps": [dict(step) for step in self.current["steps"]]}

    def cancel(self, task_id):
        if not self.busy or not self.current or self.current["id"] != task_id:
            return False
        self.cancelled.set()
        self.current["detail"] = "Зупиняю наступні кроки. Уже відкриті програми залишаться відкритими."
        return True

    @staticmethod
    def _key(value):
        return " ".join(value.casefold().split())

    def _resolve(self, name):
        if self._key(name) == "браузер":
            return self._target("Браузер за замовчуванням", default_browser_executable())
        labels = {self._key(name)}
        if self._key(name) in {"vs code", "vscode", "вс код"}:
            labels.update({"visual studio code", "microsoft visual studio code", "microsoft visual studio code (user)"})
        if self._key(name) in {"telegram", "телеграм"}:
            labels.update({"telegram", "telegram desktop"})
        matches = [app for app in self.indexer.all()
                   if any(self._key(label) in labels
                          for label in [app["name"], *app.get("aliases", [])])]
        targets = []
        for app in matches:
            try:
                targets.append(self._target(app["name"], app["command"]))
            except (ValueError, OSError):
                continue  # An unsafe/stale shortcut cannot override a valid indexed EXE.
        unique = {os.path.normcase(item["path"]): item for item in targets}
        if len(unique) != 1:
            raise ValueError(f"«{name}»: потрібна одна точна назва або налаштований псевдонім програми.")
        return next(iter(unique.values()))

    @staticmethod
    def _target(name, command):
        if str(command).startswith(("\\\\", "//")):
            raise ValueError(f"«{name}»: мережеві шляхи не підтримуються.")
        path = Path(command).resolve(strict=True)
        if path.suffix.lower() == ".lnk":
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            shell = shortcut = None
            try:
                shell = win32com.client.Dispatch("WScript.Shell")
                shortcut = shell.CreateShortcut(str(path))
                if shortcut.Arguments:
                    raise ValueError(f"«{name}»: ярлик має аргументи; цей сценарій їх не виконує.")
                if str(shortcut.TargetPath).startswith(("\\\\", "//")):
                    raise ValueError(f"«{name}»: мережеві ярлики не підтримуються.")
                path = Path(shortcut.TargetPath).resolve(strict=True)
            finally:
                shortcut = shell = None  # Release COM interfaces before uninitializing this thread.
                pythoncom.CoUninitialize()
        if not path.is_file() or path.suffix.lower() != ".exe" or str(path).startswith("\\\\"):
            raise ValueError(f"«{name}»: потрібен локальний EXE або ярлик на EXE без аргументів.")
        stat = path.stat()
        return {"name": name, "path": str(path), "stamp": (stat.st_size, stat.st_mtime_ns)}

    def _profile_names(self):
        if self.profile_path is None or not self.profile_path.exists():
            raise ValueError("Робоче місце ще не налаштовано. Скажіть: Команда, налаштуй робоче місце: браузер, VS Code, Telegram.")
        # Fail closed on damaged profiles: do not silently launch a fallback list.
        if self.profile_path.stat().st_size > 8192:
            raise ValueError("Профіль робочого місця завеликий. Налаштуйте список повторно.")
        try:
            data = json.loads(self.profile_path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Профіль робочого місця пошкоджений. Налаштуйте список повторно.") from exc
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("Невідомий формат профілю робочого місця.")
        return data.get("applications")

    async def _evidence(self, path, timeout=3):
        return await asyncio.wait_for(asyncio.to_thread(application_evidence, path), timeout)

    async def _verify_window(self, path):
        deadline = asyncio.get_running_loop().time() + self.verification_timeout
        evidence = {"process": False, "window": False}
        while not self.cancelled.is_set():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                evidence = await self._evidence(path, min(3, remaining))
            except TimeoutError:
                break
            if evidence["window"]:
                break
            try:
                await asyncio.wait_for(self.cancelled.wait(), min(.5, max(.001, deadline - asyncio.get_running_loop().time())))
            except TimeoutError:
                pass
        return evidence

    async def _approve(self, context, prompt):
        approval = asyncio.create_task(context.confirm(prompt))
        cancellation = asyncio.create_task(self.cancelled.wait())
        try:
            await asyncio.wait({approval, cancellation}, return_when=asyncio.FIRST_COMPLETED)
            if self.cancelled.is_set():
                return False
            return approval.result()
        finally:
            for task in (approval, cancellation):
                if not task.done():
                    task.cancel()
            await asyncio.gather(approval, cancellation, return_exceptions=True)

    async def run(self, command, context, *, _names=None):
        if self.busy:
            return SkillResult(True, "Спочатку завершіть або скасуйте поточне завдання.")
        if os.name != "nt" or context.services["state"].get("mode") != "chat":
            return SkillResult(True, "Цей сценарій доступний у звичайному режимі на Windows.")
        # Short activation alias; not a persistent permission/mode change.
        command = command.strip()
        if WORK_MODE.fullmatch(command):
            command = "підготуй робоче місце"
        configuring = CONFIGURE_WORKPLACE.fullmatch(command.strip())
        match = configuring or WORKPLACE.fullmatch(command.strip())
        try:
            names = _names if _names is not None else ([part.strip() for part in match.group(1).split(",")]
                     if match and match.group(1).strip() else self._profile_names() if match and not configuring else [])
        except (ValueError, OSError) as exc:
            return SkillResult(True, str(exc))
        if (not isinstance(names, list) or not 1 <= len(names) <= 3
                or any(not isinstance(name, str) or not name.strip() or len(name) > 100 for name in names)):
            return SkillResult(True, "Назвіть від 1 до 3 програм через кому: "
                               "Команда: підготуй робоче місце: Chrome, Telegram. Нічого не запущено.")
        if len({self._key(name) for name in names}) != len(names):
            return SkillResult(True, "Назвіть кожну програму лише один раз.")
        self.busy = True
        self._last_task = None
        self.cancelled.clear()
        self.current = {"id": uuid4().hex, "title": "Налаштувати робоче місце" if configuring else "Підготувати робочі програми",
                        "kind": "configure" if configuring else "launch",
                        "status": "planning", "detail": "Перевіряю весь список до запуску.",
                        "steps": [{"name": name, "requested_name": name, "status": "pending", "detail": "Перевірити програму"}
                                  for name in names]}
        handles = []
        try:
            targets = []
            for index, name in enumerate(names):
                target = await asyncio.wait_for(asyncio.to_thread(self._resolve, name), 10)
                if self.cancelled.is_set():
                    return self._finish("cancelled", "План скасовано. Нічого не запущено.")
                if any(os.path.normcase(item["path"]) == os.path.normcase(target["path"]) for item in targets):
                    raise ValueError("Дві назви вказують на ту саму програму. Уточніть список.")
                targets.append(target)
                self.current["steps"][index]["name"] = target["name"]
                self.current["steps"][index]["detail"] = target["path"]
            self.current["status"] = "awaiting_confirmation"
            self.current["detail"] = "План готовий. До підтвердження програми не запускаються."
            listing = "; ".join(f"{i}. {item['name']} ({item['path']})" for i, item in enumerate(targets, 1))
            console_print("План: " + listing)
            if configuring:
                if self.profile_path is None:
                    raise ValueError("Сховище профілю не налаштоване.")
                if not await self._approve(context, ConfirmationPrompt(
                        "збереження списку робочого місця: " + listing + ". Програми не запускатиму",
                        "Зберегти робоче місце без запуску: " + ", ".join(names),
                        caller_reports_result=True, short_question=True)):
                    return self._finish("cancelled", "Збереження скасовано. Профіль не змінено.")
                AtomicJSONFile(self.profile_path, {}).save({"version": 1, "applications": names})
                for step in self.current["steps"]:
                    step.update(status="saved", detail="Назву збережено; запуск не виконувався.")
                result = self._finish("saved", "Робоче місце збережено. Для запуску: Команда, підготуй робоче місце.")
                result.data.update(success=True)
                return result
            if not await self._approve(context, ConfirmationPrompt(
                    "послідовний запуск програм: " + listing + ". Перевірю процеси й видимі вікна. Фонові програми повторно не запускаю",
                    "Запустити робоче місце: " + ", ".join(names),
                    caller_reports_result=True, short_question=True)):
                return self._finish("cancelled", "План не підтверджено або скасовано. Нічого не запущено.")
            self.current["status"] = "running"
            for index, target in enumerate(targets):
                step = self.current["steps"][index]
                if self.cancelled.is_set():
                    return self._finish("cancelled", "Наступні кроки скасовано. Уже запущені програми не закриваю.")
                step.update(status="running", detail="Повторно перевіряю програму перед запуском.")
                fresh = await asyncio.wait_for(asyncio.to_thread(self._resolve, names[index]), 10)
                if fresh != target:
                    raise ValueError("Програма або індекс змінилися після підтвердження. Решту кроків зупинено.")
                evidence = await self._evidence(target["path"])
                if self.cancelled.is_set():
                    return self._finish("cancelled", "Наступні кроки скасовано. Уже запущені програми не закриваю.")
                if evidence["window"]:
                    step.update(status="verified", detail="Процес і видиме вікно вже знайдено. Повторно не запускав.")
                    continue
                # No shell, arguments, model-generated paths, elevation or automatic retry.
                step.update(status="verifying", detail="Очікую видиме вікно програми…")
                if not evidence["process"]:
                    handles.append(subprocess.Popen([target["path"]], cwd=str(Path(target["path"]).parent),
                                                    env=sanitized_environment()))
                evidence = await self._verify_window(target["path"])
                if self.cancelled.is_set():
                    return self._finish("cancelled", "Решту кроків скасовано. Уже запущені програми не закриваю.")
                if not evidence["window"]:
                    detail = ("Процес працює, але видиме вікно не підтверджено (можливо, програма у треї)."
                              if evidence["process"] else "Процес і вікно не підтверджено. Це не доказ невдалого запуску.")
                    step.update(status="unknown", detail=detail)
                    return self._finish("partial", target["name"] + ": " + detail + " Решту кроків зупинено; запуск не повторюю.")
                step.update(status="verified", detail="Процес із погодженим EXE та його видиме вікно знайдено.")
            return self._finish("completed", "Робоче місце підготовлено: процеси й видимі вікна всіх програм знайдено. Вхід в акаунти та готовність проєктів не перевіряв.")
        except asyncio.CancelledError:
            self._finish("interrupted", "Виконання перервано. Перевірте запущені програми; автоматичного повтору немає.")
            raise
        except (ValueError, OSError) as exc:
            return self._finish("failed", str(exc))
        except Exception:
            return self._finish("failed", "Не вдалося завершити перевірку. Решту кроків зупинено; автоматичного повтору немає.")
        finally:
            self.busy = False
            # Poll closes handles of exited children; running applications remain untouched.
            for handle in handles:
                try:
                    handle.poll()
                except OSError:
                    pass

    def _finish(self, status, detail):
        self.current.update(status=status, detail=detail)
        for step in self.current["steps"]:
            if step["status"] in {"running", "verifying"}:
                step.update(status="unknown", detail="Крок не перевірено до кінця.")
            elif step["status"] == "pending":
                step.update(status="skipped", detail="Запуск цього кроку не виконувався.")
        verified = [step["name"] for step in self.current["steps"] if step["status"] == "verified"]
        summary = detail + (" Вікна знайдено: " + ", ".join(verified) + "." if verified else "")
        if self.current["kind"] == "launch":
            self._last_task = {"expires": time.monotonic() + 300, "summary": summary,
                               "remaining": [step["requested_name"] for step in self.current["steps"] if step["status"] != "verified"]}
        console_print("Завдання: " + summary)
        command_type = "workplace_configured" if self.current["kind"] == "configure" else "agent_workplace"
        return SkillResult(True, summary, {"command_type": command_type, "accepted": True,
                                          "success": status == "completed", "task_id": self.current["id"]})
