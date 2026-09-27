from __future__ import annotations

import asyncio
import re
import logging
import math
from uuid import uuid4
from collections.abc import Awaitable, Callable


class ConfirmationPrompt(str):
    """Trusted local prompt: full details for UI, concise description for speech."""
    def __new__(cls, details: str, spoken: str, *, caller_reports_result=False, short_question=False):
        value = super().__new__(cls, details)
        value.spoken = spoken
        value.caller_reports_result = caller_reports_result
        value.short_question = short_question
        value.outcome = None
        return value


async def confirm_action(context, details, question):
    """One owner for the cancellation reply; preserve the bool callback contract."""
    from core.models import SkillResult
    prompt = ConfirmationPrompt(details, question, caller_reports_result=True, short_question=True)
    if await context.confirm(prompt):
        return None
    timeout = prompt.outcome == "timeout"
    return SkillResult(True, "Час підтвердження минув. Дію скасовано." if timeout else "Дію скасовано.",
                       {"command_type": "interpretation_cancelled", "accepted": False,
                        "success": False, "status": "cancelled", "reason": "timeout" if timeout else "declined"})


class ConfirmationService:
    ACCEPTED = {
        "так",
        "гаразд",
        "добре",
        "підтверджую",
        "підтверджує",
        "так підтверджую",
        "так підтверджує",
    }
    REJECTED = {
        "ні",
        "не підтверджую",
        "скасувати",
        "скасовую",
        "відміна",
        "не треба",
        "скасуй",
        "відмовляюсь",
    }
    GRAMMAR = sorted(ACCEPTED | REJECTED)
    VOICE_MIN_CONFIDENCE = 0.8

    @classmethod
    def recognition_grammar(cls, model):
        # Ukrainian models may use <UNK>, not the usual Vosk [unk].
        grammar = list(cls.GRAMMAR)
        for unknown in ('[unk]', '<UNK>', '<unk>'):
            index = model.vosk_model_find_word(unknown)
            if isinstance(index, int) and index >= 0:
                grammar.append(unknown)
                break
        logging.getLogger(__name__).info("confirmation_grammar=%s", grammar)
        return grammar

    def __init__(
        self,
        speak: Callable[[str], Awaitable[None]],
        timeout_seconds: int = 15,
        wait_for_speech: Callable[[], Awaitable[None]] | None = None,
    ):
        self.speak = speak
        self.timeout_seconds = timeout_seconds
        self.wait_for_speech = wait_for_speech
        self._responses: asyncio.Queue[tuple[str, float | None]] = asyncio.Queue()
        self._request_event = asyncio.Event()
        self._response_processed = asyncio.Event()
        self._response_processed.set()
        self._lock = asyncio.Lock()
        self.request_id = None
        self.prompt = ""

    @property
    def awaiting(self) -> bool:
        return self._request_event.is_set()

    async def wait_until_requested(self) -> None:
        await self._request_event.wait()

    async def wait_until_response_processed(self) -> None:
        await self._response_processed.wait()

    def submit(self, text: str, request_id: str | None = None, *, confidence: float | None = None) -> bool:
        if not self.awaiting or request_id is None or request_id != self.request_id:
            logging.getLogger(__name__).info("confirmation_reply_rejected=stale_or_uncorrelated")
            return False
        self._response_processed.clear()
        self._responses.put_nowait((text, confidence))
        return True

    async def ask(self, operation: str) -> bool:
        async with self._lock:
            self._clear_responses()
            self.request_id = uuid4().hex
            self._request_event.set()
            prompt = f"Підтвердіть операцію: {operation}. Скажіть так або ні."
            self.prompt = prompt
            try:
                print(f"[CONFIRM] {prompt}")
                spoken = operation.spoken if isinstance(operation, ConfirmationPrompt) else operation
                short = isinstance(operation, ConfirmationPrompt) and operation.short_question
                await self.speak(f"{spoken.rstrip(' .?!')}? Так чи ні." if short
                                 else f"Підтвердіть операцію: {spoken}. Скажіть так або ні.")
                if self.wait_for_speech:
                    await self.wait_for_speech()
                loop = asyncio.get_running_loop()
                deadline = loop.time() + self.timeout_seconds
                while True:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        await self._cancel_notice(operation, "timeout")
                        return False
                    try:
                        answer, confidence = await asyncio.wait_for(
                            self._responses.get(),
                            timeout=remaining,
                        )
                    except asyncio.TimeoutError:
                        await self._cancel_notice(operation, "timeout")
                        return False

                    decision = self._decision(answer, confidence)
                    if decision is True:
                        if isinstance(operation, ConfirmationPrompt):
                            operation.outcome = "approved"
                        self._response_processed.set()
                        return True
                    if decision is False:
                        await self._cancel_notice(operation, "declined")
                        self._response_processed.set()
                        return False
                    await self.speak("Не розібрав відповідь. Скажіть так або ні.")
                    if self.wait_for_speech:
                        await self.wait_for_speech()
                    self._response_processed.set()
            finally:
                self.request_id = None
                self.prompt = ""
                self._request_event.clear()
                self._response_processed.set()
                self._clear_responses()

    async def _cancel_notice(self, operation, outcome):
        if isinstance(operation, ConfirmationPrompt):
            operation.outcome = outcome
            if operation.caller_reports_result:
                return
        await self.speak("Час підтвердження минув. Операцію скасовано." if outcome == "timeout"
                         else "Операцію скасовано.")

    @classmethod
    def _decision(cls, answer: str, confidence: float | None = None) -> bool | None:
        if confidence is not None and (not math.isfinite(confidence)
                                       or not cls.VOICE_MIN_CONFIDENCE <= confidence <= 1):
            return None
        normalized = " ".join(
            re.sub(r"[^0-9a-zа-яіїєґ'’\s]", " ", answer.lower()).split()
        )
        if normalized in cls.ACCEPTED:
            return True
        if normalized in cls.REJECTED:
            return False
        return None

    def _clear_responses(self) -> None:
        while not self._responses.empty():
            try:
                self._responses.get_nowait()
            except asyncio.QueueEmpty:
                break
