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


class ContextualProposalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from core.models import TurnEnvelope
        self.TurnEnvelope = TurnEnvelope
        self.manager = object.__new__(LLMManager)
        self.manager.settings = SimpleNamespace(history_limit=12, llm_total_timeout_seconds=1)
        self.manager.history = Mock()
        self.manager.history.load.return_value = {'messages': []}
        self.manager.system_prompt = 'Fixture'
        self.manager._append_history = Mock()
        self.manager.available_order = ['fixture']
        self.manager.active_name = 'fixture'
        self.manager._cooldowns = {}
        self.requests = []
        self.output = 'CHAT\nВітаю.'

        async def stream(messages):
            self.requests.append(messages)
            yield self.output
        self.manager.providers = {'fixture': SimpleNamespace(chat_stream=stream)}
        self.apps = SimpleNamespace(open_default_browser=Mock(return_value=True), find=Mock(return_value=[]))
        self.services = {'state': {'mode': 'chat'}, 'enabled_skills': {'apps', 'web', 'files', 'windows'},
                         'apps': self.apps, 'memory': SimpleNamespace(relevant=Mock(return_value=[])),
                         'tasks': TaskContext()}
        self.speaker = SimpleNamespace(say=AsyncMock(), stop=AsyncMock())
        self.processor = CommandProcessor(SimpleNamespace(natural_actions_enabled=True),
            SimpleNamespace(route=AsyncMock(return_value=SkillResult(False))), self.manager,
            self.speaker, SimpleNamespace(record=Mock()), self.services)
        self.confirm = AsyncMock(return_value=True)
        self.sequence = 0

    async def turn(self, text, **flags):
        self.sequence += 1
        envelope = self.TurnEnvelope(turn_id=f'fixture:{self.sequence}', session_id='fixture',
            source='text', text=text, transcript=text, stt_engine='text', confidence=1, **flags)
        return await self.processor.process(envelope, self.confirm)

    async def offer(self, tool='web_search', arguments=None):
        self.output = 'PROPOSAL\n' + json.dumps({'tool': tool, 'arguments': arguments or {'query': 'безкоштовні API для STT'}})
        result = await self.turn('Я обираю інструменти для проєкту')
        self.assertEqual(result.data['command_type'], 'action_proposal')
        self.confirm.assert_not_awaited()
        return self.processor.dialogue_state.proposals.pending

    def reply(self, proposal, resolution='accept', arguments=None):
        data = {'proposal_id': proposal.proposal_id, 'resolution': resolution}
        if arguments is not None:
            data['arguments'] = arguments
        self.output = 'FOLLOWUP\n' + json.dumps(data)

    async def test_accepts_same_proposed_query_and_keeps_safety_confirmation(self):
        from core.action_policy import natural_action_permission
        for utterance in ('так', 'давай', 'підбери', 'шукай'):
            self.confirm.reset_mock()
            proposal = await self.offer()
            self.reply(proposal)
            search = AsyncMock(return_value=SkillResult(True, 'fixture', {'success': True}))
            with patch('skills.web.skill.search_web', search), patch(
                    'core.processor.natural_action_permission', wraps=natural_action_permission) as policy:
                result = await self.turn(utterance)
            search.assert_awaited_once_with('безкоштовні API для STT', self.services)
            self.confirm.assert_awaited_once()
            self.assertEqual(result.data['intent_route'], 'contextual_followup')
            self.assertEqual(policy.call_args.args[0].origin, 'contextual_followup')
            self.assertEqual(policy.call_args.args[0].turn_id, f'fixture:{self.sequence}')
            self.assertIsNone(self.processor.dialogue_state.proposals.pending)
            self.assertTrue(any(proposal.proposal_id in message['content'] for message in self.requests[-1]))
            # No second execution from the same model receipt, even on a new turn.
            with patch('core.processor.execute_intent', new_callable=AsyncMock) as execute:
                await self.turn('так')
                execute.assert_not_awaited()

    async def test_reject_clears_without_confirmation_or_execution(self):
        proposal = await self.offer()
        self.reply(proposal, 'reject')
        with patch('core.processor.execute_intent', new_callable=AsyncMock) as execute:
            result = await self.turn('ні')
        self.assertEqual(result.data['command_type'], 'proposal_rejected')
        self.assertIsNone(self.processor.dialogue_state.proposals.pending)
        execute.assert_not_awaited()
        self.confirm.assert_not_awaited()

    async def test_modify_only_reoffers_then_needs_a_new_acceptance(self):
        proposal = await self.offer(arguments={'query': 'API для STT'})
        self.reply(proposal, 'modify', {'query': 'тільки безкоштовні API для STT'})
        with patch('core.processor.execute_intent', new_callable=AsyncMock) as execute:
            result = await self.turn('тільки безкоштовні')
        self.assertEqual(result.data['command_type'], 'action_proposal_modified')
        updated = self.processor.dialogue_state.proposals.pending
        self.assertNotEqual(updated.proposal_id, proposal.proposal_id)
        self.assertIn('тільки безкоштовні', result.response)
        execute.assert_not_awaited()
        self.confirm.assert_not_awaited()
        self.reply(updated)
        with patch('skills.web.skill.search_web', new_callable=AsyncMock,
                   return_value=SkillResult(True)) as search:
            await self.turn('так')
        search.assert_awaited_once_with('тільки безкоштовні API для STT', self.services)
        self.confirm.assert_awaited_once()

    async def test_no_stale_or_cross_session_proposal_can_execute(self):
        from dataclasses import replace
        for invalidation in ('missing', 'expired', 'session', 'cancel', 'unrelated'):
            self.confirm.reset_mock()
            proposal = await self.offer()
            owner = self.processor.dialogue_state.proposals
            if invalidation == 'missing':
                owner.clear()
            elif invalidation == 'expired':
                owner.pending = replace(proposal, expires_at=0)
            elif invalidation == 'session':
                owner.pending = replace(proposal, session_id='other')
            elif invalidation == 'cancel':
                self.processor.interrupt_conversation(discard_proposal=True)
            else:
                self.output = 'CHAT\nНова тема.'
                await self.turn('Розкажи про фотосинтез')
            self.reply(proposal)
            with patch('core.processor.execute_intent', new_callable=AsyncMock) as execute:
                await self.turn('так')
            execute.assert_not_awaited()
            self.confirm.assert_not_awaited()

    async def test_bare_followup_without_proposal_clarifies_even_if_model_invents_action(self):
        self.output = 'ACTION\n' + json.dumps({'tool': 'open_app', 'arguments': {'name': 'браузер'}})
        result = await self.turn('підбери')
        self.assertEqual(result.data['command_type'], 'intent_clarification')
        self.assertFalse(self.requests)
        self.confirm.assert_not_awaited()

    async def test_local_action_acceptance_still_requires_distinct_confirmation(self):
        proposal = await self.offer('open_app', {'name': 'браузер'})
        self.reply(proposal)
        self.confirm.return_value = False
        result = await self.turn('відкривай')
        self.confirm.assert_awaited_once()
        self.assertEqual(result.execution.status.value, 'cancelled')
        self.apps.open_default_browser.assert_not_called()
        self.reply(proposal)
        await self.turn('так')
        self.apps.open_default_browser.assert_not_called()

    async def test_unreliable_turn_cannot_accept_or_recreate_proposal(self):
        proposal = await self.offer()
        self.reply(proposal)
        with patch('core.processor.execute_intent', new_callable=AsyncMock) as execute:
            await self.turn('так', recognition_unreliable=True, action_eligible=False)
        execute.assert_not_awaited()
        self.confirm.assert_not_awaited()
        self.assertIsNone(self.processor.dialogue_state.proposals.pending)

    async def test_model_accept_cannot_override_negation_question_or_extra_command(self):
        for text in ('ні', 'а що ти про них знаєш?', 'так але спочатку видали файл', '"так"'):
            proposal = await self.offer()
            self.reply(proposal)
            with patch('core.processor.execute_intent', new_callable=AsyncMock) as execute:
                await self.turn(text)
            execute.assert_not_awaited()
            self.confirm.assert_not_awaited()

    async def test_scoped_file_selection_has_priority(self):
        proposal = await self.offer()
        self.reply(proposal)
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ('a.txt', 'b.txt')]
            for path in paths:
                path.touch()
            self.services['tasks'].offer('file', paths, command_type='file_search')
            with patch.object(self.services['tasks'], 'open_file', new_callable=AsyncMock,
                              return_value=SkillResult(True)) as open_file:
                await self.turn('другий')
            self.assertEqual(open_file.await_args.args[0], paths[1])
        self.assertEqual(len(self.requests), 1)  # Proposal creation only.
        self.assertIsNone(self.processor.dialogue_state.proposals.pending)

    async def test_safety_confirmation_consumes_reply_before_proposal(self):
        from core.app import ValleRaApp
        await self.offer()
        app = object.__new__(ValleRaApp)
        app.processor, app.speaker, app.services, app.web_ui = self.processor, self.speaker, self.services, None
        app.confirmation = ConfirmationService(app._say_confirmation, timeout_seconds=1)
        question = asyncio.create_task(app.confirmation.ask('Fixture safety approval'))
        await app.confirmation.wait_until_requested()
        await app._submit_text('так', app.confirmation.request_id)
        self.assertTrue(await question)
        self.assertEqual(len(self.requests), 1)
        self.assertIsNone(self.processor.dialogue_state.proposals.pending)
        self.apps.open_default_browser.assert_not_called()

    async def test_late_resolution_after_interrupt_cannot_execute(self):
        from core.natural_turn import NaturalTurn
        from core.action_proposal import ProposalReply, ReplyKind
        proposal = await self.offer()
        entered, release = asyncio.Event(), asyncio.Event()

        async def ignoring_cancel(*args, **kwargs):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass
            return NaturalTurn('followup', proposal_reply=ProposalReply(proposal.proposal_id, ReplyKind.ACCEPT))

        self.manager.converse = ignoring_cancel
        with patch('core.processor.execute_intent', new_callable=AsyncMock) as execute:
            task = asyncio.create_task(self.turn('так'))
            await entered.wait()
            self.processor.interrupt_conversation()
            release.set()
            await task
        execute.assert_not_awaited()

    async def test_protocol_never_speaks_control_json_and_does_not_store_it_in_history(self):
        proposal = await self.offer()
        self.speaker.say.assert_not_awaited()
        self.manager._append_history.assert_not_called()
        self.reply(proposal, 'reject')
        await self.turn('ні')
        self.speaker.say.assert_not_awaited()
        self.manager._append_history.assert_not_called()

    async def test_scoped_clarification_has_priority_over_assistant_proposal(self):
        import time
        await self.offer()
        self.processor._natural_pending = {'tool': 'open_app', 'request': 'відкрий',
                                          'question': 'Яку програму?', 'expires': time.monotonic() + 60}
        self.output = 'ACTION\n' + json.dumps({'tool': 'open_app', 'arguments': {'name': 'браузер'}})
        await self.turn('браузер')
        self.apps.open_default_browser.assert_called_once()
        self.confirm.assert_awaited_once()
        self.assertFalse(any('Локально активна пропозиція' in item['content'] for item in self.requests[-1]))
        self.assertIsNone(self.processor.dialogue_state.proposals.pending)

    async def test_duplicate_envelope_acceptance_dispatches_once(self):
        from core.app import ValleRaApp
        app = object.__new__(ValleRaApp)
        app.command_queue = asyncio.Queue()
        app.command_idle = asyncio.Event()
        app.running, app.web_ui = True, None
        app.services, app.processor, app.speaker = self.services, self.processor, self.speaker
        app.confirmation = SimpleNamespace(ask=self.confirm)
        initial = await app._enqueue_command('розмова', 'text', 1)
        await app.command_queue.get()
        app.command_queue.task_done()
        self.output = 'PROPOSAL\n' + json.dumps({'tool': 'open_app', 'arguments': {'name': 'браузер'}})
        await self.processor.process(initial, self.confirm)
        proposal = self.processor.dialogue_state.proposals.pending
        self.reply(proposal)
        turn = await app._enqueue_command('так', 'text', 1)
        await app.command_queue.put(turn)
        task = asyncio.create_task(app._command_loop())
        try:
            await asyncio.wait_for(app.command_queue.join(), 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.apps.open_default_browser.assert_called_once()
        self.confirm.assert_awaited_once()
        self.assertEqual(len(self.requests), 2)

    async def test_timeout_during_resolution_cannot_authorize(self):
        proposal = await self.offer()
        self.reply(proposal)
        owner = self.processor.dialogue_state.proposals

        async def stream(messages):
            owner.clock = lambda: proposal.expires_at + 1
            yield self.output

        self.manager.providers['fixture'].chat_stream = stream
        with patch('core.processor.execute_intent', new_callable=AsyncMock) as execute:
            await self.turn('так')
        execute.assert_not_awaited()
        self.confirm.assert_not_awaited()

    async def test_external_cancel_cannot_register_late_offer(self):
        from core.natural_turn import NaturalTurn
        entered = asyncio.Event()

        async def late(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return NaturalTurn('proposal', intent=CommandIntent('open_app', {'name': 'браузер'}))

        self.manager.converse = late
        task = asyncio.create_task(self.turn('поради'))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(self.processor.dialogue_state.proposals.pending)

    def test_store_is_immutable_ephemeral_and_rejects_dangerous_or_disabled_tools(self):
        from core.action_proposal import ProposalState
        owner = ProposalState()
        args = {'name': 'браузер'}
        proposal = owner.offer(CommandIntent('open_app', args), 'turn:1', 'session', {'apps'}, 0)
        args['name'] = 'інша програма'
        self.assertEqual(proposal.intent.arguments['name'], 'браузер')
        self.assertIsNone(ProposalState().pending)
        for tool, arguments, enabled in (('delete_file', {'path': 'fixture'}, {'files'}),
                                         ('open_app', {'name': 'браузер'}, set())):
            fresh = ProposalState()
            self.assertIsNone(fresh.offer(CommandIntent(tool, arguments), 'turn:1', 'session', enabled, 0))
            self.assertIsNone(fresh.pending)

    def test_reply_decoder_rejects_swapped_arguments_and_mixed_control(self):
        from core.natural_turn import TurnDecoder
        texts = [
            'FOLLOWUP\n{"proposal_id":"' + 'a' * 32 + '","resolution":"accept","arguments":{"name":"інше"}}',
            'FOLLOWUP\n{"proposal_id":"' + 'a' * 32 + '","resolution":"accept","resolution":"reject"}',
            'CHAT\nПривіт\nPROPOSAL\n{"tool":"open_app","arguments":{"name":"браузер"}}',
        ]
        for text in texts:
            decoder = TurnDecoder()
            with self.assertRaises(InvalidIntent):
                decoder.feed(text)
                decoder.finish()

    async def test_invalid_proposal_returns_existing_unavailable_notice_without_state(self):
        self.output = 'PROPOSAL\n' + json.dumps({'tool': 'delete_file', 'arguments': {'path': 'fixture'}})
        with patch('core.processor.execute_intent', new_callable=AsyncMock) as execute:
            result = await self.turn('поради')
        self.assertIn('Нічого не виконано', result.response)
        self.assertNotIn('DecisionKind', result.response)
        self.assertIsNone(self.processor.dialogue_state.proposals.pending)
        execute.assert_not_awaited()
        self.confirm.assert_not_awaited()


class IntentValidationTests(unittest.TestCase):
    def test_typed_execution_states_do_not_infer_success(self):
        from core.execution_result import ExecutionStatus
        cases = [
            ({'accepted': False}, ExecutionStatus.REJECTED),
            ({'status': 'cancelled', 'accepted': False}, ExecutionStatus.CANCELLED),
            ({'accepted': True}, ExecutionStatus.SUBMITTED),
            ({'accepted': True, 'verified': False, 'success': False}, ExecutionStatus.SUBMITTED_UNVERIFIED),
            ({'status': 'failed'}, ExecutionStatus.FAILED),
            ({'accepted': True, 'verified': True, 'success': True}, ExecutionStatus.VERIFIED),
            ({}, ExecutionStatus.UNKNOWN),
        ]
        for data, status in cases:
            with self.subTest(data=data):
                result = SkillResult(True, 'fixture', dict(data))
                self.assertEqual(result.execution.status, status)
                self.assertEqual(result.execution.success, data.get('success'))
                self.assertEqual(result.execution.verified, data.get('verified'))
                self.assertEqual(result.data, data)

    def test_action_policy_keeps_identity_scope_and_quality_separate(self):
        from core.action_policy import natural_action_permission, PolicyOutcome, turn_permission
        from core.dialogue_decision import DialogueDecision, DecisionKind
        intent = CommandIntent('open_app', {'name': 'браузер'})
        for kind in DecisionKind:
            candidate = DialogueDecision(kind, intent=intent, turn_id='session:7')
            permission = natural_action_permission(candidate, 'відкрий браузер', 'відкрий браузер', None, False)
            self.assertEqual(permission.turn_id, 'session:7')
            self.assertEqual(permission.outcome, PolicyOutcome.ALLOW if kind is DecisionKind.ACTION_CANDIDATE
                             else PolicyOutcome.SAFE_NO_ACTION)
        candidate = DialogueDecision(DecisionKind.ACTION_CANDIDATE, intent=intent, turn_id='session:7')
        self.assertEqual(natural_action_permission(candidate, 'відкрий браузер', 'відкрий браузер',
                         None, False, action_eligible=False).outcome, PolicyOutcome.SAFE_NO_ACTION)
        self.assertEqual(natural_action_permission(candidate, 'я користуюсь браузером',
                         'я користуюсь браузером', None, False).outcome, PolicyOutcome.CLARIFY)
        self.assertEqual(turn_permission(False, True), PolicyOutcome.CLARIFY)
        self.assertEqual(turn_permission(False, False), PolicyOutcome.SAFE_NO_ACTION)
        self.assertEqual(turn_permission(False, True, stop=True), PolicyOutcome.ALLOW)

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
        self.assertFalse(result.execution.verified)
        self.assertFalse(result.execution.success)
        self.apps.open_default_browser.assert_called_once()

    async def test_denial_never_executes(self):
        self.confirm.return_value = False
        result = await execute_intent(CommandIntent("open_app", {"name": "браузер"}), self.context)
        self.assertEqual(result.data["command_type"], "interpretation_cancelled")
        self.assertEqual(result.execution.status.value, 'cancelled')
        self.apps.open_default_browser.assert_not_called()

    async def test_cancelled_approval_cannot_be_reused_by_next_action(self):
        speaking = asyncio.Event()
        release = asyncio.Event()

        async def speech():
            speaking.set()
            await release.wait()

        confirmation = ConfirmationService(AsyncMock(), wait_for_speech=speech)
        self.context.confirm = confirmation.ask
        intent = CommandIntent('open_app', {'name': 'браузер'})
        old = asyncio.create_task(execute_intent(intent, self.context))
        await asyncio.wait_for(speaking.wait(), 1)
        confirmation.submit('так', confirmation.request_id)
        old_id = confirmation.request_id
        old.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await old
        self.assertFalse(confirmation.submit('так'))
        release.set()
        new = asyncio.create_task(execute_intent(intent, self.context))
        try:
            await asyncio.wait_for(confirmation.wait_until_requested(), 1)
            self.assertNotEqual(old_id, confirmation.request_id)
            self.assertTrue(confirmation._responses.empty())
            self.apps.open_default_browser.assert_not_called()
            confirmation.submit('ні', confirmation.request_id)
            result = await asyncio.wait_for(new, 1)
            self.assertEqual(result.data['command_type'], 'interpretation_cancelled')
            self.apps.open_default_browser.assert_not_called()
        finally:
            if not new.done():
                new.cancel()
            await asyncio.gather(new, return_exceptions=True)

    async def test_correlated_voice_no_does_not_execute_action(self):
        confirmation = ConfirmationService(AsyncMock(), timeout_seconds=1)
        self.context.confirm = confirmation.ask
        task = asyncio.create_task(execute_intent(CommandIntent('open_app', {'name': 'браузер'}), self.context))
        try:
            await confirmation.wait_until_requested()
            self.assertTrue(confirmation.submit('ні', confirmation.request_id, confidence=.95))
            result = await asyncio.wait_for(task, 1)
            self.assertEqual(result.data['command_type'], 'interpretation_cancelled')
            self.apps.open_default_browser.assert_not_called()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_submission_is_not_verified_success_and_is_not_retried(self):
        self.apps.verify_launch = Mock(return_value={'verified': False})
        result = await execute_intent(CommandIntent('open_app', {'name': 'браузер'}), self.context)
        self.assertTrue(result.data['accepted'])
        self.assertFalse(result.data['verified'])
        self.assertFalse(result.data['success'])
        self.apps.open_default_browser.assert_called_once()
        self.apps.verify_launch.assert_called_once()
        self.confirm.assert_awaited_once()

    async def test_cancellation_while_speaking_clears_confirmation_and_never_executes(self):
        started = asyncio.Event()

        async def wait_for_speech():
            started.set()
            await asyncio.Event().wait()

        confirmation = ConfirmationService(AsyncMock(), wait_for_speech=wait_for_speech)
        self.context.confirm = confirmation.ask
        task = asyncio.create_task(execute_intent(CommandIntent("open_app", {"name": "браузер"}), self.context))
        await asyncio.wait_for(started.wait(), 1)
        confirmation.submit("так", confirmation.request_id)
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
