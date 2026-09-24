import asyncio
import contextlib
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

from core.confirmation import ConfirmationService
from core.app import ValleRaApp
from core.models import RecognitionResult, SkillResult
from core.processor import CommandProcessor
from core.voice_text import repair_voice_text
from services.apps.controller import ApplicationController
from skills.apps import skill as apps_skill
from skills.conversation import skill as conversation_skill
from skills.help import skill as help_skill
from skills.web import skill as web_skill


class ConfirmationServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_confirmation_grammar_uses_only_model_vocabulary(self):
        self.assertNotIn("[unk]", ConfirmationService.GRAMMAR)

    async def test_vosk_confirmation_variant_is_accepted(self):
        speak = AsyncMock()
        confirmation = ConfirmationService(speak, timeout_seconds=1)

        task = asyncio.create_task(confirmation.ask("тест"))
        await confirmation.wait_until_requested()
        self.assertTrue(confirmation.submit("підтверджує"))

        self.assertTrue(await task)
        self.assertFalse(confirmation.awaiting)

    async def test_unrelated_input_does_not_finish_confirmation(self):
        speak = AsyncMock()
        confirmation = ConfirmationService(speak, timeout_seconds=1)

        task = asyncio.create_task(confirmation.ask("тест"))
        await confirmation.wait_until_requested()
        confirmation.submit("відкрий телеграм")
        await asyncio.sleep(0)
        confirmation.submit("ні")

        self.assertFalse(await task)
        self.assertGreaterEqual(speak.await_count, 3)


class CommandProcessorModeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.router = _Router()
        self.llm = _LLM()
        self.speaker = SimpleNamespace(stop=AsyncMock())
        self.metrics = SimpleNamespace(record=Mock())
        self.services = {
            "state": {"mode": "chat"},
            "memory": SimpleNamespace(relevant=Mock(return_value=[])),
        }
        self.processor = CommandProcessor(
            SimpleNamespace(),
            self.router,
            self.llm,
            self.speaker,
            self.metrics,
            self.services,
        )

    async def test_regular_phrase_reaches_llm(self):
        result = await self.processor.process(
            "виграв",
            AsyncMock(return_value=True),
            "voice",
        )

        self.assertEqual(result.response, "chat-response")
        self.llm.chat.assert_awaited_once()

    async def test_original_case_is_preserved_for_llm_and_local_payload(self):
        await self.processor.process(
            "Поясни APIKey ABC-Def",
            AsyncMock(return_value=True),
            "text",
        )
        self.assertEqual(self.llm.chat.await_args.args[0], "Поясни APIKey ABC-Def")

        await self.processor.process(
            "Команда: запам'ятай пароль від GitHub це AbC-123-XyZ",
            AsyncMock(return_value=True),
            "text",
        )
        self.assertEqual(
            self.router.contexts[-1].raw_text,
            "запам'ятай пароль від GitHub це AbC-123-XyZ",
        )
        self.assertEqual(
            self.router.commands[-1],
            "запам'ятай пароль від github це abc-123-xyz",
        )

    async def test_unknown_prefixed_command_never_reaches_llm(self):
        result = await self.processor.process(
            "Команда: виграв",
            AsyncMock(return_value=True),
            "voice",
        )

        self.assertIn("не розпізнано", result.response)
        self.llm.chat.assert_not_awaited()

    async def test_empty_command_prefix_never_reaches_llm(self):
        result = await self.processor.process(
            "Команда:",
            AsyncMock(return_value=True),
            "voice",
        )

        self.assertIn("назвіть локальну дію", result.response)
        self.llm.chat.assert_not_awaited()

    async def test_chat_text_without_prefix_goes_only_to_llm(self):
        self.services["state"]["mode"] = "chat"

        result = await self.processor.process(
            "відкрий телеграм",
            AsyncMock(return_value=True),
            "voice",
        )

        self.assertEqual(result.response, "chat-response")
        self.llm.chat.assert_awaited_once()
        self.assertEqual(self.router.commands, [])

    async def test_addressing_valera_does_not_create_command_permission(self):
        result = await self.processor.process(
            "Валера, команда відкрий браузер",
            AsyncMock(return_value=True),
            "voice",
        )

        self.assertEqual(result.response, "chat-response")
        self.llm.chat.assert_awaited_once()
        self.assertEqual(self.router.commands, [])

    async def test_prefixed_chat_command_uses_router_and_not_llm(self):
        self.services["state"]["mode"] = "chat"
        self.router.responses["відкрий телеграм"] = SkillResult(
            True,
            "Відкриваю Telegram.",
        )

        result = await self.processor.process(
            "Команда: відкрий телеграм",
            AsyncMock(return_value=True),
            "voice",
        )

        self.assertEqual(result.response, "Відкриваю Telegram.")
        self.llm.chat.assert_not_awaited()
        self.assertEqual(self.router.commands, ["відкрий телеграм"])

    async def test_pentest_mode_routes_only_to_pentest_skill(self):
        self.services["state"]["mode"] = "pentest"

        await self.processor.process(
            "відкрий телеграм",
            AsyncMock(return_value=True),
            "voice",
        )

        self.assertEqual(self.router.allowed_skills, [{"pentest"}])

    async def test_local_command_drops_trailing_question_mark(self):
        self.services["state"]["mode"] = "pentest"

        await self.processor.process(
            "довго чекати?",
            AsyncMock(return_value=True),
            "text",
        )

        self.assertEqual(self.router.commands, ["довго чекати"])

    async def test_prefixed_command_in_pentest_can_use_other_skills(self):
        self.services["state"]["mode"] = "pentest"

        await self.processor.process(
            "Команда: відкрий хром",
            AsyncMock(return_value=True),
            "voice",
        )

        self.assertEqual(self.router.commands, ["відкрий хром"])
        self.assertEqual(self.router.allowed_skills, [None])

    async def test_llm_cannot_claim_that_local_action_was_executed(self):
        self.llm.chat.return_value = "Відкриваю Telegram."

        result = await self.processor.process(
            "так",
            AsyncMock(return_value=True),
            "voice",
        )

        self.assertIn("не виконую локальні дії", result.response)

    async def test_follow_up_reports_actual_local_open_action(self):
        self.router.responses["відкрий браузер"] = SkillResult(
            True,
            "Відкриваю браузер за замовчуванням.",
            {"command_type": "open_browser"},
        )

        await self.processor.process(
            "Команда: відкрий браузер",
            AsyncMock(return_value=True),
            "voice",
        )
        result = await self.processor.process(
            "ти нічого не відкрив",
            AsyncMock(return_value=True),
            "voice",
        )

        self.assertIn("Відкриваю браузер", result.response)
        self.llm.chat.assert_not_awaited()


class AppCommandLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_conflicting_command_prefix_requests_repeat_without_routing(self):
        app = _voice_loop_app()
        app.listener.listen_once.return_value = RecognitionResult("відкрий браузер", .9, "conflict")

        async def say(text):
            app.running = False

        app.speaker.say = AsyncMock(side_effect=say)
        await asyncio.wait_for(app._voice_loop(), 1)
        self.assertTrue(app.command_queue.empty())
        self.assertIn("Повторіть", app.speaker.say.await_args.args[0])

    async def test_voice_phrase_keeps_chat_as_default_mode(self):
        app = _command_loop_app(confidence_threshold=0.55)
        app.processor.process = AsyncMock(
            return_value=SkillResult(True, "Виконано.")
        )
        task = asyncio.create_task(app._command_loop())
        try:
            await app.command_queue.put(("котра година", "voice", 0.9))
            await asyncio.wait_for(app.command_queue.join(), 1)
        finally:
            app.running = False
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        self.assertEqual(app.services["state"]["mode"], "chat")
        app.processor.process.assert_awaited_once()

    async def test_low_confidence_local_command_is_not_executed(self):
        app = _command_loop_app(confidence_threshold=0.55)
        task = asyncio.create_task(app._command_loop())
        try:
            await app.command_queue.put(("команда виграв", "voice", 0.3))
            await asyncio.wait_for(app.command_queue.join(), 1)
        finally:
            app.running = False
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        app.processor.process.assert_not_awaited()
        app.confirmation.ask.assert_not_awaited()
        self.assertEqual(app.services["state"]["mode"], "chat")

    async def test_low_confidence_conversation_asks_to_repeat_without_llm(self):
        app = _command_loop_app(confidence_threshold=0.55)
        app.processor.process = AsyncMock(
            return_value=SkillResult(True, "Почув вас.")
        )
        task = asyncio.create_task(app._command_loop())
        try:
            await app.command_queue.put(("ти пам'ятаєш люта", "voice", 0.37))
            await asyncio.wait_for(app.command_queue.join(), 1)
        finally:
            app.running = False
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        app.processor.process.assert_not_awaited()
        self.assertIn("Повторіть", app.speaker.say.await_args.args[0])
        self.assertTrue(app.command_idle.is_set())

    async def test_text_is_not_filtered_by_voice_confidence(self):
        app = _command_loop_app(confidence_threshold=0.55)
        app.processor.process = AsyncMock(return_value=SkillResult(True, "Почув вас."))
        task = asyncio.create_task(app._command_loop())
        try:
            await app.command_queue.put(("як твої справи", "text", 0.0))
            await asyncio.wait_for(app.command_queue.join(), 1)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        app.processor.process.assert_awaited_once()

    async def test_continuous_voice_accepts_phrase_without_wake_word(self):
        app = _voice_loop_app()
        task = asyncio.create_task(app._voice_loop())
        try:
            queued = await asyncio.wait_for(app.command_queue.get(), 1)
        finally:
            app.running = False
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        self.assertEqual(queued, ("як твої справи", "voice", 0.91, None))
        self.assertEqual(
            app.listener.listen_once.call_args_list,
            [
                call(15.0, None),
            ],
        )

    async def test_shutdown_command_stops_application(self):
        app = _command_loop_app(confidence_threshold=0.55)
        app.processor.process = AsyncMock(
            return_value=SkillResult(
                True,
                "Завершую роботу Валери.",
                {"shutdown_app": True},
            )
        )
        task = asyncio.create_task(app._command_loop())
        app._tasks = [task]

        await app.command_queue.put(("заверши роботу", "voice", 1.0))
        await asyncio.wait_for(task, 1)

        self.assertFalse(app.running)
        app.speaker.wait_until_idle.assert_awaited_once()

class SkillIntentTests(unittest.IsolatedAsyncioTestCase):
    def test_voice_text_repairs_observed_vosk_errors(self):
        self.assertEqual(
            repair_voice_text("команда під край голограм"),
            "команда відкрий телеграм",
        )
        self.assertEqual(
            repair_voice_text("команда відкритого грам"),
            "команда відкрий телеграм",
        )
        self.assertEqual(
            repair_voice_text("команда відкриє браузер"),
            "команда відкрий браузер",
        )
        self.assertEqual(
            repair_voice_text("Команда відкрив браузер."),
            "Команда відкрий браузер.",
        )
        self.assertEqual(
            repair_voice_text("команда замовкне"),
            "команда замовкни",
        )
        self.assertEqual(
            repair_voice_text("команда знайде в браузері лента ру"),
            "команда знайди в браузері лента ру",
        )
        self.assertEqual(
            repair_voice_text("команда режим душу гук"),
            "команда режим душогуб",
        )
        self.assertEqual(
            repair_voice_text("команда від хром"),
            "команда відкрий хром",
        )

    def test_voice_repair_does_not_manufacture_command_permission(self):
        self.assertEqual(
            repair_voice_text("командах відкрий браузер"),
            "командах відкрий браузер",
        )
        self.assertEqual(
            repair_voice_text("відкриє браузер"),
            "відкриє браузер",
        )
        self.assertEqual(
            repair_voice_text("Відкрей браузер."),
            "Відкрей браузер.",
        )
        self.assertEqual(
            repair_voice_text("Команда від Kray, браузер."),
            "Команда відкрий браузер.",
        )
        self.assertEqual(
            repair_voice_text("банда відкриють браузер"),
            "банда відкриють браузер",
        )
        self.assertEqual(
            repair_voice_text("старова", mode="pentest"),
            "статус душогуба",
        )

    def test_embedded_open_word_is_not_an_app_command(self):
        self.assertFalse(
            apps_skill.can_handle(
                "це не була команда наступна відкрий телеграм",
                {},
            )
        )

    async def test_conversation_mode_switch(self):
        services = {"state": {"mode": "chat"}}
        result = await conversation_skill.handle(
            "режим спілкування",
            SimpleNamespace(),
            services,
        )

        self.assertTrue(result.handled)
        self.assertEqual(services["state"]["mode"], "chat")

    def test_ukrainian_application_alias_and_inflection(self):
        indexer = SimpleNamespace(
            all=Mock(return_value=[{
                "name": "Telegram Desktop",
                "command": "telegram.exe",
                "aliases": ["телеграм", "телега"],
            }])
        )
        controller = ApplicationController(indexer)

        self.assertEqual(controller.find("телеграм")[0]["score"], 100)
        self.assertEqual(controller.find("телеграму")[0]["name"], "Telegram Desktop")

    def test_default_browser_uses_https_url_not_about_protocol(self):
        with patch(
            "services.apps.controller.webbrowser.open_new_tab",
            return_value=True,
        ) as open_tab:
            accepted = ApplicationController.open_default_browser()

        self.assertTrue(accepted)
        open_tab.assert_called_once_with("https://www.google.com/")

    async def test_browser_command_accepts_sentence_punctuation(self):
        open_browser = Mock(return_value=True)
        result = await apps_skill.handle(
            "відкрий браузер",
            SimpleNamespace(raw_text="відкрий браузер."),
            {"apps": SimpleNamespace(open_default_browser=open_browser)},
        )

        self.assertTrue(result.data["accepted"])
        open_browser.assert_called_once_with()

    async def test_local_web_search_does_not_use_llm(self):
        llm = _LLM()
        services = {
            "web": SimpleNamespace(
                search=AsyncMock(
                    return_value=[
                        {
                            "title": "ValleRa",
                            "body": "Локальний голосовий асистент.",
                            "href": "https://example.test",
                        }
                    ]
                )
            ),
            "llm": llm,
        }

        result = await web_skill.handle("пошукай valera", SimpleNamespace(), services)

        self.assertIn("ValleRa", result.response)
        llm.chat.assert_not_awaited()

    async def test_web_search_accepts_query_before_or_after_location(self):
        search = AsyncMock(
            return_value=[{"title": "Lenta", "body": "Новини", "href": ""}]
        )
        services = {"web": SimpleNamespace(search=search)}

        await web_skill.handle(
            "знайди лента ру в інтернеті",
            SimpleNamespace(),
            services,
        )
        await web_skill.handle(
            "знайди в браузері лента ру",
            SimpleNamespace(),
            services,
        )

        self.assertEqual(
            search.await_args_list,
            [call("лента ру"), call("лента ру")],
        )

    async def test_help_skill_lists_local_commands(self):
        result = await help_skill.handle(
            "список команд",
            SimpleNamespace(),
            {},
        )

        self.assertTrue(result.handled)
        self.assertIn("Основні команди", result.response)


class _Router:
    def __init__(self):
        self.responses = {}
        self.commands = []
        self.allowed_skills = []
        self.contexts = []

    async def route(self, command, context, allowed_skills=None):
        self.commands.append(command)
        self.allowed_skills.append(allowed_skills)
        self.contexts.append(context)
        return self.responses.get(command, SkillResult(False))


class _LLM:
    active_name = "gemini"

    def __init__(self):
        self.chat = AsyncMock(return_value="chat-response")


def _command_loop_app(confidence_threshold: float):
    app = object.__new__(ValleRaApp)
    app.web_ui = None
    app.running = True
    app.command_queue = asyncio.Queue()
    app.command_idle = asyncio.Event()
    app.command_idle.set()
    app.settings = SimpleNamespace(
        stt_command_confidence_threshold=confidence_threshold,
        stt_chat_confidence_threshold=0.5,
    )
    app.services = {
        "state": {
            "mode": "chat",
        }
    }
    app.speaker = SimpleNamespace(
        say=AsyncMock(),
        wait_until_idle=AsyncMock(),
    )
    app.processor = SimpleNamespace(process=AsyncMock())
    app.confirmation = SimpleNamespace(ask=AsyncMock(return_value=True))
    app._tasks = []
    return app


def _voice_loop_app():
    app = object.__new__(ValleRaApp)
    app.web_ui = None
    app._mic_ready = asyncio.Event()
    app._mic_ready.set()
    app.microphone_enabled = True
    app._microphone_epoch = 0
    app.running = True
    app.command_queue = asyncio.Queue()
    app.command_idle = asyncio.Event()
    app.command_idle.set()
    app.settings = SimpleNamespace(
        confirmation_timeout_seconds=15,
        stt_post_tts_pause_seconds=0,
    )
    app.services = {"state": {"mode": "chat"}}
    app.speaker = SimpleNamespace(
        busy=False,
        say=AsyncMock(),
        wait_until_idle=AsyncMock(),
    )
    app.listener = SimpleNamespace(
        listen_once=Mock(
            return_value=RecognitionResult("як твої справи", 0.91)
        )
    )
    confirmation_requested = asyncio.Event()
    app.confirmation = SimpleNamespace(
        awaiting=False,
        wait_until_requested=confirmation_requested.wait,
    )
    return app
