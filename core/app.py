from __future__ import annotations

import asyncio
import logging
import math
import re
import threading
import time
from dataclasses import replace

from core.command_router import CommandRouter
from core.confirmation import ConfirmationService
from core.dispatch_guard import DispatchGuard
from core.stt_listener import SpeechListener
from core.metrics import MetricsCollector
from core.performance import CURRENT_TURN, DISABLED_PERFORMANCE, PerformanceRecorder, TurnTiming
from core.models import RecognitionResult, TurnEnvelope
from core.recognition_policy import RecognitionPolicy
from core.console import correlated_console_input, console_print
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
from services.platform import resolve_platform

logger = logging.getLogger(__name__)


class ValleRaApp:
    def __init__(self, settings, secret_store, text_only: bool = False, *, performance=None):
        self.settings = settings
        self.platform = resolve_platform()
        self.text_only = text_only
        self.command_queue: asyncio.Queue[TurnEnvelope] = asyncio.Queue(maxsize=1)
        self.metrics = MetricsCollector(settings.paths.data_dir / "metrics.jsonl")
        self.performance = performance or PerformanceRecorder(self.metrics)
        self.speaker = Speaker(settings)
        self.speaker.performance = self.performance
        self.listener = None if text_only else SpeechListener(settings)
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
            "windows": self.platform.windows(),
            "platform": self.platform,
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
        if self.listener is not None:
            self.listener.recognition_policy.dialogue = self.processor.dialogue_state
        self.confirmation = ConfirmationService(
            self._say_confirmation,
            settings.confirmation_timeout_seconds,
            self.speaker.wait_until_idle,
        )

    async def _say_confirmation(self, text):
        processor = getattr(self, 'processor', None)
        if processor is not None:
            processor.dialogue_state.proposals.clear()
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
            whisper_ok, whisper_detail = self.listener.status()
            mode = self.settings.stt_backend
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
            logger.info('Session summary: %s', perf.session_summary())
            if hasattr(self, "_dispatch_guard"):
                self._dispatch_guard.pending.clear()

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
        if self.settings.stt_whisper_preload:
            with perf.span("startup.stt_ready") as measurement:
                ok, _ = await asyncio.to_thread(self.listener.prepare)
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

    async def _enqueue_command(self, text: str, source: str, confidence: float, *,
                               recognition: RecognitionResult | None = None) -> TurnEnvelope:
        # Created once at ingress, retained by every delivery of this queue item.
        if not hasattr(self, "_dispatch_guard"):
            self._dispatch_guard = DispatchGuard()
        turn_id = self._dispatch_guard.issue()
        turn = TurnEnvelope(
            turn_id=turn_id, session_id=self._dispatch_guard.session_id,
            source=source, text=text, transcript=recognition.text if recognition is not None else text,
            stt_engine=recognition.engine if recognition is not None else ('text' if source == 'text' else 'unknown'),
            confidence=confidence,
            utterance_incomplete=(recognition.utterance_incomplete if recognition.utterance_incomplete is not None
                                  else recognition.incomplete) if recognition is not None else False,
            capture_truncated=recognition.capture_truncated if recognition is not None else False,
            recognition_unreliable=recognition.recognition_unreliable if recognition is not None else False,
            action_eligible=RecognitionPolicy.action_eligible(recognition) if recognition is not None else True,
            clarification_required=recognition.incomplete if recognition is not None else False,
            timing=recognition.timing if recognition is not None else None,
        )
        try:
            await self.command_queue.put(turn)
        except BaseException:
            self._dispatch_guard.discard(turn_id)
            raise
        return turn

    async def _command_loop(self) -> None:
        while self.running:
            turn = await self.command_queue.get()
            guard = getattr(self, "_dispatch_guard", None)
            if not isinstance(turn, TurnEnvelope) or guard is None or not guard.claim(turn.turn_id):
                logger.warning("turn_dispatch_rejected=duplicate_or_unissued")
                self.command_queue.task_done()
                if self.command_queue.empty():
                    self.command_idle.set()
                continue
            logger.debug("turn_dispatch_claimed operational_turn_id=%s", turn.turn_id)
            text, source, confidence = turn.text, turn.source, turn.confidence
            timing = turn.timing
            if timing is None:
                timing = TurnTiming(getattr(self, "performance", DISABLED_PERFORMANCE))
                turn = replace(turn, timing=timing)
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
                    turn,
                    self.confirmation.ask,
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
                timing.finish()
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
                    confirmation_id = self.confirmation.request_id
                    result = await self._listen_for_turn(
                        float(self.settings.confirmation_timeout_seconds),
                        ConfirmationService.GRAMMAR,
                    )
                    decision = ConfirmationService._decision(result.text, result.confidence)
                    logger.info("confirmation_voice recognized=%r engine=%s confidence=%.3f semantic=%s request=%s",
                                redact_user_text(result.text), result.engine, result.confidence,
                                'confirm' if decision is True else 'deny' if decision is False else 'unknown',
                                confirmation_id[:8] if confirmation_id else 'none')
                    if result.text:
                        if self.web_ui is not None:
                            self.web_ui.publish("user", result.text, source="voice")
                        safe_text = redact_user_text(result.text)
                        print(
                            f"[CONFIRM VOICE/{result.engine} "
                            f"{result.confidence:.2f}] {safe_text}"
                        )
                        if self.confirmation.submit(result.text, confirmation_id, confidence=result.confidence):
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
                repaired_text = result.text if result.fragmented else repair_voice_text(
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
                await self._enqueue_command(repaired_text, "voice", result.confidence, recognition=result)
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
        text_queue: asyncio.Queue[tuple[str, str | None] | None] = asyncio.Queue(maxsize=1)

        def enqueue_text(text) -> None:
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
                    text = correlated_console_input("> ", lambda: self.confirmation.request_id)
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
            entry = await text_queue.get()
            if entry is None:
                return
            text, confirmation_id = entry
            if text.strip():
                await self._submit_text(text.strip(), confirmation_id)

    def _delivery_context(self):
        """Hold references, not executable data; never bind delayed input to a new prompt."""
        dialogue = self.processor.dialogue_state
        return (dialogue.pending, dialogue.proposals.pending,
                getattr(self.services.get('tasks'), 'pending', None))

    def _delivery_notice(self, text):
        console_print(text)
        if self.web_ui is not None:
            self.web_ui.publish('notice', text)

    def _cancel_waiting_input(self):
        waiting = getattr(self, '_waiting_input', None)
        if waiting is not None:
            waiting.set()
            return True
        return False

    async def _wait_for_text_delivery(self):
        if self.command_idle.is_set() or self.confirmation.awaiting:
            return True
        if getattr(self, '_waiting_input', None) is not None:
            self._delivery_notice('Один запит уже очікує. Цей не прийнято; повторіть його після завершення або зупинки очікування.')
            return False
        cancelled = asyncio.Event()
        self._waiting_input = cancelled
        self._delivery_notice('Поточна операція ще виконується. Запит очікує; кнопка зупинки зніме його з очікування, але не скасує вже запущену дію.')
        ready = asyncio.create_task(self._wait_for_input_slot())
        stop = asyncio.create_task(cancelled.wait())
        try:
            await asyncio.wait({ready, stop}, return_when=asyncio.FIRST_COMPLETED)
            if cancelled.is_set():
                return False
            await ready
            return True
        finally:
            for task in (ready, stop):
                task.cancel()
            await asyncio.gather(ready, stop, return_exceptions=True)
            if getattr(self, '_waiting_input', None) is cancelled:
                self._waiting_input = None

    async def _submit_text(self, text, confirmation_id=None):
        if text.strip() == '/status':
            from services.health.runtime import runtime_status
            status = runtime_status(self)
            console_print(status)
            if self.web_ui is not None:
                self.web_ui.publish('notice', status)
            return
        if self.web_ui is not None:
            self.web_ui.publish("user", text, source="text")
        if confirmation_id is not None and confirmation_id != self.confirmation.request_id:
            if not self.confirmation.submit(text, confirmation_id) and self.web_ui is not None:
                self.web_ui.publish("notice", "Підтвердження змінилося. Перевірте поточну дію й повторіть відповідь.")
            return
        if confirmation_id is None and not self.confirmation.awaiting and re.fullmatch(
                r'(?:команда[\s:,-]+)?скасуй\s+пошук[.!?]*', text.strip(), re.I):
            files = self.services.get('files')
            progress = files.search_snapshot() if files is not None else None
            cancelled = bool(progress and files.cancel_search(progress['id']))
            self._delivery_notice('Запит на скасування пошуку прийнято. Очікую завершення поточного кроку.'
                                  if cancelled else 'Активного пошуку файлів немає. Нічого не скасовано.')
            return
        if CANCEL_WORKPLACE.fullmatch(text):
            self.processor.interrupt_conversation(discard_proposal=True)
            task = self.services["workplace"]
            cancelled = task.cancel(task.current["id"]) if task.current else False
            notice = "Зупиняю наступні кроки завдання." if cancelled else "Активного завдання немає."
            console_print(notice)
            if self.web_ui is not None:
                self.web_ui.publish("notice", notice)
            return
        if confirmation_id is not None or self.confirmation.awaiting:
            self.confirmation.submit(text, confirmation_id)
            return
        delivery_context = self._delivery_context()
        # Only tool-free chat is cancelled. File/app operations keep running;
        # muting their output does not mean cancelling or undoing their effects.
        if PAUSE_CONVERSATION.fullmatch(text) or STOP_SPEECH.fullmatch(text):
            self.processor.interrupt_conversation(discard_proposal=True)
        else:
            self.processor.interrupt_conversation()
        await self.speaker.stop()
        if PAUSE_CONVERSATION.fullmatch(text) and self.services['state'].get('mode', 'chat') == 'chat':
            self.processor.conversation_paused = True
            console_print('Розмова на паузі. Для повернення скажіть або введіть «продовжуй».')
            if self.web_ui is not None:
                self.web_ui.publish("notice", "Розмову призупинено. Мікрофон не вимкнено.")
            return
        if STOP_SPEECH.fullmatch(text):
            if self._cancel_waiting_input():
                self._delivery_notice('Запит знято з очікування. Уже запущені локальні дії не скасовано.')
            return
        if not await self._wait_for_text_delivery():
            return
        # A local action may have entered confirmation while we were stopping
        # speech. Never let its reply become a separate local command.
        if self.confirmation.awaiting:
            logger.info("confirmation_reply_rejected=request_changed_during_delivery")
            self._delivery_notice('Під час очікування з’явилося підтвердження. Запит не виконано; перевірте поточну дію, потім повторіть запит.')
            return
        if any(before is not after for before, after in zip(delivery_context, self._delivery_context())):
            self._delivery_notice('Контекст завдання змінився під час очікування. Запит не виконано, щоб не застосувати його до іншого уточнення. Повторіть запит.')
            return
        self.command_idle.clear()
        await self._enqueue_command(text, "text", 1.0)

    def _task_progress(self):
        from core.task_progress import TaskProgressRegistry, workplace_progress, file_search_progress
        if not hasattr(self, '_progress_registry'):
            registry = TaskProgressRegistry()
            workplace = self.services.get('workplace')
            files = self.services.get('files')
            if workplace is not None:
                registry.register('workplace', lambda: workplace_progress(workplace), workplace.cancel)
            if files is not None:
                registry.register('file_search', lambda: file_search_progress(files), files.cancel_search)
            self._progress_registry = registry
        return self._progress_registry

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
        task = self._task_progress().current()
        return {"status": status, "microphone": self.microphone_enabled,
                "microphone_available": self.listener is not None and not self._voice_unavailable,
                "microphone_stopping": not self.microphone_enabled and self._voice_inflight,
                "paused": self.processor.conversation_paused,
                "mode": self.services["state"].get("mode", "chat"),
                "provider": self.llm.active_name or "не вибрано",
                "confirmation": self.confirmation.request_id,
                "confirmation_prompt": redact_user_text(self.confirmation.prompt),
                "task": task.public() if task else None,
                "tts_busy": self.speaker.busy,
                "tts_playback": self.speaker.playback_active,
                "tts_glow": self.speaker.glow_state()}

    async def web_control(self, action, data):
        if action == 'cancel_progress':
            if not self._task_progress().cancel(data.get('kind'), data.get('task_id')):
                return 409, {'error': 'Це завдання вже неактивне або змінилося.'}
        elif action == 'cancel_search':
            files = self.services.get('files')
            if files is None or not files.cancel_search(data.get('task_id')):
                return 409, {'error': 'Цей пошук уже неактивний.'}
        elif action == "cancel_task":
            if not self.services["workplace"].cancel(data.get("task_id")):
                return 409, {"error": "Це завдання вже неактивне."}
        elif action == "confirm":
            if (not self.confirmation.awaiting
                    or data.get("request_id") != self.confirmation.request_id
                    or type(data.get("accept")) is not bool):
                return 409, {"error": "Це підтвердження вже неактивне."}
            answer = "так" if data["accept"] else "ні"
            self.confirmation.submit(answer, data["request_id"])
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
            waiting_cancelled = self._cancel_waiting_input()
            self.processor.interrupt_conversation(discard_proposal=True)
            await self.speaker.stop()
            self.web_ui.publish("notice", ("Запит знято з очікування. " if waiting_cancelled else "") +
                                "Відповідь зупинено. Запущені локальні дії не скасовано.")
        elif action in {"pause", "resume"}:
            if self.confirmation.awaiting or self.services["state"].get("mode") != "chat":
                return 409, {"error": "Спочатку завершіть підтвердження або спеціальний режим."}
            if action == "pause":
                self.processor.interrupt_conversation(discard_proposal=True)
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
