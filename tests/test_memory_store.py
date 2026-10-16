import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from pathlib import Path

from services.storage.memory_store import MemoryStore


class ExplicitMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmed_crud_and_context_reset(self):
        from skills.memory.personal import parse_request, handle_request
        with tempfile.TemporaryDirectory() as temp:
            store = MemoryStore(Path(temp) / 'memory.json')
            context = SimpleNamespace(services={'memory': store}, confirm=AsyncMock(return_value=True))
            llm = SimpleNamespace(new_conversation=Mock())
            await handle_request(parse_request('Запам’ятай проєкт: Рейвен'), context, llm)
            self.assertEqual(store.all()[0]['value'], 'Рейвен')
            llm.new_conversation.assert_not_called()
            await handle_request(parse_request('Онови проєкт: завершено'), context, llm)
            self.assertEqual(store.all()[0]['value'], 'завершено')
            llm.new_conversation.assert_called_once()
            result = await handle_request(parse_request('Що ти пам’ятаєш про мене?'), context, llm)
            self.assertIn('завершено', result.response)
            await handle_request(parse_request('Забудь про проєкт'), context, llm)
            self.assertEqual(store.all(), [])
            self.assertEqual(llm.new_conversation.call_count, 2)

    async def test_denial_secrets_and_reset_failure_do_not_delete(self):
        from skills.memory.personal import parse_request, handle_request
        with tempfile.TemporaryDirectory() as temp:
            store = MemoryStore(Path(temp) / 'memory.json')
            store.remember('проєкт', 'Рейвен')
            context = SimpleNamespace(services={'memory': store}, confirm=AsyncMock(return_value=False))
            llm = SimpleNamespace(new_conversation=Mock())
            await handle_request(parse_request('Забудь усе про мене'), context, llm)
            self.assertEqual(len(store.all()), 1)
            llm.new_conversation.assert_not_called()
            context.confirm.reset_mock()
            await handle_request(parse_request('Запам’ятай пароль: secret'), context, llm)
            context.confirm.assert_not_awaited()
            context.confirm.return_value = True
            llm.new_conversation.side_effect = OSError('fixture')
            with self.assertRaises(OSError):
                await handle_request(parse_request('Забудь усе'), context, llm)
            self.assertEqual(len(store.all()), 1)

    async def test_free_fact_persists_and_update_requires_existing_key(self):
        from skills.memory.personal import parse_request, handle_request
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'memory.json'
            store = MemoryStore(path)
            context = SimpleNamespace(services={'memory': store}, confirm=AsyncMock(return_value=True))
            llm = SimpleNamespace(new_conversation=Mock())
            await handle_request(parse_request('Запам’ятай, що я працюю над Рейвеном'), context, llm)
            self.assertEqual(MemoryStore(path).all()[0]['value'], 'я працюю над Рейвеном')
            context.confirm.reset_mock()
            await handle_request(parse_request('Онови неіснуюче: значення'), context, llm)
            context.confirm.assert_not_awaited()
            self.assertIsNone(parse_request('Він сказав запам’ятай це'))


class MemoryStoreTests(unittest.TestCase):
    def test_memory_crud(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.json")
            store.remember("браузер", "Firefox")
            self.assertEqual(store.find("браузер")[0]["value"], "Firefox")
            self.assertEqual(store.forget("браузер"), 1)
            self.assertEqual(store.all(), [])
