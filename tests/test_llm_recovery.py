import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from google.genai import types
from google.genai.errors import ClientError

from core.atomic_json import AtomicJSONFile
from core.logging_setup import RedactingFilter
from core.processor import CommandProcessor
from services.llm.errors import ResponseError, classify_failure
from services.llm.manager import LLMManager
from services.llm.providers import GeminiProvider


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def manager(self, *providers):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        manager = object.__new__(LLMManager)
        manager.settings = SimpleNamespace(
            history_limit=50, llm_total_timeout_seconds=2, llm_failures_before_switch=3,
        )
        manager.system_prompt = "Тебе звати Валера."
        manager.history = AtomicJSONFile(Path(temp.name) / "history.json", {"messages": []})
        manager.providers = dict(zip(["gemini", "groq"], providers))
        manager.available_order = list(manager.providers)
        manager.active_name = manager.available_order[0] if providers else None
        manager._cooldowns = {}
        return manager

    def processor(self, manager):
        return CommandProcessor(
            SimpleNamespace(), Mock(), manager, SimpleNamespace(say=AsyncMock()),
            SimpleNamespace(record=Mock()),
            {"state": {"mode": "chat"}, "memory": SimpleNamespace(relevant=lambda _: [])},
        )

    async def test_success_empty_then_success_through_voice_processor(self):
        calls = 0

        async def stream(messages):
            nonlocal calls
            calls += 1
            if calls == 2:
                yield "   "
            else:
                yield "Так, я на зв'язку."

        manager = self.manager(SimpleNamespace(chat_stream=stream))
        processor = self.processor(manager)
        first = await processor.process("Привіт", AsyncMock())
        self.assertTrue(first.data["response_spoken"])
        with self.assertLogs("services.llm.manager", level="ERROR"), patch("builtins.print") as output:
            failed = await processor.process("Розкажи щось", AsyncMock())
        self.assertIn("без тексту", failed.response)
        self.assertFalse(failed.data["response_spoken"])
        self.assertEqual(manager.active_name, "gemini")
        self.assertEqual(calls, 2)  # No blind retries on a completed empty response.
        self.assertEqual(len(manager.history.load()["messages"]), 2)
        self.assertNotIn("резерв", str(output.call_args_list))
        recovered = await processor.process("Ти на зв'язку?", AsyncMock())
        self.assertEqual(recovered.response, "Так, я на зв'язку.")
        self.assertEqual(calls, 3)
        self.assertEqual(len(manager.history.load()["messages"]), 4)

    async def test_network_exhaustion_does_not_disable_next_request(self):
        chat = AsyncMock(side_effect=[ConnectionError("offline")] * 3 + ["Знову тут."])
        manager = self.manager(SimpleNamespace(chat=chat))
        with (
            self.assertLogs("services.llm.manager", level="ERROR"),
            patch("builtins.print"), patch("services.llm.manager.asyncio.sleep", new_callable=AsyncMock),
        ):
            failed = await manager.chat("Привіт")
        self.assertIn("перезапускати асистента не потрібно", failed)
        self.assertEqual(manager.active_name, "gemini")
        self.assertEqual(await manager.chat("Ще раз"), "Знову тут.")

    async def test_missing_active_flag_does_not_bypass_manager(self):
        async def stream(messages):
            yield "Знову тут."

        manager = self.manager(SimpleNamespace(chat_stream=stream))
        manager.active_name = None
        result = await self.processor(manager).process("Привіт", AsyncMock())
        self.assertEqual(result.response, "Знову тут.")
        self.assertEqual(manager.active_name, "gemini")

    async def test_no_provider_is_configuration_not_voice_failure(self):
        result = await self.processor(self.manager()).process("Привіт", AsyncMock())
        self.assertIn("не налаштовано", result.response)
        self.assertNotIn("скасувати", result.response)

    async def test_empty_non_stream_response_is_not_saved_or_retried(self):
        chat = AsyncMock(return_value="  ")
        manager = self.manager(SimpleNamespace(chat=chat))
        with self.assertLogs("services.llm.manager", level="ERROR"), patch("builtins.print"):
            result = await manager.chat("Привіт")
        self.assertIn("без тексту", result)
        chat.assert_awaited_once()
        self.assertEqual(manager.history.load()["messages"], [])

    async def test_block_is_not_retried_or_forwarded_to_backup(self):
        chat = AsyncMock(side_effect=ResponseError("response_blocked", "SAFETY"))
        backup = AsyncMock(return_value="should not be requested")
        manager = self.manager(SimpleNamespace(chat=chat), SimpleNamespace(chat=backup))
        with self.assertLogs("services.llm.manager", level="ERROR"), patch("builtins.print"):
            result = await manager.chat("Синтетична тестова репліка")
        self.assertIn("Сервіс обмежив", result)
        self.assertNotIn("недоступ", result)
        chat.assert_awaited_once()
        backup.assert_not_awaited()
        self.assertEqual(manager.active_name, "gemini")

    async def test_failed_backup_can_return_to_previous_provider(self):
        primary = AsyncMock(return_value="Відновлено.")
        backup = AsyncMock(side_effect=RuntimeError("synthetic"))
        manager = self.manager(SimpleNamespace(chat=primary), SimpleNamespace(chat=backup))
        manager.active_name = "groq"
        with self.assertLogs("services.llm.manager", level="ERROR"), patch("builtins.print"):
            result = await manager.chat("Привіт")
        self.assertEqual(result, "Відновлено.")
        self.assertEqual(manager.active_name, "gemini")

    async def test_quota_error_does_not_rapidly_retry_or_log_response_body(self):
        exc = RuntimeError("PRIVATE_PROMPT api_key=DO_NOT_LOG")
        exc.code = 429
        chat = AsyncMock(side_effect=exc)
        manager = self.manager(SimpleNamespace(chat=chat))
        with self.assertLogs("services.llm.manager", level="ERROR") as log, patch("builtins.print") as output:
            result = await manager.chat("Тест")
        chat.assert_awaited_once()
        self.assertIn("квоти", result)
        self.assertIn("status=429", " ".join(log.output))
        for text in [result, " ".join(log.output), str(output.call_args_list)]:
            self.assertNotIn("PRIVATE_PROMPT", text)
            self.assertNotIn("DO_NOT_LOG", text)

    async def test_cooldown_prevents_calls_until_deadline_then_recovers(self):
        error = ClientError(429, {"error": {"details": [{
            "@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "30s",
        }]}})
        chat = AsyncMock(side_effect=[error, "Відновлено."])
        manager = self.manager(SimpleNamespace(chat=chat))
        with patch("services.llm.manager.monotonic", return_value=100) as clock:
            with self.assertLogs("services.llm.manager", level="ERROR"), patch("builtins.print"):
                result = await manager.chat("Тест")
            self.assertIn("30 секунд", result)
            clock.return_value = 110
            with patch.object(manager.history, "load") as history:
                result = await manager.chat("Ти тут?")
            history.assert_not_called()
            self.assertIn("20 секунд", result)
            self.assertEqual(chat.await_count, 1)
            clock.return_value = 130
            self.assertEqual(await manager.chat("Знову"), "Відновлено.")
        self.assertEqual(chat.await_count, 2)
        self.assertEqual(manager._cooldowns, {})

    async def test_unknown_429_defaults_to_60_seconds_without_sleeping(self):
        error = ClientError(429, {"error": {"message": "private"}})
        chat = AsyncMock(side_effect=error)
        manager = self.manager(SimpleNamespace(chat=chat))
        with (
            patch("services.llm.manager.monotonic", return_value=100),
            patch("services.llm.manager.asyncio.sleep", new_callable=AsyncMock) as sleep,
            self.assertLogs("services.llm.manager", level="ERROR"), patch("builtins.print"),
        ):
            result = await manager.chat("Тест")
            await manager.chat("Тест знову")
        self.assertIn("60 секунд", result)
        chat.assert_awaited_once()
        sleep.assert_not_awaited()

    async def test_cooling_provider_is_skipped_when_backup_is_available(self):
        chat = AsyncMock(side_effect=ClientError(429, {}))
        backup = AsyncMock(return_value="Резерв відповідає.")
        manager = self.manager(SimpleNamespace(chat=chat), SimpleNamespace(chat=backup))
        with self.assertLogs("services.llm.manager", level="ERROR"), patch("builtins.print"):
            self.assertEqual(await manager.chat("Тест"), "Резерв відповідає.")
        manager.active_name = "gemini"  # E.g. advisory startup healthcheck result.
        self.assertEqual(await manager.chat("Знову"), "Резерв відповідає.")
        chat.assert_awaited_once()
        self.assertEqual(backup.await_count, 2)

    async def test_all_cooling_providers_report_earliest_future_deadline(self):
        first = AsyncMock()
        second = AsyncMock()
        manager = self.manager(SimpleNamespace(chat=first), SimpleNamespace(chat=second))
        failure = classify_failure(ClientError(429, {}))
        manager._cooldowns = {"gemini": (180, failure), "groq": (120, failure)}
        with patch("services.llm.manager.monotonic", return_value=100):
            self.assertIn("20 секунд", await manager.chat("Тест"))
        first.assert_not_awaited()
        second.assert_not_awaited()

    def test_expired_cooldown_is_not_reported_instead_of_future_one(self):
        manager = self.manager(SimpleNamespace(), SimpleNamespace())
        failure = classify_failure(ClientError(429, {}))
        manager._cooldowns = {"gemini": (90, failure), "groq": (120, failure)}
        with patch("services.llm.manager.monotonic", return_value=100):
            self.assertIn("20 секунд", manager._cooldown_response(manager.available_order))

    async def test_new_conversation_does_not_reset_provider_cooldown(self):
        chat = AsyncMock()
        manager = self.manager(SimpleNamespace(chat=chat))
        failure = classify_failure(ClientError(429, {}))
        manager._cooldowns["gemini"] = (180, failure)
        manager.new_conversation()
        with patch("services.llm.manager.monotonic", return_value=100):
            self.assertIn("80 секунд", await manager.chat("Нова тема"))
        chat.assert_not_awaited()

    async def test_cancellation_propagates_without_retry(self):
        chat = AsyncMock(side_effect=asyncio.CancelledError())
        manager = self.manager(SimpleNamespace(chat=chat))
        with self.assertRaises(asyncio.CancelledError):
            await manager.chat("Привіт")
        chat.assert_awaited_once()

    async def test_total_deadline_preserves_candidate(self):
        async def slow(messages):
            await asyncio.sleep(1)

        manager = self.manager(SimpleNamespace(chat=slow))
        manager.settings.llm_total_timeout_seconds = .01
        with self.assertLogs("services.llm.manager", level="ERROR"), patch("builtins.print"):
            result = await manager.chat("Тест")
        self.assertIn("дочекатися", result)
        self.assertEqual(manager.active_name, "gemini")

    async def test_output_limit_diagnostic_survives_secret_log_filter(self):
        manager = self.manager(SimpleNamespace(chat=AsyncMock(
            side_effect=ResponseError("output_limit", "MAX_TOKENS"),
        )))
        with self.assertLogs("services.llm.manager", level="ERROR") as log, patch("builtins.print"):
            await manager.chat("Тест")
        record = log.records[0]
        RedactingFilter().filter(record)
        self.assertIn("kind=output_limit", record.getMessage())
        self.assertIn("reason=MAX_OUTPUT", record.getMessage())

    async def test_history_write_failure_does_not_retry_completed_generation(self):
        chat = AsyncMock(return_value="Відповідь.")
        manager = self.manager(SimpleNamespace(chat=chat))
        with patch.object(manager, "_append_history", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await manager.chat("Привіт")
        chat.assert_awaited_once()


class ResponseMetadataTests(unittest.IsolatedAsyncioTestCase):
    def test_rate_limit_retry_info_and_header_use_longer_delay(self):
        response = httpx.Response(429, headers={"Retry-After": "45"})
        exc = ClientError(429, {"error": {"details": [
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "12.5s"},
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
                {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"},
                {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"},
            ]},
        ]}}, response=response)
        failure = classify_failure(exc)
        self.assertEqual(failure.retry_after_seconds, 45)
        self.assertEqual(failure.reason, "DAILY_QUOTA")
        self.assertIn("добової", failure.message)

    def test_retry_after_http_date(self):
        now = datetime(2026, 9, 3, tzinfo=timezone.utc)
        response = httpx.Response(429, headers={"Retry-After": format_datetime(now + timedelta(seconds=90))})
        with patch("services.llm.errors.datetime") as clock:
            clock.now.return_value = now
            self.assertEqual(classify_failure(ClientError(429, {}, response)).retry_after_seconds, 90)

    def test_malformed_metadata_does_not_break_error_handling(self):
        for value in [None, -1, "NaN", "inf", "-3s", "secret", "1e309s"]:
            exc = ClientError(429, {"error": {"details": [
                None, {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": value},
                {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": None},
            ]}}, response=httpx.Response(429, headers={"Retry-After": str(value)}))
            with self.subTest(value=value):
                self.assertIsNone(classify_failure(exc).retry_after_seconds)

    def test_minute_quota_and_fractional_delay(self):
        exc = ClientError(429, {"details": [
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "2.25s"},
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
                {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"},
            ]},
        ]})
        failure = classify_failure(exc)
        self.assertEqual(failure.retry_after_seconds, 2.25)
        self.assertEqual(failure.reason, "MINUTE_QUOTA")

    async def test_gemini_sdk_does_not_multiply_manager_retries(self):
        provider = GeminiProvider("test-model", "test-key")
        with patch("google.genai.Client") as client, patch.object(provider, '_close_client', new_callable=AsyncMock):
            provider._client(60)
            await provider.close()
        self.assertEqual(client.call_args.kwargs["http_options"].retry_options.attempts, 1)

    def response(self, text=None, finish=None, block=None):
        return types.GenerateContentResponse(
            candidates=[types.Candidate(
                content=types.Content(parts=[types.Part(text=text)]) if text is not None else None,
                finish_reason=finish,
            )],
            prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason=block) if block else None,
        )

    async def run_stream(self, responses, expected_kind=None):
        closed = Mock()

        async def stream():
            try:
                for response in responses:
                    yield response
            finally:
                closed()

        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(
            generate_content_stream=AsyncMock(return_value=stream()),
        )))
        provider = GeminiProvider("test-model", "test-key")
        pieces = []
        with patch.object(provider, "_client", return_value=client), patch.object(
            provider, "_release_client", new_callable=AsyncMock,
        ) as release:
            if expected_kind:
                with self.assertRaises(ResponseError) as caught:
                    async for text in provider.chat_stream([{"role": "user", "content": "Тест"}]):
                        pieces.append(text)
                self.assertEqual(caught.exception.kind, expected_kind)
                reason = caught.exception.reason
            else:
                pieces = [text async for text in provider.chat_stream([{"role": "user", "content": "Тест"}])]
                reason = None
        closed.assert_called_once()
        release.assert_awaited_once_with(client)
        return pieces, reason

    async def test_empty_stop_diagnosed_even_with_trailing_metadata(self):
        _, reason = await self.run_stream([
            self.response(finish="STOP"), types.GenerateContentResponse(),
        ], "empty_response")
        self.assertEqual(reason, "STOP")

    async def test_prompt_feedback_block(self):
        pieces, reason = await self.run_stream([self.response(block="SAFETY")], "response_blocked")
        self.assertEqual(reason, "SAFETY")
        self.assertEqual(pieces, [])

    async def test_blocked_chunk_text_is_not_forwarded(self):
        for finish in ["SAFETY", "RECITATION", "PROHIBITED_CONTENT", "SPII"]:
            with self.subTest(finish=finish):
                pieces, reason = await self.run_stream([
                    self.response("unusable", finish),
                ], "response_blocked")
                self.assertEqual(reason, finish)
                self.assertEqual(pieces, [])

    async def test_output_limit_retains_already_streamed_partial_text(self):
        pieces, reason = await self.run_stream([
            self.response("Початок. "), self.response(finish="MAX_TOKENS"),
        ], "output_limit")
        self.assertEqual(pieces, ["Початок. "])
        self.assertEqual(reason, "MAX_TOKENS")

    async def test_empty_stream_without_metadata(self):
        _, reason = await self.run_stream([], "empty_response")
        self.assertEqual(reason, "UNSPECIFIED")

    async def test_valid_response_passes_unchanged(self):
        pieces, _ = await self.run_stream([
            self.response("При"), self.response("віт!", "STOP"), types.GenerateContentResponse(),
        ])
        self.assertEqual(pieces, ["При", "віт!"])

    async def test_non_streaming_response_checks_metadata(self):
        provider = GeminiProvider("test-model", "test-key")
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(
            generate_content=AsyncMock(return_value=self.response(finish="SAFETY")),
        )))
        with patch.object(provider, "_client", return_value=client), patch.object(
            provider, "_release_client", new_callable=AsyncMock,
        ) as release:
            with self.assertRaises(ResponseError) as caught:
                await provider.chat([{"role": "user", "content": "Тест"}])
        self.assertEqual(caught.exception.kind, "response_blocked")
        release.assert_awaited_once_with(client)

    def test_unrecognized_metadata_does_not_expose_free_text(self):
        response = SimpleNamespace(prompt_feedback=SimpleNamespace(block_reason="PRIVATE_TEXT"))
        with self.assertRaises(ResponseError) as caught:
            GeminiProvider._response_reason(response)
        self.assertEqual(caught.exception.reason, "UNKNOWN")

    def test_failure_classification_uses_status_and_transport_not_raw_body(self):
        for code, kind in [(401, "access_denied"), (403, "access_denied"), (404, "invalid_request"), (503, "server_error")]:
            with self.subTest(code=code):
                exc = RuntimeError("PRIVATE_BODY")
                exc.code = code
                failure = classify_failure(exc)
                self.assertEqual(failure.kind, kind)
                self.assertEqual(failure.retryable, code == 503)
                self.assertNotIn("PRIVATE_BODY", repr(failure))
        self.assertEqual(classify_failure(httpx.ReadTimeout("test")).kind, "timeout")
        self.assertEqual(classify_failure(httpx.ConnectError("test")).kind, "connection_error")
