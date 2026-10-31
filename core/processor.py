from __future__ import annotations

import logging
import asyncio
import re
import time

from core.models import CommandContext, SkillResult, TurnEnvelope
from core.recognition_policy import DialogueState
from core.dialogue_decision import DialogueDecision, DecisionKind
from core.action_policy import (KNOWLEDGE_GAP_NOTICE, PolicyOutcome, knowledge_gap_fallback,
                                knowledge_gap_permission, natural_action_permission)
from core.action_proposal import ReplyKind, proposal_question, needs_proposal
from core.command_actions import execute_intent
from core.command_intent import CommandIntent, InterpretationUnavailable, MULTI_ACTION, can_interpret, original_search_intent, preserves_search_intent
from core.command_catalog import chat_catalog, local_intent_candidates
from core.natural_turn import (direct_request, application_name_reply, final_request, request_scope,
                               short_clarification, voice_request, window_request)
from core.local_action_resolver import resolve_exact_app
from core.performance import DISABLED_PERFORMANCE
from core.speech_text import SpeechBuffer
from core.turn_gate import TurnGate
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
KNOWLEDGE_SOURCE_QUESTION = re.compile(
    r"(?:а\s+)?(?:звідки\s+(?:ти\s+)?(?:це\s+)?знаєш|звідки\s+це|яке\s+(?:в\s+тебе\s+)?джерело|"
    r"з\s+якого\s+джерела(?:\s+це)?)[?.!]*", re.I)


def _knowledge_source_label(entry):
    added = entry.get('added_at', '')[:10]
    date = '.'.join(reversed(added.split('-'))) if added else 'дата невідома'
    source = entry.get('source', {})
    if source.get('kind') == 'manual':
        return f'додано вами {date}'
    return f"файл «{source.get('name', 'невідомий')}», {date}"


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
        self._last_knowledge_ids = []
        from core.document_reading import DocumentReading
        self.documents = DocumentReading()

    def _document_options(self, text):
        # Documents inform conversation, never supply arguments for a requested action.
        if direct_request(text, ''):
            return {}
        document = self.documents.document.context(text + '\n' + self._last_question)
        return {'document_context': document} if document else {}

    def _cache_reply(self, text):
        # Session-only generated text; not a record of physically heard audio.
        self._reply_truncated = len(text) > 10000
        self._last_reply = text[:10000]

    def interrupt_conversation(self, *, discard_proposal=False):
        """Cancel only tool-free conversation, never a local operation/confirmation."""
        if discard_proposal:
            self.dialogue_state.proposals.clear()
        if self._chat_task is not None and not self._chat_task.done():
            self.dialogue_state.proposals.clear()
            self._chat_task.cancel()
            return True
        return False

    async def process(self, command: str | TurnEnvelope, confirm, source: str = "voice", *, allow_actions=True,
                      incomplete_input=False) -> SkillResult:
        # Direct string callers (probes/tests) keep their existing API; app always
        # supplies an envelope. Never mint an operational ID in the processor.
        input_turn = command if isinstance(command, TurnEnvelope) else None
        if input_turn is not None:
            command, source = input_turn.text, input_turn.source
        gate = TurnGate.evaluate(input_turn, allow_actions, incomplete_input)
        started = time.perf_counter()
        raw_text = command.strip()
        normalized = self._normalize(raw_text)
        state = self.services["state"]
        mode = state.get("mode", "chat")
        document_text = COMMAND_PREFIX.sub(r'\1', raw_text) if COMMAND_PREFIX.match(raw_text) else raw_text
        if (mode == 'chat' and 'files' in self.services.get('enabled_skills', set())
                and self.documents.accepts(document_text)):
            if not gate.explicit_local_request:
                self.documents.pending = None
                return SkillResult(True, 'Повторіть прохання прочитати файл повністю або введіть його текстом.')
            context = CommandContext(self.settings, self.services, confirm, source, raw_text=document_text,
                                     turn_id=input_turn.turn_id if input_turn else None)
            result = await self.documents.handle(document_text, context)
            if result is not None:
                self._natural_pending = None
                self.dialogue_state.proposals.clear()
                if self.services.get('tasks') is not None:
                    self.services['tasks'].clear()
                return result
        result = await self._memory_turn(raw_text, gate, mode, source, confirm, input_turn)
        if result is not None:
            return result
        proposals = self.dialogue_state.proposals
        session_id = input_turn.session_id if input_turn is not None else proposals.session_id
        proposal_lease = proposals.begin_turn(session_id)
        proposal_generation = proposals.generation
        proposal_quality = gate.proposal_quality
        if (not proposal_quality or self._natural_pending is not None
                or getattr(self.services.get('tasks'), 'pending', None) is not None):
            proposal_lease = None
        permission = gate.outcome(stop_phrase=bool(STOP_SPEECH.fullmatch(raw_text)))
        if permission is not PolicyOutcome.ALLOW:
            return await self._restricted_turn(raw_text, normalized, input_turn, gate, mode, source, confirm)
        success = True
        provider = "local"
        command_match = COMMAND_PREFIX.match(raw_text)
        local_raw = command_match.group(1).strip() if command_match else None
        local_command = self._normalize(local_raw) if local_raw is not None else None
        control = (DIALOGUE_CONTROLS.get(normalized.rstrip('.!?'))
                   if mode == "chat" and command_match is None else None)
        decision = DialogueDecision(
            DecisionKind.LOCAL_COMMAND if command_match else
            DecisionKind.CONTROL if control else DecisionKind.CHAT,
            turn_id=input_turn.turn_id if input_turn is not None else None)
        if command_match or control or PAUSE_CONVERSATION.fullmatch(raw_text) or STOP_SPEECH.fullmatch(raw_text):
            self._natural_pending = None
        context = CommandContext(
            self.settings,
            self.services,
            confirm,
            source,
            raw_text=local_raw if local_raw is not None else raw_text,
            normalized_text=local_command if local_command is not None else normalized,
            turn_id=input_turn.turn_id if input_turn is not None else None,
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
                result = await self._pentest_turn(raw_text, normalized, command_match,
                                                  local_raw, local_command, context)
            elif decision.kind is DecisionKind.LOCAL_COMMAND:
                result = await self._local_command(local_raw, local_command, context)
            else:
                result, provider, finished = await self._conversation_turn(
                    raw_text, normalized, control, state, context, source, started,
                    proposal_lease, proposal_generation, session_id, proposal_quality)
                if finished:
                    return result
        except Exception as exc:
            logger.exception("Command failed")
            success = False
            result = SkillResult(True, f"Не вдалося виконати команду: {exc}")

        return self._finish_turn(result, provider, success, raw_text, local_raw, source, started, state)

    async def _memory_turn(self, raw_text, gate, mode, source, confirm, input_turn):
        """Explicit personal-memory or knowledge-base phrases; None for every other turn."""
        if mode != 'chat' or 'memory' not in self.services.get('enabled_skills', set()):
            return None
        from skills.memory.knowledge import parse_knowledge_request
        from skills.memory.personal import parse_request
        memory_text = COMMAND_PREFIX.sub(r'\1', raw_text) if COMMAND_PREFIX.match(raw_text) else raw_text
        source_reply = self._knowledge_source_reply(memory_text)
        if source_reply is not None:
            return source_reply  # read-only answer from stored entries, no model
        # Knowledge phrases first: «Запам'ятай як довідку» must not become a personal fact.
        knowledge_request = (parse_knowledge_request(memory_text)
                             if self.services.get('knowledge') is not None else None)
        memory_request = None if knowledge_request else parse_request(memory_text)
        if knowledge_request is None and memory_request is None:
            return None
        if not gate.explicit_local_request:
            return SkillResult(True, 'Повторіть запит пам’яті повністю або введіть його текстом.')
        context = CommandContext(self.settings, self.services, confirm, source, raw_text=memory_text,
                                 turn_id=input_turn.turn_id if input_turn else None)
        if knowledge_request is not None:
            return await self._knowledge_turn(knowledge_request, context)
        return await self._personal_memory_turn(memory_request, context)

    async def _knowledge_turn(self, request, context):
        from skills.memory.knowledge import handle_knowledge_request
        try:
            return await handle_knowledge_request(request, context)
        except OSError:
            logger.exception('Knowledge storage operation failed')
            return SkillResult(True, 'Не вдалося завершити зміну бази знань. Перевірте сховище.')

    async def _personal_memory_turn(self, memory_request, context):
        from skills.memory.personal import handle_request
        try:
            result = await handle_request(memory_request, context, self.llm_manager)
        except OSError:
            logger.exception('Memory storage operation failed')
            return SkillResult(True, 'Не вдалося завершити зміну пам’яті. Перевірте сховище.')
        if result.data.get('memory_context_reset'):
            self.documents.clear()
            self._natural_pending = None
            self.dialogue_state.proposals.clear()
            self._last_question = ''
            self._cache_reply('')
            if self.services.get('web_answers') is not None:
                self.services['web_answers'].clear()
        return result

    def _knowledge_source_reply(self, text):
        """«Звідки ти це знаєш?» about entries used in the last reply; None otherwise."""
        if not KNOWLEDGE_SOURCE_QUESTION.fullmatch(text.strip()):
            return None
        store = self.services.get('knowledge')
        ids = getattr(self, '_last_knowledge_ids', [])
        entries = store.get(ids) if store is not None and ids else []
        if not entries:
            return None  # the model answers about web or its own knowledge
        sources = '; '.join(f"«{entry['title']}» — {_knowledge_source_label(entry)}" for entry in entries)
        return SkillResult(True, 'З локальної бази знань: ' + sources + '.', {'command_type': 'knowledge_source'})

    def _knowledge_options(self, text):
        """Matching reference entries for conversation; never for an action request."""
        store = self.services.get('knowledge')
        self._last_knowledge_ids = []
        if store is None or 'memory' not in self.services.get('enabled_skills', set()) or direct_request(text, ''):
            return {}
        try:
            entries = store.search(text)
        except OSError:
            logger.exception('Knowledge search failed')
            return {}
        # Imported files reach a cloud model only with separate consent (phase 2).
        entries = [entry for entry in entries if entry.get('source', {}).get('kind') == 'manual']
        self._last_knowledge_ids = [entry['id'] for entry in entries]
        if not entries:
            return {}
        logger.info('Knowledge context entries=%d ids=%s', len(entries), ','.join(self._last_knowledge_ids))
        # Source and date stay local: the model must not volunteer them.
        return {'knowledge_context': [{'id': entry['id'], 'title': entry['title'], 'text': entry['text']}
                                      for entry in entries]}

    async def _restricted_turn(self, raw_text, normalized, input_turn, gate, mode, source, confirm):
        """Turn that failed the pre-routing gate: tool-free reply or scoped Groq candidate."""
        # STT continuation is context, never authority for a tool or a pending
        # selection. Require a fresh complete request before normal approval.
        prior_clarification = self._natural_pending
        self._natural_pending = None
        previous_reply = self._last_reply or None
        if self.conversation_paused:
            return SkillResult(True, "", {"command_type": "conversation_paused"})
        lexical_fragment = (input_turn is not None and not input_turn.confidence_available
            and input_turn.utterance_incomplete and not input_turn.capture_truncated
            and not input_turn.recognition_unreliable and not COMMAND_PREFIX.match(raw_text))
        if gate.incomplete and not lexical_fragment:
            return SkillResult(True, "Схоже, фраза обірвалася. Повторіть її повністю, будь ласка.",
                               {"command_type": "fragment_clarify"})
        notice = "Почув фразу частинами. Повторіть повне прохання про дію; нічого не виконано."
        if input_turn is not None and not input_turn.confidence_available and not lexical_fragment:
            notice = "Для цієї голосової репліки немає оцінки впевненості. Дію не виконано; введіть прохання текстом."
        groq_candidate = gate.groq_candidate(getattr(self.settings, 'natural_actions_enabled', False))
        if mode != "chat" or (COMMAND_PREFIX.match(raw_text) and not groq_candidate):
            return SkillResult(True, notice, {"command_type": "fragment_clarify"})
        candidate_text = raw_text
        if groq_candidate and COMMAND_PREFIX.match(raw_text):
            candidate_text = COMMAND_PREFIX.match(raw_text).group(1).strip()
        scoped_name = (application_name_reply(candidate_text) if groq_candidate
            and prior_clarification and prior_clarification.get('groq_scoped')
            and time.monotonic() < prior_clarification['expires']
            and not direct_request(candidate_text, '') else None)
        # Only fill the missing app name of a recent explicit open request.
        # A conversational 'yes' is not permission and cannot fill this slot.
        model_text = (prior_clarification['request'] + '\nНазва програми: ' + scoped_name
                      if scoped_name else candidate_text)
        web = self.services.get("web_answers")
        streamed_parts = []

        async def speak_preview(phrase):
            phrase = phrase.strip()
            await self.speaker.say(phrase)
            streamed_parts.append(phrase)
            self._cache_reply(' '.join(streamed_parts))

        async def propose():
            intent = await self._resolve_exact_app(candidate_text) if groq_candidate else None
            if intent is not None:
                from core.natural_turn import NaturalTurn
                logger.info('Groq proposal route=local_exact; confirmation_required=true')
                return NaturalTurn('action', intent=intent)
            return await self.llm_manager.converse(
                model_text, self.services["memory"].relevant(normalized),
                enabled_skills=self.services.get("enabled_skills", set()),
                web_context=web.dialogue_context() if web is not None else None,
                **self._document_options(model_text),
                **self._knowledge_options(model_text),
                **({'on_chunk': speak_preview} if groq_candidate else {}))

        task = asyncio.create_task(propose())
        self._chat_task = task
        try:
            turn = await task
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            return SkillResult(True, "", {"command_type": "chat_interrupted"})
        finally:
            self._chat_task = None
        if groq_candidate and turn.kind in {'action', 'lookup'}:
            context = CommandContext(self.settings, self.services, confirm, source,
                raw_text=candidate_text, normalized_text=self._normalize(candidate_text), turn_id=input_turn.turn_id)
            return await self._groq_candidate_action(turn, candidate_text, prior_clarification, scoped_name,
                                                     previous_reply, context)
        missing_app_name = bool(re.search(r'\b(?:відкрити|відкрий|запустити|запусти)\s+(?:мені\s+)?програму[.!?\s]*$', candidate_text, re.I))
        if (groq_candidate and request_scope(candidate_text) == 'open_app'
                and (turn.kind == 'clarify' or (turn.kind == 'chat' and missing_app_name))):
            self._natural_pending = {'tool': 'open_app', 'request': candidate_text,
                                    'expires': time.monotonic() + 120, 'groq_scoped': True}
        if turn.kind == 'unavailable':
            if streamed_parts:
                await speak_preview(turn.response)
                return SkillResult(True, ' '.join(streamed_parts),
                                   {'command_type': 'llm_unavailable', 'response_spoken': True})
            return SkillResult(True, turn.response, {'command_type': 'llm_unavailable'})
        if turn.kind in {'proposal', 'followup'}:
            return SkillResult(True, 'Уточніть повне прохання, якщо хочете виконати дію. Нічого не виконано.',
                               {'command_type': 'intent_clarification'})
        return SkillResult(True, turn.response if turn.kind in {"chat", "clarify"} else notice,
                           {"command_type": "fragment_chat" if turn.kind == "chat" else "fragment_clarify",
                            "response_spoken": bool(streamed_parts)})

    async def _pentest_turn(self, raw_text, normalized, command_match, local_raw, local_command, context):
        """Pentest mode: speech stop, «Команда, …», otherwise only the pentest skill."""
        if command_match is None and STOP_SPEECH.fullmatch(raw_text):
            # Speech control, not a scan command: same effect as in chat mode (J125).
            await self.speaker.stop()
            return SkillResult(True, "", {"command_type": "speech_stopped"})
        if command_match:
            return await self._route_local(local_command, context, raw_command=local_raw)
        return await self._route_local(normalized, context, {"pentest"}, raw_command=raw_text)

    async def _local_command(self, local_raw, local_command, context):
        """«Команда, …» in chat mode: one local action, never compound."""
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
        return result

    async def _conversation_turn(self, raw_text, normalized, control, state, context, source, started,
                                 proposal_lease, proposal_generation, session_id, proposal_quality):
        """Chat-mode turn without «Команда»: controls, natural turn or LLM chat.

        Returns (result, provider, finished). finished=True keeps the former early
        return that skips post-processing and metrics for an interrupted chat.
        """
        provider = "local"
        web_answers = self.services.get("web_answers")
        web_context = web_answers.dialogue_context() if web_answers is not None else None
        if STOP_SPEECH.fullmatch(raw_text):
            await self.speaker.stop()
            return SkillResult(True, "", {"command_type": "speech_stopped"}), provider, False
        result = self._conversation_control_reply(control)
        if result is None:
            result = self._conversation_context_reply(raw_text, normalized, state, web_answers, web_context)
        if result is not None:
            return result, provider, False
        if (control is None and getattr(self.settings, "natural_actions_enabled", False)
                and callable(getattr(self.llm_manager, "converse", None))):
            result = await self._natural_turn(raw_text, normalized, context, web_context,
                proposal_lease=proposal_lease, proposal_generation=proposal_generation,
                session_id=session_id, proposal_quality=proposal_quality)
            provider = "local" if result.data.get("natural_action") else self.llm_manager.active_name
            return result, provider, False
        return await self._chat_turn(raw_text, normalized, control, source, started)

    def _conversation_control_reply(self, control):
        """Local replies to dialogue controls (repeat/continue/rephrase) and pause."""
        if control == "repeat":
            self.conversation_paused = False
            repeated = (("Повторю збережену частину довгої відповіді. " if self._reply_truncated else "") + self._last_reply)
            return SkillResult(True, repeated or "Ще немає розмовної відповіді, яку можна повторити.",
                               {"command_type": "conversation_repeat"})
        if control == "continue" and self.conversation_paused and not self._reply_interrupted:
            self.conversation_paused = False
            return SkillResult(True, "Знову слухаю. Щоб повторити відповідь, скажіть «повтори останнє».",
                               {"command_type": "conversation_resume"})
        if control and (not self._last_question or (control != 'continue' and not self._last_reply)):
            self.conversation_paused = False
            return SkillResult(True, "Слухаю. Про що поговоримо?" if control == "continue"
                               else "Спочатку поставте питання — ще немає відповіді для переформулювання.",
                               {"command_type": "conversation_resume"})
        if self.conversation_paused and control is None:
            return SkillResult(True, "", {"command_type": "conversation_paused"})
        return None

    @staticmethod
    def _conversation_context_reply(raw_text, normalized, state, web_answers, web_context):
        """Local replies about the previous web answer or the last local open."""
        if WEB_CORRECTION.fullmatch(raw_text) and web_context and web_context.get("status") != "expired":
            return web_answers.request_correction()
        if LAST_OPEN_QUESTION.match(normalized):
            last_open = state.get("last_open_action")
            return SkillResult(
                True,
                (
                    f"Останній результат локального відкриття: "
                    f"{last_open['response']}"
                    if last_open
                    else "У цій сесії я ще не відкривав програм або файлів."
                ),
                {"command_type": "last_open_action"},
            )
        return None

    async def _chat_turn(self, raw_text, normalized, control, source, started):
        """Tool-free LLM chat with streamed speech; returns (result, provider, finished)."""
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

        system_context, web_options = self._chat_options(control, control_context, source)
        chat_task = asyncio.create_task(self.llm_manager.chat(
            raw_text,
            [] if control else self.services["memory"].relevant(normalized),
            system_context,
            on_chunk=on_chunk,
            **web_options,
            **self._document_options(raw_text),
            **self._knowledge_options(raw_text),
        ))
        self._chat_task = chat_task
        try:
            answer = await chat_task
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            return SkillResult(True, "", {"command_type": "chat_interrupted"}), provider, True
        finally:
            self._chat_task = None
        if saw_tokens:
            await emit(buffer.feed("", final=True))
            answer = " ".join(spoken_parts)
            self.llm_manager.rewrite_last_answer(answer)
        else:
            answer = self._screen_unstreamed_chat_answer(answer)
        result = SkillResult(
            True,
            answer,
            {"command_type": "chat", "response_spoken": bool(spoken_parts)},
        )
        self._cache_reply(answer)
        self._reply_interrupted = False
        return result, provider, False

    def _screen_unstreamed_chat_answer(self, answer):
        """A non-streamed chat answer must not claim a local action was performed."""
        if LOCAL_ACTION_CLAIM.match(answer.lower().strip()):
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
        return answer

    def _chat_options(self, control, control_context, source):
        """System prompt and web/dialogue-control options for one chat request."""
        web_answers = self.services.get("web_answers")
        web_options = ({"web_context": web_answers.dialogue_context()}
                       if web_answers is not None else {})
        if control_context is not None:
            web_options = {"dialogue_control": control_context}
        system_context = CHAT_MODE_PROMPT + ("\n" + VOICE_INPUT_PROMPT if source == "voice" else "")
        system_context += "\n" + chat_catalog(self.services.get("enabled_skills", set()))
        if control in CONTROL_PROMPTS:
            system_context += "\n" + CONTROL_PROMPTS[control]
        return system_context, web_options

    def _finish_turn(self, result, provider, success, raw_text, local_raw, source, started, state):
        """Shared post-processing: context resets, last local action, metrics."""
        command_type = result.data.get("command_type", "unknown")
        if provider == "local" or result.data.get("natural_action"):
            logger.info("Local result type=%s status=%s accepted=%s verified=%s", command_type,
                        result.data.get("status", "unspecified"), result.data.get("accepted"), result.data.get("verified"))
        if command_type == "conversation_new":
            self.documents.clear()
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

    @property
    def dialogue_state(self):
        # Lazy creation retains direct object.__new__ diagnostic fixtures.
        if not hasattr(self, '_dialogue_state'):
            self._dialogue_state = DialogueState(self.services)
        return self._dialogue_state

    @property
    def _natural_pending(self):
        return self.dialogue_state.pending

    @_natural_pending.setter
    def _natural_pending(self, pending):
        self.dialogue_state.pending = pending

    def stt_scoped_reply(self, text):
        """Compatibility for probes; audio never calls the processor."""
        return self.dialogue_state.scoped_reply(text)

    async def _natural_turn(self, text, normalized, context, web_context, *, proposal_lease=None,
                            proposal_generation=None, session_id=None, proposal_quality=True):
        pending = self._natural_pending
        self._natural_pending = None  # Consume once; later chat must not replay an old request.
        full_request = voice_request(text) if context.source == "voice" else text
        # Gates see the direct request after a conversational lead-in; the model sees all.
        request = final_request(full_request)
        pending, scoped = self._scope_pending(pending, text, request)
        if self._needs_proposal_clarification(text, scoped, proposal_lease):
            return SkillResult(True, 'Уточніть, будь ласка, що саме потрібно зробити.',
                               {'command_type': 'intent_clarification', 'success': False})
        result = await self._natural_window(text, request, pending, scoped, context)
        if result is not None:
            return result
        model_text = self._natural_model_text(text, full_request, pending, scoped)
        result = await self._natural_local_shortcut(request, scoped, context)
        if result is not None:
            return result
        buffer = SpeechBuffer()
        spoken = []
        assistant_offer = self._assistant_offer(proposal_lease)
        self._last_question = text[:2000]
        self._cache_reply("")
        self._reply_interrupted = True

        async def emit(parts):
            for part in parts:
                part = self._screen_natural_part(part)
                await self.speaker.say(part)
                spoken.append(part)
                self._cache_reply(" ".join(spoken))

        async def on_chunk(chunk):
            # Explicit CHAT supplies screened sentences; other formats wait for validation.
            await emit(buffer.feed(chunk, final=True))

        proposal_options = self._proposal_options(proposal_lease)
        task = asyncio.create_task(self.llm_manager.converse(
            model_text, self.services["memory"].relevant(normalized),
            enabled_skills=self.services.get("enabled_skills", set()), web_context=web_context,
            **self._document_options(model_text),
            **self._knowledge_options(model_text),
            on_chunk=on_chunk, **proposal_options))
        self._chat_task = task
        try:
            turn = await task
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            return SkillResult(True, "", {"command_type": "chat_interrupted"})
        finally:
            self._chat_task = None  # Local operations must not be cancelled by conversation interruption.
        if turn.kind in {'proposal', 'followup'}:
            return await self._natural_proposal(turn, text, context, scoped, proposal_lease,
                                                proposal_generation, session_id, proposal_quality)
        return await self._natural_decision(turn, text, request, model_text, pending, scoped, context,
                                            emit, buffer, spoken, assistant_offer)

    async def _natural_decision(self, turn, text, request, model_text, pending, scoped, context,
                                emit, buffer, spoken, assistant_offer=None):
        """ACTION goes through policy; CHAT/CLARIFY only speak and may keep a scoped question."""
        turn = DialogueDecision.from_natural(turn, context.turn_id)
        logger.info("Natural decision kind=%s tool=%s scoped=%s", turn.kind.value,
                    turn.intent.tool if turn.intent is not None else None, scoped)
        if turn.kind == "action":
            return await self._natural_action(turn, request, text, pending, scoped, context, assistant_offer)
        if turn.kind == "lookup":
            self._reply_interrupted = False
            return await self._run_permission(knowledge_gap_permission(turn, text), context)
        if turn.kind == 'unavailable' and spoken:
            # Preserve partial speech, surface failure, and never rewrite older history.
            await emit([turn.response])
            answer = " ".join(spoken)
            self._cache_reply(answer)
            self._reply_interrupted = True
            return SkillResult(True, answer, {"command_type": "chat", "response_spoken": True})
        await emit(buffer.feed("", final=True))
        answer = self._natural_answer(turn, spoken)
        self._remember_natural_question(turn, answer, request, model_text, pending, scoped)
        return SkillResult(True, answer, {"command_type": "chat", "response_spoken": bool(spoken)})

    @staticmethod
    def _scope_pending(pending, text, request):
        """(pending, scoped): a short live reply continues the pending tool request."""
        scoped = bool(pending and time.monotonic() < pending["expires"]
                      and pending.get("tool") and short_clarification(text)
                      and not direct_request(request, ""))
        if pending and not scoped and pending.get("tool"):
            pending = None
        return pending, scoped

    @staticmethod
    def _screen_natural_part(part):
        part = part.strip("*#` ")
        if LOCAL_ACTION_CLAIM.match(part.lower()):
            part = "Дію ще не виконано. Для запуску потрібен погоджений план."
        return part

    def _needs_proposal_clarification(self, text, scoped, proposal_lease):
        return not scoped and not self.dialogue_state.proposals.current(proposal_lease) and needs_proposal(text)

    def _proposal_options(self, proposal_lease):
        if self.dialogue_state.proposals.current(proposal_lease):
            return {'proposal_context': proposal_lease.proposal.model_context()}
        return {}

    async def _natural_proposal(self, turn, text, context, scoped, proposal_lease,
                                proposal_generation, session_id, proposal_quality):
        if asyncio.current_task().cancelling():
            self.dialogue_state.proposals.clear()
            raise asyncio.CancelledError
        self._reply_interrupted = False
        if not proposal_quality or scoped:
            return SkillResult(True, 'Повторіть повне прохання або уточніть поточний запит; нічого не виконано.',
                               {'command_type': 'intent_clarification', 'success': False})
        return await self._resolve_proposal(turn, text, context, proposal_lease,
                                            proposal_generation, session_id)

    async def _natural_window(self, text, request, pending, scoped, context):
        """Window control from a direct request or a scoped name reply; None if not a window turn."""
        window = window_request(request) if direct_request(request, "window_control") else None
        if scoped and pending["tool"] == "window_control":
            name = application_name_reply(text)
            if name is None:
                self._natural_pending = pending
                return SkillResult(True, "Назвіть програму або заголовок вікна. Самого «так» недостатньо.",
                                   {"command_type": "intent_clarification", "success": False})
            window = (pending["window_action"], name)
        if window is None:
            return None
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

    @staticmethod
    def _natural_model_text(text, request, pending, scoped):
        """Model prompt; a scoped reply continues the pending request instead of starting anew."""
        if not scoped:
            return request
        return (pending["request"] + "\nУточнення користувача: " + text
                + "\nПопереднє уточнювальне питання: " + pending.get("question", "")
                + "\nЦе продовження одного запиту. Запропонуй тільки " + pending["tool"]
                + "; не вигадуй відсутніх параметрів. Дозвіл на виконання запитає локальна система.")

    async def _natural_local_shortcut(self, request, scoped, context):
        """Exact workplace/app requests that need no model; None when the model must decide."""
        # Exact saved-workplace requests need no model permission dialogue.
        local_request = re.sub(r"^(?:рейвен|рейвене|raven)[, ]+", "", request.strip(), flags=re.I)
        if (WORK_MODE.fullmatch(local_request) or local_request.lower().rstrip('.!?') in
                {"підготуй робоче місце", "підготуй мене до роботи", "включи режим робота"}):
            result = await execute_intent(CommandIntent("prepare_workplace", {}), context)
            result.data["natural_action"] = True
            return result
        # A complete, literal installed-app request does not need model inference.
        # No speculative launch: the SAME validator, approval and result checks run.
        intent = await self._resolve_exact_app(request) if not scoped else None
        if intent is not None:
            logger.info("Natural decision kind=action tool=open_app route=local_exact")
            result = await asyncio.wait_for(execute_intent(intent, context), timeout=120)
            result.data["natural_action"] = True
            result.data["intent_route"] = "local_exact"
            return result
        return None

    def _assistant_offer(self, proposal_lease):
        """Raven's previous reply, which may have offered a next action in plain CHAT.

        A live structured proposal is resolved through FOLLOWUP instead.
        """
        if self.dialogue_state.proposals.current(proposal_lease):
            return None
        return self._last_reply or None

    async def _natural_action(self, turn, request, text, pending, scoped, context, assistant_offer=None):
        self._reply_interrupted = False
        permission = natural_action_permission(turn, request, text, pending, scoped,
                                               assistant_offer=assistant_offer)
        permission = knowledge_gap_fallback(permission, turn, request, text, scoped)
        result = await self._run_permission(permission, context)
        self._reply_interrupted = False
        return result

    async def _run_permission(self, permission, context):
        """Shared ALLOW/refusal handling; the executor still previews and confirms."""
        if permission.outcome is not PolicyOutcome.ALLOW:
            return SkillResult(True, permission.response,
                               {"command_type": "intent_clarification", "success": False})
        if permission.route == "knowledge_gap":
            # Honest notice before the executor's exact-query confirmation.
            await self.speaker.say(KNOWLEDGE_GAP_NOTICE)
        result = await asyncio.wait_for(execute_intent(permission.intent, context), timeout=120)
        result.data["natural_action"] = True
        if permission.route != "direct":
            result.data["intent_route"] = permission.route
        return result

    async def _groq_candidate_action(self, turn, candidate_text, prior_clarification, scoped_name,
                                     previous_reply, context):
        """Unscored Groq voice: separate candidate path, not ACTION eligibility or router re-entry.

        Only audited executor branches which ALWAYS preview exact args and ask
        the existing correlated confirmation may be reached.
        """
        from core.command_intent import validate_intent, InvalidIntent
        try:
            intent = validate_intent({'tool': turn.intent.tool, 'arguments': turn.intent.arguments})
        except (InvalidIntent, AttributeError, TypeError):
            return SkillResult(True, 'Не вдалося перевірити дію. Уточніть повне прохання.',
                               {'command_type': 'intent_clarification'})
        if intent.tool not in {'open_app', 'find_files', 'weather', 'web_search'}:
            return SkillResult(True, 'Для цієї дії введіть прохання текстом. Нічого не виконано.',
                               {'command_type': 'intent_clarification'})
        kind = DecisionKind.KNOWLEDGE_GAP if turn.kind == 'lookup' else DecisionKind.ACTION_CANDIDATE
        decision = DialogueDecision(kind, intent=intent, turn_id=context.turn_id)
        if kind is DecisionKind.KNOWLEDGE_GAP:
            return await self._run_permission(knowledge_gap_permission(decision, candidate_text), context)
        # A conversational lead-in must not hide the direct request (J131).
        request = prior_clarification['request'] if scoped_name else final_request(candidate_text)
        permission = natural_action_permission(decision, request, candidate_text,
            prior_clarification if scoped_name else None, bool(scoped_name), assistant_offer=previous_reply)
        permission = knowledge_gap_fallback(permission, decision, request, candidate_text, bool(scoped_name))
        return await self._run_permission(permission, context)

    def _natural_answer(self, turn, spoken):
        """Final CHAT/CLARIFY text: spoken parts win; local-action claims are rewritten."""
        answer = " ".join(spoken) if spoken else turn.response
        if LOCAL_ACTION_CLAIM.match(answer.lower().strip()):
            answer = "Дію ще не виконано. Для запуску потрібен погоджений план."
        if spoken and answer != turn.response:
            self.llm_manager.rewrite_last_answer(answer)
        self._cache_reply(answer)
        self._reply_interrupted = False
        return answer

    def _remember_natural_question(self, turn, answer, request, model_text, pending, scoped):
        """Keep a scoped clarification so a short follow-up can complete the same request."""
        scope = pending["tool"] if scoped else request_scope(request)
        if scope and (turn.kind == "clarify" or (turn.kind == "chat" and "?" in answer)):
            self._natural_pending = {"expires": time.monotonic() + 120, "tool": scope,
                                     "request": model_text[:1600] if scoped else request,
                                     "question": answer[:500]}

    async def _resolve_proposal(self, turn, text, context, lease, generation, session_id):
        proposals = self.dialogue_state.proposals
        unclear = SkillResult(True, 'Уточніть, будь ласка, яку саме дію ви хочете виконати; нічого не виконано.',
                              {'command_type': 'intent_clarification', 'success': False})
        if turn.kind == 'proposal':
            proposal = proposals.offer(turn.intent, context.turn_id, session_id,
                                       self.services.get('enabled_skills', set()), generation)
            if proposal is None:
                return unclear
            question = proposal_question(proposal.intent)
            self._cache_reply(question)
            return SkillResult(True, question, {'command_type': 'action_proposal'})
        reply = turn.proposal_reply
        if not (reply and proposals.current(lease) and reply.proposal_id == lease.proposal.proposal_id):
            logger.info('Contextual reply rejected: stale_or_missing_proposal')
            return unclear
        logger.info('Contextual reply resolution=%s origin_turn=%s turn_id=%s',
                    reply.kind.value, lease.proposal.origin_turn_id, context.turn_id)
        if reply.kind is ReplyKind.REJECT:
            proposals.consume(lease)
            return SkillResult(True, 'Добре, запропоновану дію не виконую.', {'command_type': 'proposal_rejected'})
        if reply.kind is ReplyKind.MODIFY:
            original = proposals.consume(lease)
            if original is None:
                return unclear
            proposal = proposals.offer(CommandIntent(original.tool, dict(reply.arguments)), context.turn_id,
                                       session_id, self.services.get('enabled_skills', set()), proposals.generation)
            if proposal is None:
                return unclear
            question = proposal_question(proposal.intent)
            self._cache_reply(question)
            return SkillResult(True, question, {'command_type': 'action_proposal_modified'})
        if reply.kind is not ReplyKind.ACCEPT:
            proposals.consume(lease)
            return unclear
        proposal = lease.proposal
        candidate = DialogueDecision(DecisionKind.ACTION_CANDIDATE, intent=proposal.intent,
            turn_id=context.turn_id, origin='contextual_followup', proposal_id=proposal.proposal_id)
        permission = natural_action_permission(candidate, text, text, None, False,
                                                contextual_proposal=proposal)
        if permission.outcome is not PolicyOutcome.ALLOW or proposals.consume(lease) is None:
            return unclear
        # Same executor, validation, approval and verification as explicit actions.
        result = await asyncio.wait_for(execute_intent(permission.intent, context), timeout=120)
        result.data['natural_action'] = True
        result.data['intent_route'] = 'contextual_followup'
        return result

    async def _resolve_exact_app(self, request: str) -> CommandIntent | None:
        with self.services.get('performance', DISABLED_PERFORMANCE).span('command.local_app_lookup'):
            return await resolve_exact_app(
                request, getattr(self.services.get('apps'), 'has_exact_name', None),
                apps_enabled='apps' in self.services.get('enabled_skills', set()))

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
                intent = original_search_intent(context.raw_text, intent)
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
