from __future__ import annotations

import asyncio
import logging
import math
import threading
import time

from core.command_router import CommandRouter
from core.confirmation import ConfirmationService
from core.listen import VoskListener
from core.metrics import MetricsCollector
from core.performance import CURRENT_TURN, DISABLED_PERFORMANCE, PerformanceRecorder, TurnTiming
from core.models import RecognitionResult
from core.console import console_input, console_print
from core.processor import COMMAND_PREFIX, STOP_SPEECH, PAUSE_CONVERSATION, CommandProcessor
from core.security import redact_user_text, scrub_sensitive_environment
from core.skill_loader import SkillLoader
from core.task_context import TaskContext
from core.speak import SPEECH_SCOPE, Speaker
from core.voice_text import repair_voice_text
from services.apps.controller import ApplicationController
from services.apps.indexer import ApplicationIndexer
from services.apps.workplace import CANCEL_WORKPLACE, WorkplaceAgent
from services.diagnostics import DiagnosticsService
from services.files.file_service import FileService
from services.llm.manager import LLMManager
from services.pentest import PentestService
from services.storage.memory_store import MemoryStore
from services.storage.notes_store import NotesStore
from services.storage.reminder_store import ReminderStore
from services.update_checker import UpdateChecker
from services.web.search import WebSearchService
from services.web.answers import WebAnswerService
from services.web.weather import WeatherService
from services.windows.window_controller import WindowController

logger = logging.getLogger(__name__)


class ValleRaApp:
    def __init__(self, settings, secret_store, text_only: bool = False, *, performance=None):
        self.settings = settings
        self.text_only = text_only
        self.command_queue: asyncio.Queue[tuple[str, str, float] | tuple[str, str, float, TurnTiming | None]] = asyncio.Queue(
            maxsize=1
        )
        self.metrics = MetricsCollector(settings.paths.data_dir / "metrics.jsonl")
        self.performance = performance or PerformanceRecorder(self.metrics)
        self.speaker = Speaker(settings)
        self.speaker.performance = self.performance
        self.listener = None if text_only else VoskListener(settings)
        if self.listener is not None:
            self.listener.performance = self.performance
            self.listener.whisper.performance = self.performance
        self.llm = LLMManager(settings)
        self.llm.performance = self.performance
        # Providers already copied the configured keys. Do not leave credentials
        # in the process environment where child processes can inherit them.
        scrub_sensitive_environment()
        self.running = True
        self.command_idle = asyncio.Event()
        self.command_idle.set()
        self._tasks: list[asyncio.Task] = []
        self._console_thread: threading.Thread | None = None
        self.web_ui = None
        self.microphone_enabled = not text_only
        self._microphone_epoch = 0
        self._voice_inflight = False
        self._voice_error = False
        self._voice_unavailable = text_only
        self._mic_ready = asyncio.Event()
        if self.microphone_enabled:
            self._mic_ready.set()

        memory = MemoryStore(settings.paths.data_dir / "memory.json")
        notes = NotesStore(settings.paths.data_dir / "notes.json")
        reminders = ReminderStore(settings.paths.data_dir / "reminders.json")
        pentest = PentestService(
            settings.paths.data_dir,
            enabled=settings.pentest_enabled,
            max_hosts=settings.pentest_max_hosts,
            connect_timeout=settings.pentest_connect_timeout_seconds,
            scan_timeout=settings.pentest_scan_timeout_seconds,
            concurrency=settings.pentest_concurrency,
        )
        app_indexer = ApplicationIndexer(
            settings.paths.data_dir / "applications.json",
            settings.application_aliases,
        )
        services = {
            "memory": memory,
            "secrets": secret_store,
            "notes": notes,
            "reminders": reminders,
            "app_indexer": app_indexer,
            "workplace": WorkplaceAgent(app_indexer, settings.paths.data_dir / "workplace.json"),
            "apps": ApplicationController(app_indexer),
            "windows": WindowController(),
            "files": FileService(settings.user_directories,
                                 all_local_drives=settings.file_search_all_local_drives,
                                 budget_seconds=settings.file_search_budget_seconds),
            "web": WebSearchService(),
            "web_answers": WebAnswerService(self.llm),
            "weather": WeatherService(),
            "metrics": self.metrics,
            "performance": self.performance,
            "pentest": pentest,
            "speaker": self.speaker,
            "llm": self.llm,
            "state": {
                "mode": "chat",
            },
            "tasks": TaskContext(),
        }
        services["diagnostics"] = DiagnosticsService(
            settings,
            secret_store,
            self.listener,
            self.speaker,
            self.llm,
            pentest,
        )
        self.services = services

        skills = SkillLoader(settings.paths.project_root / "skills", services).load()
        router = CommandRouter(skills, settings.fuzzy_threshold)
        services["enabled_skills"] = {skill.name for skill in router.skills}
        self.llm.enabled_skills = services["enabled_skills"]
        self.processor = CommandProcessor(
            settings, router, self.llm, self.speaker, self.metrics, services
        )
        self.confirmation = ConfirmationService(
            self._say_confirmation,
            settings.confirmation_timeout_seconds,
            self.speaker.wait_until_idle,
        )

    async def _say_confirmation(self, text):
        # Safety prompts are not obsolete just because conversational output
        # was interrupted while a local operation was preparing confirmation.
        token = SPEECH_SCOPE.set(None)
        try:
            await self.speaker.say(text)
        finally:
            SPEECH_SCOPE.reset(token)

    async def run(self) -> None:
        startup_started = asyncio.get_running_loop().time()
        await self.speaker.start()
        statuses = self.llm.provisional_statuses()
        self.llm.select_startup_provider(statuses)

        if self.web_ui is not None:
            self.speaker.on_text = lambda text: self.web_ui.publish("assistant", text)
            self.speaker.on_playback = lambda: self.web_ui.publish("playback")
            # High-frequency visual telemetry must not evict conversation events.
            self.speaker.on_glow = self.web_ui.changed.set

        console_print("=== ValleRa запущена ===")
        console_print("Текст «стоп» зупиняє відповідь; нове текстове повідомлення перериває попередню озвучку.")
        print(
            f"[PERF] Профіль: {self.settings.performance_profile}; "
            f"Whisper beam={self.settings.stt_whisper_beam_size}; "
            f"CPU threads={self.settings.stt_whisper_cpu_threads or 'auto'}; "
            f"audio block={getattr(self.settings, 'stt_audio_block_ms', 250)} мс; "
            f"quiet endpoint={getattr(self.settings, 'stt_endpoint_silence_ms', 0) or 'native'}; "
            f"preload={'так' if self.settings.stt_whisper_preload else 'ні'}."
        )
        if self.text_only:
            console_print("Введіть повідомлення. Явний резервний формат: «Команда: <дія>».")
        else:
            console_print("Говоріть природно — ValleRa слухає без слова активації.")
            console_print("Прохання без слова Команда аналізує LLM; дії виконуються після підтвердження."
                          if self.settings.natural_actions_enabled else
                          "Звичайна репліка → розмова; «Команда: <дія>» → локальна дія.")
            whisper_ok, whisper_detail = self.listener.whisper.status()
            mode = "Vosk + Whisper" if whisper_ok else "лише Vosk"
            policy = getattr(self.settings, "stt_refinement_policy", "legacy")
            print(f"[STT] Режим: {mode}; policy={policy} — {whisper_detail}")
        startup_ms = round(
            (asyncio.get_running_loop().time() - startup_started) * 1000,
            2,
        )
        self.metrics.record("startup_ready", duration_ms=startup_ms)
        perf = getattr(self, "performance", DISABLED_PERFORMANCE)
        ready_ms = (time.perf_counter() - perf.started) * 1000 if perf.enabled else startup_ms
        perf.record("startup.interface_since_unlock", ready_ms)
        print(f"[PERF] Інтерфейс готовий за {ready_ms:.0f} мс після розблокування; backends готуються у фоні.")

        tasks = [
            asyncio.create_task(self._text_input_loop(), name="text-input"),
            asyncio.create_task(self._command_loop(), name="commands"),
            asyncio.create_task(self._reminder_loop(), name="reminders"),
            asyncio.create_task(
                self._initialize_backends(),
                name="backend-initialization",
            ),
        ]
        if not self.text_only:
            tasks.append(asyncio.create_task(self._voice_loop(), name="voice"))
        self._tasks = tasks

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            if self.running:
                raise
        finally:
            self.running = False
            files = self.services.get("files")
            if files is not None:
                files.close()
            if self.web_ui is not None:
                await self.web_ui.close()
            if self.listener is not None:
                self.listener.interrupt()
            await self.services["pentest"].deactivate()
            for task in tasks:
                task.cancel()
            cleanup = [self.speaker.close(), self.llm.close()]
            if self.listener is not None:
                cleanup.append(asyncio.to_thread(self.listener.close))
            await asyncio.gather(*cleanup, return_exceptions=True)
            await asyncio.gather(*tasks, return_exceptions=True)
            self._tasks = []

    async def _initialize_backends(self) -> None:
        started = asyncio.get_running_loop().time()
        perf = getattr(self, "performance", DISABLED_PERFORMANCE)
        jobs = [
            asyncio.create_task(perf.measure("startup.llm_checks", self.llm.check_all()), name="llm-healthcheck"),
            asyncio.create_task(
                perf.measure("startup.app_index", asyncio.to_thread(self.services["app_indexer"].rebuild)),
                name="app-index",
            ),
            asyncio.create_task(perf.measure("startup.update_check", self._check_updates()), name="update-check"),
            asyncio.create_task(perf.measure("startup.tts_prepare", self.speaker.prepare()), name="tts-prepare"),
        ]
        if self.listener is not None:
            jobs.append(
                asyncio.create_task(perf.measure("startup.stt_total", self._prepare_stt()), name="stt-prepare")
            )

        try:
            statuses = await jobs[0]
            self.llm.select_startup_provider(statuses)
            results = await asyncio.gather(*jobs[1:], return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.error("Background initialization failed: %s", result)
            duration_ms = round(
                (asyncio.get_running_loop().time() - started) * 1000,
                2,
            )
            self.metrics.record("backends_ready", duration_ms=duration_ms)
            perf.record("startup.backends_wall", duration_ms,
                        status="error" if any(isinstance(item, Exception) for item in results) else "ok")
            print(f"[PERF] Фонову підготовку завершено за {duration_ms:.0f} мс; стан кожного компонента — вище.")
        finally:
            for job in jobs:
                if not job.done():
                    job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)

    async def _prepare_stt(self) -> None:
        if self.listener is None:
            return
        perf = getattr(self, "performance", DISABLED_PERFORMANCE)
        await perf.measure("startup.vosk_ready", asyncio.to_thread(self.listener._get_model))
        if self.listener.whisper.enabled and self.settings.stt_whisper_preload:
            with perf.span("startup.whisper_ready") as measurement:
                ok, _ = await asyncio.to_thread(self.listener.whisper.prepare)
                if not ok:
                    measurement.status = "unavailable"

    async def _check_updates(self) -> None:
        if not self.settings.check_updates or not self.settings.github_repository:
            return
        try:
            latest = await UpdateChecker(self.settings.github_repository).check()
            if latest:
                console_print(f"Доступний реліз {latest}. Оновлення виконується вручну.")
        except Exception:
            logger.exception("Update check failed")

    async def _command_loop(self) -> None:
        while self.running:
            item = await self.command_queue.get()
            text, source, confidence = item[:3]
            timing = item[3] if len(item) > 3 else None
            if timing is None:
                timing = TurnTiming(getattr(self, "performance", DISABLED_PERFORMANCE))
            token = CURRENT_TURN.set(timing)
            speech_token = SPEECH_SCOPE.set((self.speaker, getattr(self.speaker, "generation", 0)))
            timing.mark("dispatch")
            self.command_idle.clear()
            try:
                if not text.strip():
                    continue

                if source == "voice":
                    is_local = (
                        self.services["state"].get("mode") == "pentest"
                        or COMMAND_PREFIX.match(text.strip())
                        or (self.services.get("tasks") is not None
                            and self.services["tasks"].is_selection_reply(text))
                    )
                    threshold = (
                        self.settings.stt_command_confidence_threshold if is_local
                        else self.settings.stt_chat_confidence_threshold
                    )
                    if not math.isfinite(confidence) or confidence < threshold:
                        await self.speaker.say(
                            "Не розібрав фразу. Повторіть її, будь ласка."
                        )
                        continue

                result = await self.processor.process(
                    text,
                    self.confirmation.ask,
                    source,
                )
                if self.web_ui is not None:
                    if result.data.get("command_type") == "conversation_new":
                        self.web_ui.publish("new_conversation")
                    for item in result.data.get("web_results", []):
                        self.web_ui.publish("source", item.get("title", "Джерело"),
                                            href=str(item.get("href", ""))[:2000])
                    for item in result.data.get("task_choices", []):
                        self.web_ui.publish("notice", item.get("location", ""))
                for index, item in enumerate(result.data.get("web_results", []), 1):
                    number = item.get("source_id", index)
                    published = item.get("published") or "не встановлено"
                    console_print(f"{number}. {item['title']} — {item['href']}")
                    if "retrieved" in item:
                        console_print(f"   Публікація (зі сторінки): {published}; прочитано: {item['retrieved']}")
                for index, item in enumerate(result.data.get("task_choices", []), 1):
                    console_print(f"{index}. {item['location']}")
                if result.response and not result.data.get("response_spoken"):
                    await self.speaker.say(result.response)
                if result.data.get("shutdown_app"):
                    await self.speaker.wait_until_idle()
                    self.running = False
                    current = asyncio.current_task()
                    for task in self._tasks:
                        if task is not current:
                            task.cancel()
                    return
            finally:
                if self.web_ui is not None:
                    self.web_ui.publish("response_end")
                SPEECH_SCOPE.reset(speech_token)
                CURRENT_TURN.reset(token)
                self.command_queue.task_done()
                self.command_idle.set()

    async def _voice_loop(self) -> None:
        while self.running:
            try:
                await self._mic_ready.wait()
                await self._wait_for_input_slot()
                await self._wait_for_speaker()
                if not self.microphone_enabled:
                    continue

                if self.confirmation.awaiting:
                    result = await self._listen_for_turn(
                        float(self.settings.confirmation_timeout_seconds),
                        ConfirmationService.GRAMMAR,
                    )
                    if result.text:
                        if self.web_ui is not None:
                            self.web_ui.publish("user", result.text, source="voice")
                        safe_text = redact_user_text(result.text)
                        print(
                            f"[CONFIRM VOICE/{result.engine} "
                            f"{result.confidence:.2f}] {safe_text}"
                        )
                        if self.confirmation.submit(result.text):
                            await self.confirmation.wait_until_response_processed()
                    continue

                result = await self._listen_for_turn(
                    15.0,
                    None,
                )
                if result.engine == "conflict":
                    await self.speaker.say(
                        "Розпізнавачі не погодилися щодо фрази. Повторіть прохання, будь ласка; нічого не виконано."
                    )
                    continue
                if not result.text:
                    continue

                print(
                    f"[VOICE/{result.engine} {result.confidence:.2f}] "
                    f"{redact_user_text(result.text)}"
                )
                repaired_text = repair_voice_text(
                    result.text,
                    self.services["state"].get("mode", "chat"),
                )
                if repaired_text != result.text:
                    print(
                        "[VOICE REPAIR] "
                        f"{redact_user_text(result.text)} → "
                        f"{redact_user_text(repaired_text)}"
                    )
                self.command_idle.clear()
                if self.web_ui is not None:
                    self.web_ui.publish("user", repaired_text, source="voice")
                await self.command_queue.put(
                    (repaired_text, "voice", result.confidence, result.timing)
                )
            except FileNotFoundError as exc:
                self._voice_error = True
                self._voice_unavailable = True
                self.microphone_enabled = False
                self._mic_ready.clear()
                if self.web_ui is not None:
                    self.web_ui.publish("notice", "Голосове введення недоступне. Перевірте журнал сесії.")
                print(f"[STT] {exc}")
                console_print("Голосове введення недоступне. Подробиці — у журналі сесії.")
                return
            except Exception:
                self._voice_error = True
                if self.web_ui is not None:
                    self.web_ui.publish("notice", "Помилка мікрофона. Повторна спроба підключення…")
                logger.exception("Voice loop failed")
                console_print("Помилка мікрофона. Подробиці — у журналі сесії.")
                await asyncio.sleep(1)

    async def _listen_for_turn(self, timeout, grammar):
        if not self.microphone_enabled:
            return RecognitionResult("", 0.0, "interrupted")
        epoch = self._microphone_epoch
        self._voice_inflight = True
        started = asyncio.get_running_loop().time()
        task = asyncio.create_task(asyncio.to_thread(self.listener.listen_once, timeout, grammar))
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=0.05)
                # Text input or a reminder may start TTS after recording began.
                # Never enqueue the assistant's own audio as a user message.
                if (not self.microphone_enabled or epoch != self._microphone_epoch
                        or self.speaker.busy or (grammar is None and not self.command_idle.is_set())):
                    self.listener.interrupt()
                    await task
                    return RecognitionResult("", 0.0, "interrupted")
            result = task.result()
            if not self.microphone_enabled or epoch != self._microphone_epoch:
                return RecognitionResult("", 0.0, "interrupted")
            self._voice_error = False
            if result.text:
                duration = asyncio.get_running_loop().time() - started
                logger.info("Voice capture + STT %.3f s, engine=%s", duration, result.engine)
                getattr(self, "performance", DISABLED_PERFORMANCE).record(
                    "voice.capture_and_stt", duration * 1000,
                )
            return result
        finally:
            if not task.done():
                self.listener.interrupt()
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._voice_inflight = False

    async def _text_input_loop(self) -> None:
        loop = asyncio.get_running_loop()
        text_queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=1)

        def enqueue_text(text: str | None) -> None:
            if text is None:
                asyncio.create_task(text_queue.put(None))
                return
            if text_queue.full():
                console_print("Попередня текстова команда ще очікує. Нову відхилено.")
                return
            text_queue.put_nowait(text)

        def read_console() -> None:
            while self.running:
                try:
                    text = console_input("> ")
                except EOFError:
                    text = None
                try:
                    loop.call_soon_threadsafe(enqueue_text, text)
                except RuntimeError:
                    return
                if text is None:
                    return

        self._console_thread = threading.Thread(
            target=read_console,
            name="console-input",
            daemon=True,
        )
        self._console_thread.start()

        while self.running:
            text = await text_queue.get()
            if text is None:
                return
            if text.strip():
                await self._submit_text(text.strip())

    async def _submit_text(self, text):
        if self.web_ui is not None:
            self.web_ui.publish("user", text, source="text")
        if CANCEL_WORKPLACE.fullmatch(text):
            task = self.services["workplace"]
            cancelled = task.cancel(task.current["id"]) if task.current else False
            notice = "Зупиняю наступні кроки завдання." if cancelled else "Активного завдання немає."
            console_print(notice)
            if self.web_ui is not None:
                self.web_ui.publish("notice", notice)
            return
        if self.confirmation.submit(text):
            return
        # Only tool-free chat is cancelled. File/app operations keep running;
        # muting their output does not mean cancelling or undoing their effects.
        self.processor.interrupt_conversation()
        await self.speaker.stop()
        if PAUSE_CONVERSATION.fullmatch(text) and self.services['state'].get('mode', 'chat') == 'chat':
            self.processor.conversation_paused = True
            console_print('Розмова на паузі. Для повернення скажіть або введіть «продовжуй».')
            if self.web_ui is not None:
                self.web_ui.publish("notice", "Розмову призупинено. Мікрофон не вимкнено.")
            return
        if STOP_SPEECH.fullmatch(text):
            return
        await self._wait_for_input_slot()
        # A local action may have entered confirmation while we were stopping
        # speech. Never let its reply become a separate local command.
        if self.confirmation.submit(text):
            return
        self.command_idle.clear()
        await self.command_queue.put((text, "text", 1.0))

    def web_state(self):
        """Only explicit UI fields: never export settings, history files or secrets."""
        if not self.running:
            status = "offline"
        elif self.confirmation.awaiting:
            status = "confirmation"
        elif self.speaker.playback_active:
            status = "speaking"
        elif self.speaker.busy:
            status = "preparing_speech"
        elif not self.command_idle.is_set():
            status = "thinking"
        elif self.processor.conversation_paused:
            status = "paused"
        elif not self.microphone_enabled:
            status = "ready"
        elif self._voice_error:
            status = "voice_error"
        elif self.listener is not None and self.listener.capture_active.is_set():
            status = "listening"
        elif self._voice_inflight:
            status = "processing_audio"
        else:
            status = "ready"
        return {"status": status, "microphone": self.microphone_enabled,
                "microphone_available": self.listener is not None and not self._voice_unavailable,
                "microphone_stopping": not self.microphone_enabled and self._voice_inflight,
                "paused": self.processor.conversation_paused,
                "mode": self.services["state"].get("mode", "chat"),
                "provider": self.llm.active_name or "не вибрано",
                "confirmation": self.confirmation.request_id,
                "confirmation_prompt": redact_user_text(self.confirmation.prompt),
                "task": self.services["workplace"].snapshot(),
                "tts_busy": self.speaker.busy,
                "tts_playback": self.speaker.playback_active,
                "tts_glow": self.speaker.glow_state()}

    async def web_control(self, action, data):
        if action == "cancel_task":
            if not self.services["workplace"].cancel(data.get("task_id")):
                return 409, {"error": "Це завдання вже неактивне."}
        elif action == "confirm":
            if (not self.confirmation.awaiting
                    or data.get("request_id") != self.confirmation.request_id
                    or type(data.get("accept")) is not bool):
                return 409, {"error": "Це підтвердження вже неактивне."}
            answer = "так" if data["accept"] else "ні"
            self.confirmation.submit(answer)
            self.web_ui.publish("user", answer, source="text")
        elif action == "microphone":
            if (self.listener is None or self._voice_unavailable
                    or type(data.get("enabled")) is not bool):
                return 400, {"error": "Голосове введення недоступне."}
            self.microphone_enabled = data["enabled"]
            self._microphone_epoch += 1
            self.listener.set_paused(not self.microphone_enabled)
            if self.microphone_enabled:
                self._mic_ready.set()
            else:
                self._mic_ready.clear()
        elif action == "stop":
            self.processor.interrupt_conversation()
            await self.speaker.stop()
            self.web_ui.publish("notice", "Відповідь зупинено. Запущені локальні дії не скасовано.")
        elif action in {"pause", "resume"}:
            if self.confirmation.awaiting or self.services["state"].get("mode") != "chat":
                return 409, {"error": "Спочатку завершіть підтвердження або спеціальний режим."}
            if action == "pause":
                self.processor.interrupt_conversation()
                self.processor.conversation_paused = True
                await self.speaker.stop()
            else:
                # Resume listening only; don't spend an LLM call or replay an action.
                self.processor.conversation_paused = False
        return 200, {"ok": True}

    async def _wait_for_input_slot(self) -> None:
        if self.command_idle.is_set() or self.confirmation.awaiting:
            return
        idle_waiter = asyncio.create_task(self.command_idle.wait())
        confirm_waiter = asyncio.create_task(self.confirmation.wait_until_requested())
        try:
            await asyncio.wait(
                {idle_waiter, confirm_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in (idle_waiter, confirm_waiter):
                task.cancel()
            await asyncio.gather(idle_waiter, confirm_waiter, return_exceptions=True)

    async def _wait_for_speaker(self) -> None:
        was_busy = self.speaker.busy
        if was_busy:
            await self.speaker.wait_until_idle()
            await asyncio.sleep(self.settings.stt_post_tts_pause_seconds)

    async def _reminder_loop(self) -> None:
        store = self.services["reminders"]
        await asyncio.sleep(1)
        missed = await asyncio.to_thread(store.missed)
        if missed:
            await self.speaker.say(f"У вас є {len(missed)} пропущені нагадування.")
            for reminder in missed:
                await self.speaker.say(reminder["text"])

        while self.running:
            due = await asyncio.to_thread(store.due)
            for reminder in due:
                await self.speaker.say(f"Нагадування: {reminder['text']}")
                await asyncio.to_thread(store.mark_triggered, reminder["id"])
            await asyncio.sleep(1)
