import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.atomic_json import AtomicJSONFile
from core.processor import CommandProcessor
from core.performance import PerformanceRecorder
from core.speech_text import SpeechBuffer
from services.llm.manager import LLMManager
from services.llm.providers import GeminiProvider


class SpeechBufferTests(unittest.TestCase):
    def test_fragmented_tokens_form_sentences_without_loss(self):
        buffer = SpeechBuffer()
        self.assertEqual(buffer.feed("Пер"), [])
        self.assertEqual(buffer.feed("ша фраза. "), ["Перша фраза."])
        self.assertEqual(buffer.feed("Друга"), [])
        self.assertEqual(buffer.feed(" фраза", final=True), ["Друга фраза"])

    def test_unpunctuated_long_text_is_bounded(self):
        text = "слово " * 200
        chunks = SpeechBuffer().feed(text, final=True)
        self.assertTrue(all(len(chunk) <= 240 for chunk in chunks))
        self.assertEqual(" ".join(chunks), text.strip())


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    def manager(self, provider):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        manager = object.__new__(LLMManager)
        manager.settings = SimpleNamespace(
            history_limit=50, llm_total_timeout_seconds=2, llm_failures_before_switch=2
        )
        manager.system_prompt = "Валера"
        manager.history = AtomicJSONFile(Path(temp.name) / "history.json", {"messages": []})
        manager.providers = {"gemini": provider}
        manager.active_name = "gemini"
        manager._cooldowns = {}
        manager.available_order = ["gemini"]
        return manager

    async def test_first_phrase_is_spoken_before_model_finishes(self):
        first_spoken = asyncio.Event()
        finish = asyncio.Event()

        async def stream(messages):
            yield "Привіт! "
            await finish.wait()
            yield "Як справи?"

        llm = self.manager(SimpleNamespace(chat_stream=stream))
        timing_metrics = Mock()
        llm.performance = PerformanceRecorder(timing_metrics)
        speaker = SimpleNamespace(say=AsyncMock(side_effect=lambda _: first_spoken.set()))
        processor = CommandProcessor(
            SimpleNamespace(),
            Mock(),
            llm,
            speaker,
            SimpleNamespace(record=Mock()),
            {
                "state": {"mode": "chat"},
                "memory": SimpleNamespace(relevant=lambda _: []),
                "performance": llm.performance,
            },
        )
        task = asyncio.create_task(processor.process("привіт", AsyncMock()))
        try:
            await asyncio.wait_for(first_spoken.wait(), 1)
            self.assertFalse(task.done())
            stages = [call.kwargs["stage"] for call in timing_metrics.record.call_args_list]
            self.assertIn("llm.gemini.first_text", stages)
            self.assertIn("response.first_phrase_to_tts_queue", stages)
            self.assertNotIn("llm.gemini.request_including_callbacks", stages)
        finally:
            finish.set()
        result = await task
        self.assertTrue(result.data["response_spoken"])
        self.assertEqual(
            [c.args[0] for c in speaker.say.await_args_list], ["Привіт!", "Як справи?"]
        )
        self.assertEqual(len(llm.history.load()["messages"]), 2)

    async def test_partial_stream_failure_does_not_retry_or_duplicate(self):
        calls = 0

        async def stream(messages):
            nonlocal calls
            calls += 1
            yield "Частина відповіді. "
            raise ConnectionError("test")

        manager = self.manager(SimpleNamespace(chat_stream=stream))
        callback = AsyncMock()
        with self.assertLogs("services.llm.manager", level="ERROR"):
            result = await manager.chat("привіт", on_chunk=callback)
        self.assertEqual(calls, 1)
        self.assertIn("лише частину", result)
        self.assertEqual(result.count("Частина відповіді"), 1)
        self.assertEqual(len(manager.history.load()["messages"]), 2)

    async def test_guard_applies_before_streamed_action_claim_is_spoken(self):
        async def stream(messages):
            yield "**Відкриваю "
            yield "Telegram.** "

        llm = self.manager(SimpleNamespace(chat_stream=stream))
        speaker = SimpleNamespace(say=AsyncMock())
        processor = CommandProcessor(
            SimpleNamespace(),
            Mock(),
            llm,
            speaker,
            SimpleNamespace(record=Mock()),
            {
                "state": {"mode": "chat"},
                "memory": SimpleNamespace(relevant=lambda _: []),
            },
        )
        result = await processor.process("відкрий телеграм", AsyncMock())
        self.assertIn("не виконую локальні дії", result.response)
        self.assertNotIn("Відкриваю", speaker.say.await_args.args[0])

    async def test_gemini_stream_uses_native_async_api_and_releases_lease(self):
        async def chunks():
            yield SimpleNamespace(text="Привіт")
            yield SimpleNamespace(text="!")

        client = SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(generate_content_stream=AsyncMock(return_value=chunks()))
            )
        )
        provider = GeminiProvider("test-model", "test-key")
        with (
            patch.object(provider, "_client", return_value=client),
            patch.object(provider, "_release_client", new_callable=AsyncMock) as release,
            patch.object(provider, "_close_client", new_callable=AsyncMock) as close,
        ):
            result = [
                piece
                async for piece in provider.chat_stream([{"role": "user", "content": "привіт"}])
            ]
        self.assertEqual(result, ["Привіт", "!"])
        release.assert_awaited_once_with(client)
        close.assert_not_awaited()
