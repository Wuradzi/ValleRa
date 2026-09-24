import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from config import ProjectPaths, Settings, ConfigError, merge_config, validate_config
from core.command_actions import execute_intent
from core.command_intent import (
    CommandIntent, InvalidIntent, InterpretationUnavailable, can_interpret, parse_intent,
)
from core.command_router import CommandRouter
from core.confirmation import ConfirmationService
from core.models import CommandContext, SkillResult
from core.processor import CommandProcessor
from core.task_context import TaskContext
from services.llm.manager import LLMManager
from services.llm.errors import ResponseError


class IntentValidationTests(unittest.TestCase):
    def test_supported_contracts(self):
        examples = [
            ("find_files", {"query": "диплом", "extension": ".pdf"}),
            ("open_app", {"name": "Telegram"}),
            ("weather", {"city": "Луцьк", "period": "tomorrow"}),
            ("web_search", {"query": "історія Луцька"}),
            ("unsupported", {}),
        ]
        for tool, args in examples:
            with self.subTest(tool=tool):
                self.assertEqual(parse_intent(json.dumps({"tool": tool, "arguments": args})), CommandIntent(tool, args))

    def test_invalid_or_ambiguous_contracts_are_rejected(self):
        values = [
            [], {"tool": "shell", "arguments": {"command": "echo fixture"}},
            {"tool": "open_app", "arguments": {"name": "Telegram", "command": "anything"}},
            {"tool": "find_files", "arguments": {"query": "../outside", "extension": ""}},
            {"tool": "find_files", "arguments": {"query": "x", "extension": ".exe"}},
            {"tool": "open_app", "arguments": {"name": "C:\\tool.exe"}},
            {"tool": "open_app", "arguments": {"name": "cmd /c echo hi"}},
            {"tool": "open_app", "arguments": {"name": "Telegram; echo hi"}},
            {"tool": "open_app", "arguments": {"name": ""}},
            {"tool": "open_app", "arguments": {"name": "x\ny"}},
            {"tool": "open_app", "arguments": {"name": False}},
            {"tool": "weather", "arguments": {"city": "Луцьк", "period": "next_year"}},
            {"tool": "weather", "arguments": {"city": "", "period": "now"}},
            {"tool": "web_search", "arguments": {"query": "API_KEY=private-fixture"}},
            {"tool": "unsupported", "arguments": {}, "execute": True},
        ]
        for value in values:
            with self.subTest(value=value), self.assertRaises(InvalidIntent):
                parse_intent(json.dumps(value))
        for text in ('{"tool":"unsupported","tool":"open_app","arguments":{}}',
                     '```json\n{"tool":"unsupported","arguments":{}}\n```', "x" * 4001):
            with self.subTest(text=text[:40]), self.assertRaises(InvalidIntent):
                parse_intent(text)

    def test_sensitive_or_compound_requests_do_not_leave_local_boundary(self):
        for text in ("мій пароль abc", "запам'ятай API_KEY=fixture", "ключ відновлення fixture",
                     "Відкрий браузер і видали файл", "запусти хром а потім знайди документ",
                     "x\ny", "x" * 601):
            with self.subTest(text=text):
                self.assertFalse(can_interpret(text))
        self.assertTrue(can_interpret("Допоможи відшукати документ про диплом"))

    def test_feature_defaults_and_strict_configuration(self):
        config = merge_config({})
        self.assertTrue(config["commands"]["llm_interpretation"])
        validate_config(config)
        for custom in ({"llm_interpretation": "false"}, {"interpretation_timeout_seconds": 0},
                       {"interpretation_timeout_seconds": True}, {"interpretation_timeout_seconds": 31}):
            with self.subTest(custom=custom), self.assertRaises(ConfigError):
                validate_config(merge_config({"commands": custom}))
        self.assertFalse(merge_config({"commands": {"llm_interpretation": False}})["commands"]["llm_interpretation"])


class InterpreterRequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        settings = Settings(ProjectPaths.from_root(Path(temp.name)), llm_models=dict(merge_config({})["llm"]["models"]))
        with patch.dict("os.environ", {}, clear=True):
            self.manager = LLMManager(settings)
        self.provider = SimpleNamespace(chat=AsyncMock(return_value='{"tool":"open_app","arguments":{"name":"Telegram"}}'))
        self.reserve = SimpleNamespace(chat=AsyncMock())
        self.manager.providers = {"gemini": self.provider, "reserve": self.reserve}
        self.manager.available_order = ["gemini", "reserve"]
        self.manager.active_name = "gemini"
        self.manager.history = Mock()

    async def test_stateless_request_uses_only_current_command_and_contract(self):
        intent = await self.manager.interpret_command("Будь ласка увімкни Telegram")
        self.assertEqual(intent.tool, "open_app")
        messages = self.provider.chat.await_args.args[0]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[1], {"role": "user", "content": "Будь ласка увімкни Telegram"})
        self.manager.history.load.assert_not_called()
        self.manager.history.save.assert_not_called()
        self.reserve.chat.assert_not_awaited()

    async def test_sensitive_request_makes_no_api_call(self):
        self.assertIsNone(await self.manager.interpret_command("мій пароль fixture"))
        self.provider.chat.assert_not_awaited()

    async def test_invalid_output_has_no_retry_or_history_write(self):
        self.provider.chat.return_value = "Готово, я все відкрив!"
        self.assertIsNone(await self.manager.interpret_command("увімкни телеграм"))
        self.provider.chat.assert_awaited_once()
        self.reserve.chat.assert_not_awaited()
        self.manager.history.save.assert_not_called()

    async def test_timeout_has_no_fallback_or_error_body(self):
        self.provider.chat.side_effect = TimeoutError("PRIVATE_RESPONSE_FIXTURE")
        with self.assertRaises(InterpretationUnavailable) as error:
            await self.manager.interpret_command("увімкни телеграм")
        self.assertNotIn("PRIVATE_RESPONSE_FIXTURE", str(error.exception))
        self.provider.chat.assert_awaited_once()
        self.reserve.chat.assert_not_awaited()

    async def test_cancellation_propagates(self):
        self.provider.chat.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.manager.interpret_command("увімкни телеграм")
        self.reserve.chat.assert_not_awaited()

    async def test_configured_deadline_cancels_pending_request(self):
        stopped = asyncio.Event()

        async def slow(messages):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        self.provider.chat.side_effect = slow
        self.manager.settings.command_interpretation_timeout_seconds = 0.01
        with self.assertRaises(InterpretationUnavailable):
            await self.manager.interpret_command("увімкни телеграм")
        self.assertTrue(stopped.is_set())
        self.reserve.chat.assert_not_awaited()

    async def test_quota_cooldown_is_shared_with_chat_and_no_immediate_retry(self):
        error = RuntimeError("PRIVATE_RESPONSE_FIXTURE")
        error.status_code = 429
        self.provider.chat.side_effect = error
        with self.assertRaises(InterpretationUnavailable):
            await self.manager.interpret_command("увімкни телеграм")
        self.assertTrue(self.manager._cooling_down("gemini"))
        self.reserve.chat.assert_not_awaited()
        self.manager.available_order = ["gemini"]
        with self.assertRaises(InterpretationUnavailable):
            await self.manager.interpret_command("увімкни телеграм")
        self.provider.chat.assert_awaited_once()

    async def test_blocked_or_empty_response_is_not_retried(self):
        for kind in ("response_blocked", "empty_response"):
            self.provider.chat.reset_mock()
            self.provider.chat.side_effect = ResponseError(kind)
            with self.assertRaises(InterpretationUnavailable):
                await self.manager.interpret_command("увімкни телеграм")
            self.provider.chat.assert_awaited_once()
            self.reserve.chat.assert_not_awaited()


class IntentExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.apps = SimpleNamespace(open_default_browser=Mock(return_value=True), find=Mock(return_value=[]))
        self.confirm = AsyncMock(return_value=True)
        self.context = CommandContext(SimpleNamespace(), {"apps": self.apps, "enabled_skills": {"apps", "web", "files"},
                                                        "tasks": TaskContext()}, self.confirm)

    async def test_proposal_is_confirmed_before_execution(self):
        async def confirm(text):
            self.apps.open_default_browser.assert_not_called()
            self.assertIn("браузер", text)
            return True
        self.confirm.side_effect = confirm
        result = await execute_intent(CommandIntent("open_app", {"name": "браузер"}), self.context)
        self.assertTrue(result.data["accepted"])
        self.apps.open_default_browser.assert_called_once()

    async def test_denial_never_executes(self):
        self.confirm.return_value = False
        result = await execute_intent(CommandIntent("open_app", {"name": "браузер"}), self.context)
        self.assertEqual(result.data["command_type"], "interpretation_cancelled")
        self.apps.open_default_browser.assert_not_called()

    async def test_cancellation_while_speaking_clears_confirmation_and_never_executes(self):
        started = asyncio.Event()

        async def wait_for_speech():
            started.set()
            await asyncio.Event().wait()

        confirmation = ConfirmationService(AsyncMock(), wait_for_speech=wait_for_speech)
        self.context.confirm = confirmation.ask
        task = asyncio.create_task(execute_intent(CommandIntent("open_app", {"name": "браузер"}), self.context))
        await asyncio.wait_for(started.wait(), 1)
        confirmation.submit("так")
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(confirmation.awaiting)
        self.assertTrue(confirmation._responses.empty())
        self.assertFalse(confirmation.submit("так"))
        self.apps.open_default_browser.assert_not_called()

    async def test_failed_confirmation_speech_clears_state(self):
        confirmation = ConfirmationService(AsyncMock(side_effect=RuntimeError("speech fixture")))
        with self.assertRaises(RuntimeError):
            await confirmation.ask("відкриття браузера")
        self.assertFalse(confirmation.awaiting)
        self.assertFalse(confirmation.submit("так"))

    async def test_disabled_skill_never_executes(self):
        self.context.services["enabled_skills"] = set()
        result = await execute_intent(CommandIntent("open_app", {"name": "браузер"}), self.context)
        self.assertEqual(result.data["command_type"], "interpretation_unavailable")
        self.confirm.assert_not_awaited()
        self.apps.open_default_browser.assert_not_called()

    async def test_forged_intent_is_revalidated_at_executor_boundary(self):
        for intent in (CommandIntent("shell", {"command": "test"}),
                       CommandIntent("open_app", {"name": "cmd /c fixture"}),
                       CommandIntent("open_app", {"name": "браузер", "code": "fixture"})):
            with self.subTest(intent=intent):
                result = await execute_intent(intent, self.context)
                self.assertEqual(result.data["command_type"], "interpretation_invalid")
        self.confirm.assert_not_awaited()
        self.apps.open_default_browser.assert_not_called()

    async def test_web_and_weather_call_only_explicit_helpers(self):
        with patch("skills.web.skill.search_web", new_callable=AsyncMock, return_value=SkillResult(True, "sources")) as search:
            result = await execute_intent(CommandIntent("web_search", {"query": "історія Луцька"}), self.context)
            search.assert_awaited_once_with("історія Луцька", self.context.services)
            self.assertEqual(result.response, "sources")
        with patch("skills.web.skill.get_weather", new_callable=AsyncMock, return_value=SkillResult(True, "forecast")) as weather:
            await execute_intent(CommandIntent("weather", {"city": "Луцьк", "period": "tomorrow"}), self.context)
            weather.assert_awaited_once_with("Луцьк", "tomorrow", self.context.services)


class ProcessorInterpretationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.router = SimpleNamespace(route=AsyncMock(return_value=SkillResult(False)))
        self.llm = SimpleNamespace(chat=AsyncMock(return_value="chat"), active_name="fixture",
                                   interpret_command=AsyncMock(return_value=CommandIntent("unsupported", {})))
        self.settings = SimpleNamespace(command_interpretation_enabled=True)
        self.services = {"state": {"mode": "chat"}, "tasks": TaskContext(),
                         "memory": SimpleNamespace(relevant=Mock(return_value=[]))}
        self.processor = CommandProcessor(self.settings, self.router, self.llm,
                                          SimpleNamespace(stop=AsyncMock()), Mock(), self.services)
        self.confirm = AsyncMock(return_value=True)

    async def test_only_unknown_explicit_command_is_interpreted(self):
        result = await self.processor.process("Команда: допоможи відшукати документ про диплом", self.confirm)
        self.assertEqual(result.data["command_type"], "interpretation_unsupported")
        self.llm.interpret_command.assert_awaited_once_with("допоможи відшукати документ про диплом")
        self.llm.chat.assert_not_awaited()

    async def test_known_command_keeps_fast_local_path(self):
        self.router.route.return_value = SkillResult(True, "local")
        result = await self.processor.process("Команда: відкрий браузер", self.confirm)
        self.assertEqual(result.response, "local")
        self.llm.interpret_command.assert_not_awaited()
        self.llm.chat.assert_not_awaited()

    async def test_plain_conversation_never_interprets_or_executes(self):
        await self.processor.process("допоможи відкрити браузер", self.confirm)
        self.llm.chat.assert_awaited_once()
        self.llm.interpret_command.assert_not_awaited()
        self.router.route.assert_not_awaited()

    async def test_disabled_feature_keeps_unknown_command_local(self):
        self.settings.command_interpretation_enabled = False
        await self.processor.process("Команда: невідомий намір", self.confirm)
        self.llm.interpret_command.assert_not_awaited()

    async def test_pentest_mode_and_sensitive_requests_never_use_interpreter(self):
        await self.processor.process("Команда: секрет fixture", self.confirm)
        self.services["state"]["mode"] = "pentest"
        await self.processor.process("Команда: невідомий намір", self.confirm)
        self.llm.interpret_command.assert_not_awaited()

    async def test_compound_command_does_not_execute_first_part(self):
        result = await self.processor.process("Команда: відкрий браузер і знайди документ", self.confirm)
        self.assertEqual(result.data["command_type"], "compound_command")
        self.router.route.assert_not_awaited()
        self.llm.interpret_command.assert_not_awaited()

    async def test_invalid_interpretation_never_confirms(self):
        self.llm.interpret_command.return_value = None
        await self.processor.process("Команда: невідомий намір", self.confirm)
        self.confirm.assert_not_awaited()

    async def test_failed_local_skill_does_not_fall_through_to_model(self):
        skill = SimpleNamespace(name="fixture", platforms=["all"], triggers=["відкрий"],
                                can_handle=AsyncMock(return_value=True), handle=AsyncMock(side_effect=RuntimeError("fixture")))
        self.processor.router = CommandRouter([skill])
        result = await self.processor.process("Команда: відкрий браузер", self.confirm)
        self.assertEqual(result.data["command_type"], "skill_error")
        self.llm.interpret_command.assert_not_awaited()
