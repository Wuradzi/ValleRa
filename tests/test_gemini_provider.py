import asyncio
import unittest
from unittest.mock import AsyncMock, ANY, Mock, patch
from types import SimpleNamespace

from services.llm.providers import GeminiProvider


class GeminiPoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.reply = SimpleNamespace(text='Чотири', candidates=[], prompt_feedback=None)
        self.models = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(supported_actions=['generateContent'])),
            generate_content=AsyncMock(return_value=self.reply), generate_content_stream=AsyncMock())
        async def chunks():
            yield self.reply
        self.models.generate_content_stream.side_effect = lambda **kw: chunks()
        self.client = SimpleNamespace(aio=SimpleNamespace(models=self.models, aclose=AsyncMock()), close=Mock())
        self.factory_patch = patch('google.genai.Client', return_value=self.client)
        self.factory = self.factory_patch.start()
        self.provider = GeminiProvider('fixture', 'secret-fixture')
        self.messages = [{'role': 'user', 'content': 'Два плюс два?'}]

    async def asyncTearDown(self):
        await self.provider.close()
        self.factory_patch.stop()

    async def test_all_request_types_share_one_client_and_separate_http_timeouts(self):
        self.assertTrue((await self.provider.healthcheck()).available)
        self.assertEqual(await self.provider.chat(self.messages), 'Чотири')
        schema = {'type': 'object'}
        await self.provider.chat_structured(self.messages, schema)
        for _ in range(2):
            self.assertEqual([c async for c in self.provider.chat_stream(self.messages)], ['Чотири'])
        self.factory.assert_called_once()
        self.assertEqual(self.factory.call_args.kwargs['http_options'].async_client_args['limits'].keepalive_expiry, 60)
        self.assertEqual(self.models.get.call_args.kwargs['config'].http_options.timeout, 30000)
        for call in self.models.generate_content.call_args_list:
            self.assertEqual(call.kwargs['config'].http_options.timeout, 60000)
        self.assertIsNone(self.models.generate_content.call_args_list[0].kwargs['config'].response_json_schema)
        self.assertEqual(self.models.generate_content.call_args_list[1].kwargs['config'].response_json_schema, schema)
        self.client.aio.aclose.assert_not_awaited()
        await asyncio.gather(self.provider.close(), self.provider.close())
        self.client.aio.aclose.assert_awaited_once()
        self.client.close.assert_called_once()
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            await self.provider.chat(self.messages)
        self.factory.assert_called_once()

    async def test_healthcheck_cancellation_does_not_cancel_concurrent_chat(self):
        checking, chatting, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def get(**kwargs):
            checking.set()
            await asyncio.Event().wait()
        async def chat(**kwargs):
            chatting.set()
            await release.wait()
            return self.reply
        self.models.get.side_effect = get
        self.models.generate_content.side_effect = chat
        check = asyncio.create_task(self.provider.healthcheck())
        answer = asyncio.create_task(self.provider.chat(self.messages))
        await asyncio.wait_for(asyncio.gather(checking.wait(), chatting.wait()), 2)
        check.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await check
        self.client.aio.aclose.assert_not_awaited()
        self.assertFalse(answer.done())
        release.set()
        self.assertEqual(await asyncio.wait_for(answer, 2), 'Чотири')
        self.factory.assert_called_once()

    async def test_stream_cancel_closes_stream_not_pool_and_next_request_works(self):
        entered, finished = asyncio.Event(), asyncio.Event()
        async def stream():
            try:
                yield self.reply
                entered.set()
                await asyncio.Event().wait()
            finally:
                finished.set()
        self.models.generate_content_stream.side_effect = lambda **kw: stream()
        async def consume():
            return [c async for c in self.provider.chat_stream(self.messages)]
        task = asyncio.create_task(consume())
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(finished.is_set())
        self.client.aio.aclose.assert_not_awaited()
        self.assertEqual(await self.provider.chat(self.messages), 'Чотири')
        self.factory.assert_called_once()

    async def test_deadline_leaves_pool_usable(self):
        finished = asyncio.Event()
        async def get(**kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()
        self.models.get.side_effect = get
        with self.assertRaises(asyncio.TimeoutError):
            await self.provider._with_deadline(self.provider._check_async(.01), .01, cancel_on_timeout=True)
        await asyncio.wait_for(finished.wait(), 2)
        self.client.aio.aclose.assert_not_awaited()
        self.assertEqual(await self.provider.chat(self.messages), 'Чотири')

    async def test_shutdown_cancels_inflight_request_and_closes_both_transports(self):
        entered = asyncio.Event()
        async def chat(**kwargs):
            entered.set()
            await asyncio.Event().wait()
        self.models.generate_content.side_effect = chat
        task = asyncio.create_task(self.provider.chat(self.messages))
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.wait_for(self.provider.close(), 3)
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.client.aio.aclose.assert_awaited_once()
        self.client.close.assert_called_once()
        self.assertFalse(self.provider._active_requests)

    async def test_close_before_first_request_does_not_create_client(self):
        await self.provider.close()
        await self.provider.close()
        self.factory.assert_not_called()

    async def test_cancelled_close_caller_does_not_abandon_cleanup(self):
        await self.provider.chat(self.messages)
        entered, release = asyncio.Event(), asyncio.Event()
        async def close():
            entered.set()
            await release.wait()
        self.client.aio.aclose.side_effect = close
        task = asyncio.create_task(self.provider.close())
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        release.set()
        await asyncio.wait_for(self.provider.close(), 2)
        self.client.aio.aclose.assert_awaited_once()
        self.client.close.assert_called_once()


class GeminiProviderTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_deadline_returns_without_waiting_for_slow_cleanup(self):
        release = asyncio.Event()
        cleanup_started = asyncio.Event()
        finished = asyncio.Event()

        async def slow_operation():
            try:
                await release.wait()
            finally:
                cleanup_started.set()
                await release.wait()
                finished.set()

        try:
            # Do not impose a 100 ms scheduling budget on a loaded machine.
            # The deadline must return while work/cleanup is still blocked.
            async with asyncio.timeout(3):
                with self.assertRaises(asyncio.TimeoutError):
                    await GeminiProvider._with_deadline(
                        slow_operation(), 0.01, cancel_on_timeout=False,
                    )
                self.assertFalse(cleanup_started.is_set())
                self.assertFalse(finished.is_set())
        finally:
            release.set()
            await asyncio.wait_for(finished.wait(), 3)

    async def test_healthcheck_uses_at_least_30_seconds(self):
        provider = GeminiProvider("gemini-model", "api-key", timeout=10)

        with (
            patch(
                "services.llm.providers.GeminiProvider._check_async",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "services.llm.providers.GeminiProvider._with_deadline",
                new_callable=AsyncMock,
                side_effect=self._complete,
            ) as deadline,
        ):
            status = await provider.healthcheck()

        deadline.assert_awaited_once_with(
            ANY,
            30,
            cancel_on_timeout=True,
        )
        self.assertTrue(status.available)

    async def test_healthcheck_reports_30_second_timeout(self):
        provider = GeminiProvider("gemini-model", "api-key", timeout=10)

        with (
            patch(
                "services.llm.providers.GeminiProvider._check_async",
                new_callable=AsyncMock,
            ),
            patch(
                "services.llm.providers.GeminiProvider._with_deadline",
                new_callable=AsyncMock,
                side_effect=self._timeout,
            ),
        ):
            status = await provider.healthcheck()

        self.assertFalse(status.available)
        self.assertEqual(status.detail, "Тайм-аут перевірки Gemini: 30 с")

    async def test_chat_uses_at_least_60_seconds(self):
        provider = GeminiProvider("gemini-model", "api-key", timeout=10)

        with (
            patch(
                "services.llm.providers.GeminiProvider._chat_async",
                new_callable=AsyncMock,
                return_value="response",
            ),
            patch(
                "services.llm.providers.GeminiProvider._with_deadline",
                new_callable=AsyncMock,
                side_effect=self._complete,
            ) as deadline,
        ):
            response = await provider.chat([{"role": "user", "content": "Hi"}])

        deadline.assert_awaited_once_with(
            ANY,
            60,
            cancel_on_timeout=True,
        )
        self.assertEqual(response, "response")

    async def test_chat_reports_effective_timeout(self):
        provider = GeminiProvider("gemini-model", "api-key", timeout=75)

        with (
            patch(
                "services.llm.providers.GeminiProvider._chat_async",
                new_callable=AsyncMock,
            ),
            patch(
                "services.llm.providers.GeminiProvider._with_deadline",
                new_callable=AsyncMock,
                side_effect=self._timeout,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Gemini не відповів за 75 секунд",
            ):
                await provider.chat([{"role": "user", "content": "Hi"}])

    @staticmethod
    async def _timeout(awaitable, timeout, **kwargs):
        del timeout, kwargs
        close = getattr(awaitable, "close", None)
        if close is not None:
            close()
        raise asyncio.TimeoutError

    @staticmethod
    async def _complete(awaitable, timeout, **kwargs):
        del timeout, kwargs
        return await awaitable
