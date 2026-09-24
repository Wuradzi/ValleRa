import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from google.genai import types

from core.atomic_json import AtomicJSONFile
from core.processor import CommandProcessor, VOICE_INPUT_PROMPT
from services.llm.errors import ResponseError
from services.llm.manager import LLMManager, CHAT_MODE_PROMPT
from services.llm.providers import GeminiProvider
from services.llm.response_diagnostics import ResponseDiagnostics


def response(text=None, finish="STOP", **kwargs):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(parts=[types.Part(text=text)]) if text is not None else None,
        finish_reason=finish, **kwargs,
    )])


class EmptyRetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.provider = GeminiProvider("synthetic-model", "synthetic-key")
        self.manager = object.__new__(LLMManager)
        self.manager.settings = SimpleNamespace(history_limit=50, llm_total_timeout_seconds=30,
                                                llm_failures_before_switch=3)
        self.manager.system_prompt = "Synthetic system context"
        self.manager.history = AtomicJSONFile(Path(temporary.name) / "history.json", {"messages": []})
        self.backup = SimpleNamespace(chat=AsyncMock(), chat_stream=Mock())
        self.manager.providers = {"gemini": self.provider, "groq": self.backup}
        self.manager.available_order = ["gemini", "groq"]
        self.manager.active_name = "gemini"
        self.manager._cooldowns = {}

    async def run_responses(self, attempts, *, streaming=True):
        closed = []

        async def stream(items):
            try:
                for item in items:
                    if isinstance(item, BaseException):
                        raise item
                    yield item
            finally:
                closed.append(True)

        call = AsyncMock(side_effect=[stream(items) for items in attempts]) if streaming else AsyncMock(
            side_effect=[items[0] for items in attempts])
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(
            generate_content_stream=call, generate_content=call)))
        callback = AsyncMock() if streaming else None
        with patch.object(self.provider, "_client", return_value=client), patch.object(
            self.provider, "_close_client", new_callable=AsyncMock
        ), patch("builtins.print"), self.assertLogs("services.llm.manager", level="ERROR"):
            answer = await self.manager.chat("Synthetic user context", on_chunk=callback)
        if streaming:
            self.assertEqual(len(closed), call.await_count)
        self.backup.chat.assert_not_awaited()
        self.backup.chat_stream.assert_not_called()
        return answer, call, callback

    async def test_empty_then_success_preserves_request_and_saves_only_final_answer(self):
        answer, call, callback = await self.run_responses([[response(), types.GenerateContentResponse()], [response("Привіт!")]])
        self.assertEqual(answer, "Привіт!")
        self.assertEqual(call.await_count, 2)
        self.assertEqual(call.await_args_list[0], call.await_args_list[1])
        callback.assert_awaited_once_with("Привіт!")
        self.assertEqual(len(self.manager.history.load()["messages"]), 2)

    async def test_non_streaming_empty_then_success(self):
        answer, call, _ = await self.run_responses([[response()], [response("Привіт!")]], streaming=False)
        self.assertEqual(answer, "Привіт!")
        self.assertEqual(call.await_count, 2)

    async def test_two_empties_do_not_retry_again_or_use_backup_or_save_error(self):
        answer, call, callback = await self.run_responses([[response()], [response()]])
        self.assertEqual(call.await_count, 2)
        self.assertIn("без тексту", answer)
        callback.assert_not_awaited()
        self.assertEqual(self.manager.history.load()["messages"], [])

    async def test_network_failure_on_empty_retry_cannot_start_another_retry(self):
        _, call, _ = await self.run_responses([[response()], [ConnectionError("PRIVATE_CONTENT")]])
        self.assertEqual(call.await_count, 2)

    async def test_rate_limit_after_empty_keeps_cooldown_and_stops_requests(self):
        error = RuntimeError("PRIVATE_QUOTA_BODY")
        error.code = 429
        answer, call, _ = await self.run_responses([[response()], [error]])
        self.assertEqual(call.await_count, 2)
        self.assertIn("квоти", answer)
        self.assertIn("gemini", self.manager._cooldowns)

    async def test_cancelled_empty_retry_does_not_become_another_attempt(self):
        calls = 0

        async def chat(messages):
            nonlocal calls
            calls += 1
            if calls == 1:
                error = ResponseError("empty_response", "STOP")
                probe = ResponseDiagnostics()
                probe.observe(response())
                probe.attach(error)
                raise error
            raise asyncio.CancelledError()

        with patch.object(self.provider, "chat", side_effect=chat), patch("builtins.print"), self.assertLogs(
            "services.llm.manager", level="ERROR"
        ), self.assertRaises(asyncio.CancelledError):
            await self.manager.chat("Synthetic context")
        self.assertEqual(calls, 2)
        self.backup.chat.assert_not_awaited()
        self.assertEqual(self.manager.history.load()["messages"], [])

    async def test_error_log_allowlist_drops_untrusted_diagnostic_values(self):
        error = ResponseError("empty_response", "STOP")
        error.diagnostics = {"parts": "PRIVATE_PART", "secret": "PRIVATE_SECRET", "responses": 1}
        with patch.object(self.provider, "chat", side_effect=error), patch("builtins.print") as output, self.assertLogs(
            "services.llm.manager", level="WARNING"
        ) as logs:
            await self.manager.chat("Synthetic context")
        self.assertNotIn("PRIVATE", str(logs.output) + str(output.call_args_list))
        self.assertIn("'responses': 1", str(logs.output))

    async def test_block_and_unknown_empty_are_never_retried(self):
        for value in (response(finish="SAFETY"), response(finish=None), types.GenerateContentResponse(),
                      response(safety_ratings=[types.SafetyRating(blocked=True)])):
            with self.subTest(value=type(value).__name__):
                _, call, _ = await self.run_responses([[value]])
                call.assert_awaited_once()

    async def test_partial_stream_is_not_replayed(self):
        _, call, callback = await self.run_responses([[response("Привіт. ", finish=None), response(finish="MAX_TOKENS")]])
        call.assert_awaited_once()
        self.assertEqual(callback.await_args_list[0].args, ("Привіт. ",))

    async def test_insufficient_budget_and_explicit_single_attempt_skip_retry(self):
        for budget, attempts in ((.5, 3), (30, 1)):
            self.manager.settings.llm_total_timeout_seconds = budget
            self.manager.settings.llm_failures_before_switch = attempts
            _, call, _ = await self.run_responses([[response()]])
            call.assert_awaited_once()

    async def test_empty_retry_is_capped_at_ten_seconds(self):
        real_wait_for = asyncio.wait_for
        budgets = []

        async def tracked(awaitable, timeout):
            budgets.append(timeout)
            return await real_wait_for(awaitable, timeout)

        with patch("services.llm.manager.asyncio.wait_for", side_effect=tracked):
            await self.run_responses([[response()], [response("Привіт!")]])
        self.assertGreater(budgets[0], 10)
        self.assertLessEqual(budgets[1], 10)


class ShapeTests(unittest.TestCase):
    def test_diagnostics_contain_only_numeric_shape_not_content(self):
        probe = ResponseDiagnostics()
        probe.observe(response("PRIVATE_TEXT", finish_message="PRIVATE_MESSAGE"))
        error = ResponseError("empty_response", "STOP")
        probe.attach(error)
        self.assertFalse(error.empty_retry_safe)
        self.assertEqual(probe.counts["visible_text_parts"], 1)
        self.assertTrue(all(type(value) is int for value in error.diagnostics.values()))
        self.assertNotIn("PRIVATE", str(vars(probe)) + str(error.diagnostics) + str(error))

    def test_non_text_thought_and_ambiguous_metadata_prevent_retry(self):
        for part in (types.Part(function_call=types.FunctionCall(name="private_function")),
                     types.Part(text="private_thought", thought=True),
                     types.Part(thought_signature=b"private_signature")):
            probe = ResponseDiagnostics()
            probe.observe(types.GenerateContentResponse(candidates=[types.Candidate(
                content=types.Content(parts=[part]), finish_reason="STOP")]))
            error = ResponseError("empty_response", "STOP")
            probe.attach(error)
            self.assertFalse(error.empty_retry_safe)
            self.assertNotIn("private", str(vars(probe)))
        probe = ResponseDiagnostics()
        probe.observe(response(finish="OTHER"))
        probe.observe(response())
        error = ResponseError("empty_response", "STOP")
        probe.attach(error)
        self.assertFalse(error.empty_retry_safe)


class ConversationPromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_voice_context_does_not_contaminate_text_or_trim_requested_detail(self):
        long_answer = "Детальне пояснення. " * 30
        manager = SimpleNamespace(active_name="synthetic", chat=AsyncMock(return_value=long_answer))
        processor = CommandProcessor(SimpleNamespace(), Mock(), manager, SimpleNamespace(say=AsyncMock()),
                                     SimpleNamespace(record=Mock()),
                                     {"state": {"mode": "chat"}, "memory": SimpleNamespace(relevant=lambda _: [])})
        for source in ("voice", "text"):
            result = await processor.process("Поясни докладно", AsyncMock(), source)
            self.assertEqual(result.response, long_answer)
            prompt = manager.chat.call_args.args[2]
            self.assertIn(CHAT_MODE_PROMPT, prompt)
            self.assertEqual(VOICE_INPUT_PROMPT in prompt, source == "voice")
