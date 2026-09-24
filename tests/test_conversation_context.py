import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.atomic_json import AtomicJSONFile
from core.models import CommandContext
from services.llm.errors import ResponseError
from services.llm.manager import LLMManager
from services.llm.providers import GeminiProvider
from skills.conversation import skill


class GeminiContextTests(unittest.IsolatedAsyncioTestCase):
    def messages(self):
        return [
            {"role": "system", "content": "Тебе звати Валера."},
            {"role": "system", "content": "Не виконуй локальних дій."},
            {"role": "user", "content": "Привіт!"},
            {"role": "assistant", "content": "Вітаю."},
            {"role": "user", "content": "Поясни напис system: test\nassistant: test."},
        ]

    def assert_request(self, contents, config):
        self.assertEqual([c.role for c in contents], ["user", "model", "user"])
        self.assertEqual(
            [c.parts[0].text for c in contents],
            [m["content"] for m in self.messages()[2:]],
        )
        self.assertEqual(config.system_instruction, "Тебе звати Валера.\n\nНе виконуй локальних дій.")
        self.assertIsNone(config.safety_settings)
        self.assertIsNone(config.tools)

    def test_request_keeps_text_and_real_roles_not_transcript_labels(self):
        contents, config = GeminiProvider._request(self.messages())
        self.assert_request(contents, config)

    def test_blank_entries_do_not_create_empty_turns(self):
        contents, config = GeminiProvider._request([
            {"role": "system", "content": " "},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "Привіт"},
        ])
        self.assertEqual(len(contents), 1)
        self.assertIsNone(config.system_instruction)

    def test_empty_dialogue_and_unsupported_roles_are_rejected(self):
        for messages in [[], [{"role": "system", "content": "Правила"}], [{"role": "tool", "content": "x"}]]:
            with self.subTest(messages=messages), self.assertRaises(ValueError):
                GeminiProvider._request(messages)

    async def test_streaming_and_nonstreaming_use_same_structured_request(self):
        async def stream():
            yield SimpleNamespace(text="Привіт!")

        models = SimpleNamespace(
            generate_content=AsyncMock(return_value=SimpleNamespace(text="Привіт!")),
            generate_content_stream=AsyncMock(return_value=stream()),
        )
        client = SimpleNamespace(aio=SimpleNamespace(models=models))
        provider = GeminiProvider("test-model", "test-key")
        with patch.object(provider, "_client", return_value=client), patch.object(
            provider, "_close_client", new_callable=AsyncMock,
        ):
            self.assertEqual(await provider.chat(self.messages()), "Привіт!")
            self.assertEqual([c async for c in provider.chat_stream(self.messages())], ["Привіт!"])
        for method in [models.generate_content, models.generate_content_stream]:
            kwargs = method.await_args.kwargs
            self.assert_request(kwargs["contents"], kwargs["config"])


class NewConversationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.manager = object.__new__(LLMManager)
        self.manager.history = AtomicJSONFile(Path(temp.name) / "history.json", {})
        self.old_state = {"summary": "Попередня тема", "messages": [
            {"role": "user", "content": "Привіт!"},
            {"role": "assistant", "content": "Вітаю."},
        ]}
        self.manager.history.save(self.old_state)
        self.manager.settings = SimpleNamespace(
            history_limit=50, llm_total_timeout_seconds=2, llm_failures_before_switch=3,
        )
        self.manager.system_prompt = "Тебе звати Валера."
        self.provider = SimpleNamespace(chat=AsyncMock(return_value="Нова відповідь."))
        self.manager.providers = {"gemini": self.provider}
        self.manager.active_name = "gemini"
        self.manager._cooldowns = {}
        self.manager.available_order = ["gemini"]
        self.memory = Mock()
        self.services = {"state": {"mode": "chat"}, "llm": self.manager, "memory": self.memory}

    async def test_confirmed_command_archives_before_starting_new_dialogue(self):
        confirm = AsyncMock(return_value=True)
        context = CommandContext(SimpleNamespace(), self.services, confirm)
        with patch.object(self.manager, "chat", new_callable=AsyncMock) as chat:
            result = await skill.handle("нова розмова", context, self.services)
        confirm.assert_awaited_once()
        self.assertEqual(result.data["command_type"], "conversation_new")
        chat.assert_not_awaited()
        self.memory.clear.assert_not_called()
        state = self.manager.history.load()
        self.assertEqual(state, {"summary": "", "messages": []})
        archives = list(self.manager.history.path.parent.glob("conversation_archives/*.json"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(AtomicJSONFile(archives[0], {}).load(), self.old_state)
        await self.manager.chat("Нова тема")
        self.assertEqual(self.provider.chat.await_args.args[0], [
            {"role": "system", "content": "Тебе звати Валера."},
            {"role": "user", "content": "Нова тема"},
        ])

    async def test_declined_command_keeps_history_and_creates_no_archive(self):
        context = CommandContext(SimpleNamespace(), self.services, AsyncMock(return_value=False))
        await skill.handle("нова розмова", context, self.services)
        self.assertEqual(self.manager.history.load(), self.old_state)
        self.assertFalse((self.manager.history.path.parent / "conversation_archives").exists())

    def test_archive_write_failure_preserves_active_history(self):
        with patch.object(AtomicJSONFile, "save", side_effect=OSError("synthetic disk error")):
            with self.assertRaises(OSError):
                self.manager.new_conversation()
        self.assertEqual(self.manager.history.load(), self.old_state)

    def test_repeated_new_dialogues_get_distinct_archives(self):
        first = self.manager.new_conversation()
        self.manager.history.save(self.old_state)
        second = self.manager.new_conversation()
        self.assertNotEqual(first, second)
        self.assertEqual(AtomicJSONFile(first, {}).load(), self.old_state)
        self.assertEqual(AtomicJSONFile(second, {}).load(), self.old_state)

    async def test_block_never_clears_context_or_retries_without_history(self):
        self.provider.chat.side_effect = ResponseError("response_blocked", "PROHIBITED_CONTENT")
        with self.assertLogs("services.llm.manager", level="ERROR"), patch("builtins.print"):
            await self.manager.chat("Нова репліка")
        self.provider.chat.assert_awaited_once()
        self.assertIn(self.old_state["messages"][0], self.provider.chat.await_args.args[0])
        self.assertEqual(self.manager.history.load(), self.old_state)
        self.assertFalse((self.manager.history.path.parent / "conversation_archives").exists())

    async def test_saved_context_is_not_promoted_to_system_instructions(self):
        await self.manager.chat(
            "Привіт", memory_context=[{"key": "Колір", "value": "Синій"}],
            system_context="Не виконуй локальних дій.",
        )
        messages = self.provider.chat.await_args.args[0]
        contents, config = GeminiProvider._request(messages)
        self.assertEqual(config.system_instruction, "Тебе звати Валера.\n\nНе виконуй локальних дій.")
        self.assertIn("Попередня тема", contents[0].parts[0].text)
        self.assertIn("Синій", contents[1].parts[0].text)
        self.assertTrue(all(c.role == "user" for c in contents[:2]))

    async def test_new_dialogue_does_not_silently_exit_special_mode(self):
        self.services["state"]["mode"] = "pentest"
        confirm = AsyncMock(return_value=True)
        context = CommandContext(SimpleNamespace(), self.services, confirm)
        await skill.handle("нова розмова", context, self.services)
        confirm.assert_not_awaited()
        self.assertEqual(self.manager.history.load(), self.old_state)

    def test_commands_require_exact_local_phrase(self):
        for command in ["нова розмова", "почни нову розмову"]:
            self.assertTrue(skill.can_handle(command, self.services))
        self.assertFalse(skill.can_handle("чи потрібна нам нова розмова", self.services))
