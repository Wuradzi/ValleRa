from __future__ import annotations

import logging
import asyncio
import re
import time

from core.models import CommandContext, SkillResult
from core.command_actions import execute_intent
from core.command_intent import CommandIntent, InterpretationUnavailable, MULTI_ACTION, can_interpret, preserves_search_intent
from core.command_catalog import chat_catalog, local_intent_candidates
from core.natural_turn import direct_request, application_name_reply, request_scope, short_clarification, voice_request, window_request, exact_open_request
from core.performance import DISABLED_PERFORMANCE
from core.speech_text import SpeechBuffer
from services.llm.manager import CHAT_MODE_PROMPT
from services.apps.workplace import CONFIGURE_WORKPLACE, WORK_MODE, WORKPLACE, TASK_STATUS, TASK_RETRY

VOICE_INPUT_PROMPT = (
    "Поточна репліка отримана розпізнаванням мовлення й може містити помилки. "
    "Якщо зміст неясний, коротко перепитай, не вигадуй намір користувача. "
    "Не називай помилки автокорекцією: ти не знаєш їх точної причини."
)

logger = logging.getLogger(__name__)

COMMAND_PREFIX = re.compile(
    r"^команда\b(?:\s*[:,-]\s*|\s+)?(.*)$",
    re.IGNORECASE,
)
LOCAL_ACTION_CLAIM = re.compile(
    r"^(?:відкриваю|відкрив|відкрила|запускаю|запустив|запустила|"
    r"закриваю|закрив|закрила|вимикаю|вимкнув|увімкнув)\b"
)
LAST_OPEN_QUESTION = re.compile(
    r"^(?:що|яку програму|який браузер|ти\b).*"
    r"(?:відкрив|відкривав|запустив)"
)
LOCAL_ACTION_TYPES = {
    "agent_workplace",
    "workplace_configured",
    "open_browser",
    "open_application",
    "window_minimize",
    "window_maximize",
    "window_restore",
    "window_choice",
    "window_close",
    "process_terminate",
    "file_open",
    "file_search",
    "file_choice",
    "app_choice",
    "file_delete",
    "media",
    "shutdown",
    "lock",
    "pentest",
}
OPEN_ACTION_TYPES = {"open_browser", "open_application", "file_open", "agent_workplace"}
STOP_SPEECH = re.compile(r"^(?:команда\s*[:,]?\s*)?(?:стоп|замовкни|зупини озвучення)[.!?]*$", re.I)
WEB_CORRECTION = re.compile(r"^(?:це не те|не те|ти не те знайшов|не те знайшов)[.!?]*$", re.I)
PAUSE_CONVERSATION = re.compile(r"^(?:зачекай|пауза|постав розмову на паузу)[.!?]*$", re.I)
DIALOGUE_CONTROLS = {
    "продовжуй": "continue", "продовжимо": "continue", "продовж розмову": "continue",
    "повтори": "repeat", "повтори останнє": "repeat", "повтори відповідь": "repeat",
    "коротше": "shorter", "скажи коротше": "shorter", "відповідай коротше": "shorter",
    "поясни простіше": "simpler", "простішими словами": "simpler",
}
CONTROL_PROMPTS = {
    "continue": "Продовж пояснення на ту саму тему після збереженого тексту, без довгого повторення. Якщо тексту немає — відповідай на збережене питання. Не стверджуй, що знаєш, яку частину користувач почув.",
    "shorter": "Скороти збережену відповідь до 1–3 речень, зберігаючи сенс, важливі застереження й невизначеність. Не додавай нових фактів.",
    "simpler": "Поясни збережену відповідь простими словами, без зайвого жаргону. Збережи тему, важливі застереження й невизначеність. Не вигадуй нових фактів.",
}


class CommandProcessor:
    def __init__(self, settings, router, llm_manager, speaker, metrics, services):
        self.settings = settings
        self.router = router
        self.llm_manager = llm_manager
        self.speaker = speaker
        self.metrics = metrics
        self.services = services
        self._chat_task = None
        self.conversation_paused = False
        self._last_reply = ""
        self._last_question = ""
        self._reply_interrupted = False
        self._reply_truncated = False
        self._natural_pending = None

    def _cache_reply(self, text):
        # Session-only generated text; not a record of physically heard audio.
        self._reply_truncated = len(text) > 10000
        self._last_reply = text[:10000]

    def interrupt_conversation(self):
        """Cancel only tool-free conversation, never a local operation/confirmation."""
        if self._chat_task is not None and not self._chat_task.done():
            self._chat_task.cancel()
            return True
        return False

    async def process(self, command: str, confirm, source: str = "voice") -> SkillResult:
        started = time.perf_counter()
        raw_text = command.strip()
        normalized = self._normalize(raw_text)
        state = self.services["state"]
        mode = state.get("mode", "chat")
        success = True
        provider = "local"
        command_match = COMMAND_PREFIX.match(raw_text)
        local_raw = command_match.group(1).strip() if command_match else None
        local_command = self._normalize(local_raw) if local_raw is not None else None
        control = (DIALOGUE_CONTROLS.get(normalized.rstrip('.!?'))
                   if mode == "chat" and command_match is None else None)
        if command_match or control or PAUSE_CONVERSATION.fullmatch(raw_text) or STOP_SPEECH.fullmatch(raw_text):
            self._natural_pending = None
        context = CommandContext(
            self.settings,
            self.services,
            confirm,
            source,
            raw_text=local_raw if local_raw is not None else raw_text,
            normalized_text=local_command if local_command is not None else normalized,
        )

        try:
            tasks = self.services.get("tasks")
            selection = await tasks.consume(raw_text, context) if tasks is not None and mode == "chat" else None
            if PAUSE_CONVERSATION.fullmatch(raw_text) and mode == "chat":
                self.conversation_paused = True
                result = SkillResult(True, "Розмова на паузі. Скажіть «продовжуй», коли будете готові.",
                                     {"command_type": "conversation_paused"})
            elif selection is not None:
                result = selection
            elif command_match and not local_command:
                result = SkillResult(
                    True,
                    "Після слова «Команда» назвіть локальну дію.",
                    {"command_type": "empty_command"},
                )
            elif mode == "pentest":
                if command_match:
                    result = await self._route_local(
                        local_command,
                        context,
                        raw_command=local_raw,
                    )
                else:
                    result = await self._route_local(
                        normalized,
                        context,
                        {"pentest"},
                        raw_command=raw_text,
                    )
            elif command_match:
                if (WORKPLACE.fullmatch(local_raw) or CONFIGURE_WORKPLACE.fullmatch(local_raw) or WORK_MODE.fullmatch(local_raw)
                        or TASK_STATUS.fullmatch(local_raw) or TASK_RETRY.fullmatch(local_raw)):
                    result = await self._route_local(local_command, context, {"workplace"}, raw_command=local_raw)
                elif MULTI_ACTION.search(local_raw):
                    result = SkillResult(True, "Поки виконую одну дію за команду. Розділіть завдання на окремі команди.",
                                         {"command_type": "compound_command"})
                elif local_command == "скасувати":
                    result = SkillResult(True, "Команду скасовано.")
                elif local_command == "замовкни":
                    await self.speaker.stop()
                    result = SkillResult(True, "")
                else:
                    result = await self._route_local(
                        local_command,
                        context,
                        raw_command=local_raw,
                        interpret=True,
                    )
            else:
                web_answers = self.services.get("web_answers")
                web_context = web_answers.dialogue_context() if web_answers is not None else None
                if STOP_SPEECH.fullmatch(raw_text):
                    await self.speaker.stop()
                    result = SkillResult(True, "", {"command_type": "speech_stopped"})
                elif control == "repeat":
                    self.conversation_paused = False
                    repeated = (("Повторю збережену частину довгої відповіді. " if self._reply_truncated else "") + self._last_reply)
                    result = SkillResult(True, repeated or "Ще немає розмовної відповіді, яку можна повторити.",
                                         {"command_type": "conversation_repeat"})
                elif control == "continue" and self.conversation_paused and not self._reply_interrupted:
                    self.conversation_paused = False
                    result = SkillResult(True, "Знову слухаю. Щоб повторити відповідь, скажіть «повтори останнє».",
                                         {"command_type": "conversation_resume"})
                elif control and (not self._last_question or (control != 'continue' and not self._last_reply)):
                    self.conversation_paused = False
                    result = SkillResult(True, "Слухаю. Про що поговоримо?" if control == "continue"
                                         else "Спочатку поставте питання — ще немає відповіді для переформулювання.",
                                         {"command_type": "conversation_resume"})
                elif self.conversation_paused and control is None:
                    result = SkillResult(True, "", {"command_type": "conversation_paused"})
                elif WEB_CORRECTION.fullmatch(raw_text) and web_context and web_context.get("status") != "expired":
                    result = web_answers.request_correction()
                elif LAST_OPEN_QUESTION.match(normalized):
                    last_open = state.get("last_open_action")
                    result = SkillResult(
                        True,
                        (
                            f"Останній результат локального відкриття: "
                            f"{last_open['response']}"
                            if last_open
                            else "У цій сесії я ще не відкривав програм або файлів."
                        ),
                        {"command_type": "last_open_action"},
                    )
                elif (control is None and getattr(self.settings, "natural_actions_enabled", False)
                      and callable(getattr(self.llm_manager, "converse", None))):
                    result = await self._natural_turn(raw_text, normalized, context, web_context)
                    provider = "local" if result.data.get("natural_action") else self.llm_manager.active_name
                else:
                    self.conversation_paused = False
                    control_context = None
                    if control:
                        control_context = {"question": self._last_question, "answer": self._last_reply,
                                           "interrupted": self._reply_interrupted, "truncated": self._reply_truncated}
                    else:
                        self._last_question = raw_text[:2000]
                        self._cache_reply("")
                    self._reply_interrupted = True
                    provider = self.llm_manager.active_name
                    buffer = SpeechBuffer()
                    spoken_parts: list[str] = []
                    saw_tokens = False

                    async def emit(parts):
                        for part in parts:
                            part = part.strip("*#` ")
                            if LOCAL_ACTION_CLAIM.match(part.lower()):
                                part = "Я не виконую локальні дії в розмові. Скажіть: Команда, а потім назвіть дію."
                            if not spoken_parts:
                                self.services.get("performance", DISABLED_PERFORMANCE).record(
                                    "response.first_phrase_to_tts_queue",
                                    (time.perf_counter() - started) * 1000,
                                )
                                self.metrics.record(
                                    "first_speech_queued",
                                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                                )
                            await self.speaker.say(part)
                            spoken_parts.append(part)
                            self._cache_reply(" ".join(spoken_parts))

                    async def on_chunk(text):
                        nonlocal saw_tokens
                        saw_tokens = True
                        await emit(buffer.feed(text))

                    web_answers = self.services.get("web_answers")
                    web_options = ({"web_context": web_answers.dialogue_context()}
                                   if web_answers is not None else {})
                    if control_context is not None:
                        web_options = {"dialogue_control": control_context}
                    system_context = CHAT_MODE_PROMPT + ("\n" + VOICE_INPUT_PROMPT if source == "voice" else "")
                    system_context += "\n" + chat_catalog(self.services.get("enabled_skills", set()))
                    if control in CONTROL_PROMPTS:
                        system_context += "\n" + CONTROL_PROMPTS[control]
                    chat_task = asyncio.create_task(self.llm_manager.chat(
                        raw_text,
                        [] if control else self.services["memory"].relevant(normalized),
                        system_context,
                        on_chunk=on_chunk,
                        **web_options,
                    ))
                    self._chat_task = chat_task
                    try:
                        answer = await chat_task
                    except asyncio.CancelledError:
                        if asyncio.current_task().cancelling():
                            raise
                        return SkillResult(True, "", {"command_type": "chat_interrupted"})
                    finally:
                        self._chat_task = None
                    if saw_tokens:
                        await emit(buffer.feed("", final=True))
                        answer = " ".join(spoken_parts)
                        self.llm_manager.rewrite_last_answer(answer)
                    elif LOCAL_ACTION_CLAIM.match(answer.lower().strip()):
                        answer = (
                            "Я не виконую локальні дії в режимі розмови. "
                            "Скажіть: «Команда», а потім назвіть дію."
                        )
                        rewrite = getattr(
                            self.llm_manager,
                            "rewrite_last_answer",
                            None,
                        )
                        if rewrite:
                            rewrite(answer)
                    result = SkillResult(
                        True,
                        answer,
                        {"command_type": "chat", "response_spoken": bool(spoken_parts)},
                    )
                    self._cache_reply(answer)
                    self._reply_interrupted = False

        except Exception as exc:
            logger.exception("Command failed")
            success = False
            result = SkillResult(True, f"Не вдалося виконати команду: {exc}")

        command_type = result.data.get("command_type", "unknown")
        if provider == "local" or result.data.get("natural_action"):
            logger.info("Local result type=%s status=%s accepted=%s verified=%s", command_type,
                        result.data.get("status", "unspecified"), result.data.get("accepted"), result.data.get("verified"))
        if command_type == "conversation_new":
            self._natural_pending = None
            if self.services.get("tasks") is not None:
                self.services["tasks"].clear()
            workplace = self.services.get("workplace")
            if workplace is not None:
                workplace.clear_context()
            self._last_question = ""
            self._cache_reply("")
            self._reply_interrupted = False
            self.conversation_paused = False
        elif command_type == "chat_mode_enable":
            self.conversation_paused = False
        if (
            provider == "local"
            and (command_type in LOCAL_ACTION_TYPES or result.data.get("natural_action"))
            and result.response
        ):
            action = {
                "command": local_raw or raw_text,
                "response": result.response,
                "command_type": command_type,
            }
            state["last_local_action"] = action
            if command_type in OPEN_ACTION_TYPES and result.data.get("accepted", True):
                state["last_open_action"] = action
            record = getattr(self.llm_manager, "record_local_action", None)
            if record:
                record(action["command"], action["response"])

        self.metrics.record(
            "command_completed",
            command_type=command_type,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            provider=provider,
            success=success and result.data.get("success", result.data.get("accepted", True)),
            source=source,
        )
        return result

    async def _natural_turn(self, text, normalized, context, web_context):
        pending = self._natural_pending
        self._natural_pending = None  # Consume once; later chat must not replay an old request.
        request = voice_request(text) if context.source == "voice" else text
        scoped = bool(pending and time.monotonic() < pending["expires"]
                      and pending.get("tool") and short_clarification(text)
                      and not direct_request(request, ""))
        if pending and not scoped and pending.get("tool"):
            pending = None
        window = window_request(request) if direct_request(request, "window_control") else None
        if scoped and pending["tool"] == "window_control":
            name = application_name_reply(text)
            if name is None:
                self._natural_pending = pending
                return SkillResult(True, "Назвіть програму або заголовок вікна. Самого «так» недостатньо.",
                                   {"command_type": "intent_clarification", "success": False})
            window = (pending["window_action"], name)
        if window is not None:
            if "windows" not in self.services.get("enabled_skills", set()):
                return SkillResult(True, "Керування вікнами недоступне в цій сесії.",
                                   {"command_type": "window_unavailable", "success": False})
            action, name = window
            if not name:
                self._natural_pending = {"expires": time.monotonic() + 120, "tool": "window_control",
                                         "window_action": action, "request": request}
                return SkillResult(True, "Назвіть програму або заголовок вікна.",
                                   {"command_type": "intent_clarification", "success": False})
            result = await execute_intent(CommandIntent("window_control", {"name": name, "action": action}), context)
            result.data["natural_action"] = True
            return result
        model_text = request
        if scoped:
            model_text = (pending["request"] + "\nУточнення користувача: " + text
                          + "\nПопереднє уточнювальне питання: " + pending.get("question", "")
                          + "\nЦе продовження одного запиту. Запропонуй тільки " + pending["tool"]
                          + "; не вигадуй відсутніх параметрів. Дозвіл на виконання запитає локальна система.")
        # Exact saved-workplace requests need no model permission dialogue.
        local_request = re.sub(r"^(?:валера|валеро)[, ]+", "", request.strip(), flags=re.I)
        if (WORK_MODE.fullmatch(local_request) or local_request.lower().rstrip('.!?') in
                {"підготуй робоче місце", "підготуй мене до роботи", "включи режим робота"}):
            result = await execute_intent(CommandIntent("prepare_workplace", {}), context)
            result.data["natural_action"] = True
            return result
        # A complete, literal installed-app request does not need model inference.
        # No speculative launch: the SAME validator, approval and result checks run.
        name = exact_open_request(request) if not scoped else None
        exact = getattr(self.services.get("apps"), "has_exact_name", None)
        if name and callable(exact) and "apps" in self.services.get("enabled_skills", set()):
            known = False
            with self.services.get("performance", DISABLED_PERFORMANCE).span("command.local_app_lookup"):
                try:
                    known = await asyncio.wait_for(asyncio.to_thread(exact, name), timeout=0.25)
                except (TimeoutError, OSError, ValueError, KeyError, TypeError, AttributeError):
                    logger.info("Local app lookup unavailable; using normal intent path")
            if known is True:
                logger.info("Natural decision kind=action tool=open_app route=local_exact")
                result = await asyncio.wait_for(
                    execute_intent(CommandIntent("open_app", {"name": name}), context), timeout=120)
                result.data["natural_action"] = True
                result.data["intent_route"] = "local_exact"
                return result
        buffer = SpeechBuffer()
        spoken = []
        self._last_question = text[:2000]
        self._cache_reply("")
        self._reply_interrupted = True

        async def emit(parts):
            for part in parts:
                part = part.strip("*#` ")
                if LOCAL_ACTION_CLAIM.match(part.lower()):
                    part = "Дію ще не виконано. Для запуску потрібен погоджений план."
                await self.speaker.say(part)
                spoken.append(part)
                self._cache_reply(" ".join(spoken))

        async def on_chunk(chunk):
            # converse delivers ONE fully validated envelope, not raw tokens.
            # Flush its last sentence now, before stream cleanup/history writes.
            await emit(buffer.feed(chunk, final=True))

        task = asyncio.create_task(self.llm_manager.converse(
            model_text, self.services["memory"].relevant(normalized),
            enabled_skills=self.services.get("enabled_skills", set()), web_context=web_context, on_chunk=on_chunk))
        self._chat_task = task
        try:
            turn = await task
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            return SkillResult(True, "", {"command_type": "chat_interrupted"})
        finally:
            self._chat_task = None  # Local operations must not be cancelled by conversation interruption.
        logger.info("Natural decision kind=%s tool=%s scoped=%s", turn.kind,
                    turn.intent.tool if turn.intent is not None else None, scoped)
        if turn.kind == "action":
            self._reply_interrupted = False
            intent = turn.intent
            expected_scope = pending["tool"] if scoped else request_scope(request)
            if intent is not None and expected_scope and intent.tool != expected_scope:
                return SkillResult(True, "Запропонована дія не відповідає типу вашого запиту. Уточніть прохання; нічого не виконано.",
                                   {"command_type": "intent_clarification", "success": False})
            if (intent is not None and intent.tool == "open_app" and pending is not None
                    and time.monotonic() < pending["expires"] and not direct_request(text, intent.tool)):
                name = application_name_reply(text)
                if name is not None:
                    # Only the current user's literal name, never a name invented from old context.
                    intent = CommandIntent("open_app", {"name": name})
                elif scoped:
                    return SkillResult(True, "Назвіть, будь ласка, програму, яку відкрити.",
                                       {"command_type": "intent_clarification", "success": False})
            else:
                pending = None
            if intent is None or (not direct_request(request, intent.tool)
                                  and not scoped
                                  and not (pending and application_name_reply(text) is not None)):
                self._reply_interrupted = False
                return SkillResult(True, "Не впевнений, що це пряме прохання виконати дію. Уточніть, будь ласка; нічого не виконано.",
                                   {"command_type": "intent_clarification", "success": False})
            if not preserves_search_intent(request, intent):
                return SkillResult(True, "Уточніть точний запит пошуку: не можу надійно зберегти його зміст.",
                                   {"command_type": "intent_clarification", "success": False})
            result = await asyncio.wait_for(execute_intent(intent, context), timeout=120)
            result.data["natural_action"] = True
            self._reply_interrupted = False
            return result
        await emit(buffer.feed("", final=True))
        answer = " ".join(spoken) if spoken else turn.response
        if LOCAL_ACTION_CLAIM.match(answer.lower().strip()):
            answer = "Дію ще не виконано. Для запуску потрібен погоджений план."
        if spoken and answer != turn.response:
            self.llm_manager.rewrite_last_answer(answer)
        self._cache_reply(answer)
        self._reply_interrupted = False
        scope = pending["tool"] if scoped else request_scope(request)
        if scope and (turn.kind == "clarify" or (turn.kind == "chat" and "?" in answer)):
            self._natural_pending = {"expires": time.monotonic() + 120, "tool": scope,
                                     "request": model_text[:1600] if scoped else request,
                                     "question": answer[:500]}
        return SkillResult(True, answer, {"command_type": "chat", "response_spoken": bool(spoken)})

    async def _route_local(
        self,
        command: str,
        context: CommandContext,
        allowed_skills: set[str] | None = None,
        raw_command: str | None = None,
        *,
        interpret: bool = False,
    ) -> SkillResult:
        command = re.sub(r"[?.!,;:]+$", "", command).strip()
        context.raw_text = (raw_command if raw_command is not None else command).strip()
        context.normalized_text = command
        result = await self.router.route(command, context, allowed_skills)
        if result.handled:
            return result
        if interpret and can_interpret(context.raw_text):
            candidates = local_intent_candidates(context.raw_text, context.services.get("enabled_skills", set()))
            if len(candidates) == 1:
                return await execute_intent(CommandIntent(candidates[0], {}), context)
            if len(candidates) > 1:
                return SkillResult(True, "Є кілька можливих дій. Уточніть, що саме потрібно зробити; нічого не виконано.",
                                   {"command_type": "intent_clarification", "success": False})
        if (interpret and getattr(self.settings, "command_interpretation_enabled", False)
                and can_interpret(context.raw_text)):
            try:
                intent = await self.llm_manager.interpret_command(context.raw_text)
            except InterpretationUnavailable as exc:
                return SkillResult(True, str(exc), {"command_type": "interpretation_unavailable", "success": False})
            if intent is not None:
                if not preserves_search_intent(context.raw_text, intent):
                    return SkillResult(True, "Не можу надійно зберегти зміст пошукового запиту. "
                                       "Скажіть: Команда, знайди в інтернеті, і точний запит.",
                                       {"command_type": "interpretation_invalid", "success": False})
                try:
                    return await asyncio.wait_for(execute_intent(intent, context), timeout=120)
                except asyncio.TimeoutError:
                    return SkillResult(True, "Час виконання дії минув. Результат невідомий; автоматично не повторюю.",
                                       {"command_type": "skill_timeout", "success": False})
            return SkillResult(True, "Не вдалося надійно розібрати команду. Дію не виконано; уточніть запит.",
                               {"command_type": "interpretation_invalid", "success": False})
        return SkillResult(
            True,
            "Команду не розпізнано. Використайте одну з налаштованих команд.",
            {"command_type": "unknown_command"},
        )

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(text.lower().strip().split())
