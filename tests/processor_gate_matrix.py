"""Shared fixture for the processor gate characterization (audit 2026-10-29 #3).

Observes only side effects that matter for safety: which path a turn reached
(router, chat LLM, natural converse, document, memory) and whether a
confirmation was requested. Not a test module by itself.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from core.models import SkillResult, TurnEnvelope
from core.natural_turn import NaturalTurn
from core.processor import CommandProcessor

TRUST = {
    "reliable": {},
    "not_eligible": {"action_eligible": False},
    "unreliable": {"recognition_unreliable": True, "action_eligible": False},
    "truncated": {"capture_truncated": True, "action_eligible": False},
    "incomplete": {"utterance_incomplete": True, "clarification_required": True,
                   "action_eligible": False},
    "unscored_groq": {"stt_engine": "groq", "confidence": 0.0, "confidence_available": False,
                      "action_eligible": False},
}
TEXTS = {
    "command": "Команда: відкрий браузер",
    "empty_command": "Команда",
    "chat": "Розкажи про погоду на Марсі",
    "natural_open": "Відкрий браузер",
    "stop": "стоп",
    "pause": "пауза",
    "document": "прочитай файл звіт.txt",
    "memory": "Запам’ятай проєкт: Рейвен",
    "ip": "192.168.1.10",
}
MODES = ("chat", "pentest")
NATURAL = (False, True)


def build(mode: str, natural: bool):
    router = SimpleNamespace(calls=[])

    async def route(command, context, allowed_skills=None):
        router.calls.append((command, None if allowed_skills is None else sorted(allowed_skills)))
        return SkillResult(True, "routed", {"command_type": "routed"})

    router.route = route
    llm = SimpleNamespace(
        active_name="stub",
        chat=AsyncMock(return_value="chat-response"),
        converse=AsyncMock(return_value=NaturalTurn("chat", "converse-response")),
        interpret_command=AsyncMock(return_value=None),
    )
    memory = SimpleNamespace(relevant=Mock(return_value=[]), all=Mock(return_value=[]),
                             remember=Mock())
    services = {
        "state": {"mode": mode},
        "memory": memory,
        "enabled_skills": {"files", "memory", "apps", "pentest"},
    }
    speaker = SimpleNamespace(stop=AsyncMock(), say=AsyncMock())
    settings = SimpleNamespace(natural_actions_enabled=natural,
                               command_interpretation_enabled=False)
    processor = CommandProcessor(settings, router, llm, speaker,
                                 SimpleNamespace(record=Mock()), services)
    processor.documents.handle = AsyncMock(return_value=SkillResult(True, "document"))
    return processor, router, llm, memory, speaker


def envelope(text: str, trust: str) -> TurnEnvelope:
    base = TurnEnvelope(turn_id="matrix", session_id="matrix", source="voice", text=text,
                        transcript=text, stt_engine="vosk", confidence=0.95)
    return replace(base, **TRUST[trust])


async def observe(mode: str, natural: bool, text_kind: str, trust: str) -> tuple:
    processor, router, llm, memory, speaker = build(mode, natural)
    confirm = AsyncMock(return_value=False)
    result = await asyncio.wait_for(
        processor.process(envelope(TEXTS[text_kind], trust), confirm), timeout=5)
    return (
        result.data.get("command_type", ""),
        bool(router.calls),
        router.calls[0][1] if router.calls else None,
        llm.chat.await_count > 0,
        llm.converse.await_count > 0,
        processor.documents.handle.await_count > 0,
        confirm.await_count > 0,
        memory.remember.called,
        speaker.stop.await_count > 0,
    )


FIELDS = ("command_type", "routed", "allowed_skills", "chat_llm", "converse", "document",
          "confirm_asked", "memory_written", "speech_stopped")


def cases():
    for mode in MODES:
        for natural in NATURAL:
            for text_kind in TEXTS:
                for trust in TRUST:
                    yield mode, natural, text_kind, trust
