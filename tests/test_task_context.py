import asyncio
import contextlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from config import ProjectPaths, Settings
from core.app import ValleRaApp
from core.command_intent import CommandIntent
from core.command_router import CommandRouter
from core.models import CommandContext, SkillResult
from core.processor import CommandProcessor
from core.skill_loader import SkillLoader
from core.task_context import TaskContext
from services.files.file_service import FileService
from skills.apps import skill as app_skill
from skills.files import skill as file_skill


class TaskContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.paths = [self.root / name for name in ("диплом-план.txt", "диплом-чернетка.txt")]
        for path in self.paths:
            path.write_text("fixture", encoding="utf-8")
        self.files = FileService([str(self.root)])
        self.files.open = Mock()
        self.now = 10.0
        self.tasks = TaskContext(clock=lambda: self.now)
        self.services = {"files": self.files, "tasks": self.tasks}
        self.confirm = AsyncMock(return_value=True)
        self.context = CommandContext(SimpleNamespace(), self.services, self.confirm)

    async def test_select_second_file_is_local_and_one_shot(self):
        result = self.tasks.offer("file", self.paths, command_type="file_search")
        self.assertEqual(len(result.data["task_choices"]), 2)
        self.files.open.assert_not_called()
        result = await self.tasks.consume("Другий.", self.context)
        self.assertTrue(result.data["accepted"])
        self.files.open.assert_called_once_with(self.paths[1])
        self.assertIsNone(await self.tasks.consume("Другий", self.context))

    async def test_selection_variants(self):
        for reply in ("другий", "два", "2", "відкрий другий файл", "Команда: другий", "номер 2"):
            with self.subTest(reply=reply):
                self.tasks.offer("file", self.paths, command_type="file_search")
                self.assertTrue(self.tasks.is_selection_reply(reply))
                result = await self.tasks.consume(reply, self.context)
                self.assertTrue(result.data["accepted"])

    async def test_cancel_drops_scope(self):
        for reply in ("скасувати", "ні", "Команда: скасуй"):
            self.tasks.offer("file", self.paths, command_type="file_search")
            result = await self.tasks.consume(reply, self.context)
            self.assertEqual(result.data["command_type"], "task_cancelled")
            self.assertIsNone(self.tasks.pending)
        self.files.open.assert_not_called()

    async def test_expired_choice_never_executes(self):
        self.tasks.offer("file", self.paths, command_type="file_search")
        self.now = 131.0
        result = await self.tasks.consume("другий", self.context)
        self.assertEqual(result.data["command_type"], "task_expired")
        self.files.open.assert_not_called()

    async def test_unrelated_phrase_or_new_command_clears_old_scope(self):
        for reply in ("як твої справи", "це другий день", "Команда: погода в місті Луцьк"):
            self.tasks.offer("file", self.paths, command_type="file_search")
            self.assertIsNone(await self.tasks.consume(reply, self.context))
            self.assertIsNone(await self.tasks.consume("другий", self.context))
        self.files.open.assert_not_called()

    async def test_out_of_range_preserves_choices_without_executing(self):
        self.tasks.offer("file", self.paths, command_type="file_search")
        result = await self.tasks.consume("9", self.context)
        self.assertEqual(result.data["command_type"], "task_invalid_choice")
        self.assertIsNotNone(self.tasks.pending)
        self.files.open.assert_not_called()

    async def test_file_must_still_exist_and_be_allowed_at_execution(self):
        self.tasks.offer("file", self.paths, command_type="file_search")
        self.paths[1].unlink()
        result = await self.tasks.consume("другий", self.context)
        self.assertFalse(result.data["accepted"])
        self.tasks.offer("file", self.paths[:1], command_type="file_search")
        with patch.object(self.files, "is_allowed", return_value=False):
            result = await self.tasks.consume("перший", self.context)
        self.assertFalse(result.data["accepted"])
        self.files.open.assert_not_called()

    async def test_non_document_file_requires_explicit_confirmation(self):
        path = self.root / "fixture.exe"
        path.write_bytes(b"not executable, test fixture only")
        self.tasks.offer("file", [path], command_type="file_search")
        self.confirm.return_value = False
        result = await self.tasks.consume("перший", self.context)
        self.assertEqual(result.data["command_type"], "task_cancelled")
        self.confirm.assert_awaited_once()
        self.files.open.assert_not_called()

    async def test_apps_are_rechecked_against_index(self):
        apps = [{"name": "Editor One", "command": "one", "score": 95},
                {"name": "Editor Two", "command": "two", "score": 94}]
        controller = SimpleNamespace(find=Mock(return_value=[apps[1]]), open=Mock(return_value=True))
        self.services["apps"] = controller
        self.tasks.offer("app", apps, command_type="app_choice")
        result = await self.tasks.consume("відкрий другу програму", self.context)
        self.assertTrue(result.data["accepted"])
        controller.open.assert_called_once_with(apps[1])
        self.tasks.offer("app", apps, command_type="app_choice")
        controller.find.return_value = [{**apps[1], "command": "changed"}]
        result = await self.tasks.consume("другий", self.context)
        self.assertFalse(result.data["accepted"])
        self.assertEqual(controller.open.call_count, 1)

    async def test_file_skill_searches_name_and_offers_selection(self):
        self.context.raw_text = "знайди документ про диплом"
        result = await file_skill.handle(self.context.raw_text, self.context, self.services)
        self.assertEqual(result.data["command_type"], "file_search")
        self.assertEqual(len(self.tasks.pending.entries), 2)
        self.files.open.assert_not_called()

    async def test_ambiguous_file_open_does_not_choose_first_match(self):
        self.context.raw_text = "відкрий файл за назвою диплом"
        result = await file_skill.handle(self.context.raw_text, self.context, self.services)
        self.assertEqual(result.data["command_type"], "file_choice")
        self.files.open.assert_not_called()

    async def test_noncanonical_file_request_can_reach_interpreter(self):
        self.context.raw_text = "знайди мені будь ласка документ про диплом"
        with patch.object(self.files, "search") as search:
            result = await file_skill.handle(self.context.raw_text, self.context, self.services)
        self.assertFalse(result.handled)
        search.assert_not_called()

    def test_apps_do_not_steal_file_open_requests(self):
        for text in ("відкрий файл диплом", "відкрий документ диплом", "відкрий файли диплом"):
            self.assertFalse(app_skill.can_handle(text, {}))

    def test_file_search_does_not_return_paths_outside_allowed_scope(self):
        with patch.object(self.files, "is_allowed", return_value=False):
            self.assertEqual(self.files.search("диплом"), [])


class TaskIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_router_search_then_second_choice_without_llm(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("диплом-1.txt", "диплом-2.txt"):
                (root / name).write_text("fixture", encoding="utf-8")
            settings = Settings(ProjectPaths.from_root(root))
            files = FileService([str(root)])
            files.open = Mock()
            llm = SimpleNamespace(active_name="fixture", chat=AsyncMock(return_value="chat"),
                                  interpret_command=AsyncMock(), record_local_action=Mock())
            services = {"state": {"mode": "chat"}, "tasks": TaskContext(), "files": files,
                        "apps": SimpleNamespace(find=Mock(return_value=[])),
                        "memory": SimpleNamespace(relevant=Mock(return_value=[]))}
            skills_path = Path(__file__).resolve().parents[1] / "skills"
            router = CommandRouter([skill for skill in SkillLoader(skills_path, services).load()
                                    if skill.name in {"apps", "files", "web"}])
            services["enabled_skills"] = {skill.name for skill in router.skills}
            processor = CommandProcessor(settings, router, llm, Mock(), Mock(), services)
            result = await processor.process("Команда: знайди документ про диплом", AsyncMock())
            self.assertEqual(result.data["command_type"], "file_search")
            target = services["tasks"].pending.entries[1]
            result = await processor.process("Другий", AsyncMock())
            self.assertTrue(result.data["accepted"])
            files.open.assert_called_once_with(target)
            llm.chat.assert_not_awaited()
            llm.interpret_command.assert_not_awaited()
            self.assertIn(target.name, services["state"]["last_open_action"]["response"])

    async def test_interpreted_search_then_selection_uses_one_model_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "диплом.txt").write_text("fixture", encoding="utf-8")
            files = FileService([str(root)])
            files.open = Mock()
            llm = SimpleNamespace(active_name="fixture", chat=AsyncMock(), record_local_action=Mock(),
                                  interpret_command=AsyncMock(return_value=CommandIntent("find_files", {"query": "диплом", "extension": ""})))
            services = {"state": {"mode": "chat"}, "tasks": TaskContext(), "files": files,
                        "enabled_skills": {"files"}}
            processor = CommandProcessor(SimpleNamespace(command_interpretation_enabled=True),
                                          SimpleNamespace(route=AsyncMock(return_value=SkillResult(False))),
                                          llm, Mock(), Mock(), services)
            confirm = AsyncMock(return_value=True)
            result = await processor.process("Команда: допоможи відшукати документ про диплом", confirm)
            self.assertEqual(result.data["command_type"], "file_search")
            confirm.assert_awaited_once()
            files.open.assert_not_called()
            await processor.process("перший", confirm)
            files.open.assert_called_once_with(root / "диплом.txt")
            llm.interpret_command.assert_awaited_once()
            llm.chat.assert_not_awaited()

    async def test_voice_choice_uses_command_confidence_threshold(self):
        app = object.__new__(ValleRaApp)
        app.web_ui = None
        app.running = True
        tasks = TaskContext()
        tasks.offer("file", [Path("fixture.txt")], command_type="file_search")
        app.services = {"state": {"mode": "chat"}, "tasks": tasks}
        app.settings = SimpleNamespace(stt_command_confidence_threshold=0.55, stt_chat_confidence_threshold=0.5)
        app.command_queue = asyncio.Queue()
        app.command_idle = asyncio.Event()
        app.speaker = SimpleNamespace(say=AsyncMock())
        app.processor = SimpleNamespace(process=AsyncMock())
        app.confirmation = SimpleNamespace(ask=AsyncMock())
        task = asyncio.create_task(app._command_loop())
        try:
            await app.command_queue.put(("другий", "voice", 0.52))
            await asyncio.wait_for(app.command_queue.join(), 1)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        app.processor.process.assert_not_awaited()
        self.assertIn("Повторіть", app.speaker.say.await_args.args[0])
