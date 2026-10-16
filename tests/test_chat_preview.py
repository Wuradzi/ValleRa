import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from services.llm.manager import LLMManager


class ChatPreviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manager = object.__new__(LLMManager)
        self.manager.settings = SimpleNamespace(history_limit=12, llm_total_timeout_seconds=2)
        self.manager.history = Mock()
        self.manager.history.load.return_value = {'messages': []}
        self.manager.system_prompt = 'Fixture'
        self.manager._append_history = Mock()
        self.manager.available_order = ['fixture']
        self.manager.active_name = 'fixture'
        self.manager._cooldowns = {}
        self.spoken = []

    async def emit(self, text):
        self.spoken.append(text)

    async def run_stream(self, stream):
        self.manager.providers = {'fixture': SimpleNamespace(chat_stream=stream)}
        return await self.manager.converse('Привіт', on_chunk=self.emit)

    async def test_chat_sentence_arrives_before_generation_finishes_once(self):
        async def stream(messages):
            yield 'CH'
            yield 'AT\nПерше речення. '
            self.assertEqual(self.spoken, ['Перше речення. '])
            yield 'Друге речення.'
        result = await self.run_stream(stream)
        self.assertEqual(result.kind, 'chat')
        self.assertEqual(self.spoken, ['Перше речення. ', 'Друге речення.'])
        self.manager._append_history.assert_called_once_with('Привіт', result.response)

    async def test_plain_clarify_action_stay_buffered(self):
        for output in ('Звичайний текст. Далі.', 'CLARIFY\nЯку програму? ',
                       'ACTION\n{"tool":"open_app","arguments":{"name":"Telegram"}}'):
            self.spoken.clear()
            async def stream(messages):
                for char in output:
                    yield char
                    self.assertEqual(self.spoken, [])
            result = await self.run_stream(stream)
            if result.kind == 'action':
                self.assertEqual(self.spoken, [])
            else:
                self.assertEqual(self.spoken, [result.response])

    async def test_late_control_after_preview_never_becomes_action(self):
        for tail in ('ACTION\n{"tool":"open_app","arguments":{"name":"Telegram"}}',
                     'PROPOSAL\n{}', '"tool": "open_app"', 'Я вже відкрив Telegram.'):
            self.spoken.clear()
            async def stream(messages):
                yield 'CHAT\nВітаю. '
                for char in tail:
                    yield char
            result = await self.run_stream(stream)
            self.assertEqual(result.kind, 'unavailable')
            self.assertEqual(self.spoken, ['Вітаю. '])
        self.manager._append_history.assert_not_called()

    async def test_network_failure_keeps_partial_out_of_history(self):
        async def stream(messages):
            yield 'CHAT\nВітаю. '
            raise OSError('fixture')
        result = await self.run_stream(stream)
        self.assertEqual(result.kind, 'unavailable')
        self.assertEqual(self.spoken, ['Вітаю. '])
        self.manager._append_history.assert_not_called()

    async def test_cancel_closes_stream_without_late_speech(self):
        entered, closed = asyncio.Event(), asyncio.Event()
        async def stream(messages):
            try:
                yield 'CHAT\nВітаю. '
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    pass  # A misbehaving provider must not revive the cancelled turn.
                yield 'Застаріле речення.'
            finally:
                closed.set()
        task = asyncio.create_task(self.run_stream(stream))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(closed.is_set())
        self.assertEqual(self.spoken, ['Вітаю. '])
        self.manager._append_history.assert_not_called()

    async def test_processor_streams_normal_and_groq_without_replay(self):
        from core.models import TurnEnvelope, SkillResult
        from core.processor import CommandProcessor
        from core.task_context import TaskContext
        for cloud, fail in ((False, False), (True, False), (False, True), (True, True)):
            self.manager._append_history.reset_mock()
            speaker = SimpleNamespace(say=AsyncMock(), stop=AsyncMock())
            services = {'state': {'mode': 'chat'}, 'enabled_skills': set(),
                        'memory': SimpleNamespace(relevant=Mock(return_value=[])),
                        'tasks': TaskContext()}
            processor = CommandProcessor(SimpleNamespace(natural_actions_enabled=True),
                SimpleNamespace(route=AsyncMock(return_value=SkillResult(False))), self.manager,
                speaker, SimpleNamespace(record=Mock()), services)
            async def stream(messages):
                yield 'CHAT\nВітаю. '
                speaker.say.assert_awaited_once_with('Вітаю.')
                if fail:
                    raise OSError('fixture')
                yield 'Радий розмові.'
            self.manager.providers = {'fixture': SimpleNamespace(chat_stream=stream)}
            self.manager.rewrite_last_answer = Mock()
            turn = TurnEnvelope(turn_id='test', session_id='test', source='voice' if cloud else 'text',
                text='Привіт', transcript='Привіт', stt_engine='groq' if cloud else 'text',
                confidence=0 if cloud else 1, confidence_available=not cloud, action_eligible=not cloud)
            confirmation = AsyncMock()
            result = await processor.process(turn, confirmation)
            self.assertTrue(result.data['response_spoken'])
            self.assertEqual(speaker.say.await_count, 2)
            confirmation.assert_not_awaited()
            if fail:
                self.manager._append_history.assert_not_called()
                self.manager.rewrite_last_answer.assert_not_called()
