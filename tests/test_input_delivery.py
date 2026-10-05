"""Waiting text must not inherit a prompt created after its arrival."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from core.app import ValleRaApp


class InputDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def app(self):
        app = ValleRaApp.__new__(ValleRaApp)
        app.command_idle = asyncio.Event()
        app.confirmation = SimpleNamespace(awaiting=False, request_id=None, submit=Mock())
        app.processor = SimpleNamespace(
            dialogue_state=SimpleNamespace(pending=None, proposals=SimpleNamespace(pending=None)),
            interrupt_conversation=Mock())
        app.speaker = SimpleNamespace(stop=AsyncMock())
        app.web_ui = Mock()
        app.services = {'state': {'mode': 'chat'}, 'tasks': SimpleNamespace(pending=None)}
        app._enqueue_command = AsyncMock()
        app._wait_for_input_slot = app.command_idle.wait
        return app

    async def test_waited_request_dispatches_once_when_context_unchanged(self):
        app = self.app()
        task = asyncio.create_task(app._submit_text('нове питання'))
        await asyncio.sleep(0)
        self.assertIsNotNone(app._waiting_input)
        app.command_idle.set()
        await asyncio.wait_for(task, 1)
        app._enqueue_command.assert_awaited_once_with('нове питання', 'text', 1.0)
        self.assertIsNone(app._waiting_input)

    async def test_new_selection_or_clarification_cannot_consume_waiting_reply(self):
        for owner in ('tasks', 'dialogue', 'proposal', 'confirmation'):
            app = self.app()
            task = asyncio.create_task(app._submit_text('так'))
            await asyncio.sleep(0)
            if owner == 'tasks':
                app.services['tasks'].pending = object()
            elif owner == 'dialogue':
                app.processor.dialogue_state.pending = object()
            elif owner == 'proposal':
                app.processor.dialogue_state.proposals.pending = object()
            else:
                app.confirmation.awaiting = True
            app.command_idle.set()
            await asyncio.wait_for(task, 1)
            app._enqueue_command.assert_not_awaited()
            app.confirmation.submit.assert_not_called()

    async def test_stop_cancels_delivery_not_local_operation(self):
        app = self.app()
        task = asyncio.create_task(app._submit_text('наступне питання'))
        await asyncio.sleep(0)
        await app.web_control('stop', {})
        await asyncio.wait_for(task, 1)
        app.command_idle.set()
        app._enqueue_command.assert_not_awaited()
        app.confirmation.submit.assert_not_called()
        self.assertIsNone(app._waiting_input)

    async def test_cancelled_caller_leaves_no_waiter_or_dispatch(self):
        app = self.app()
        task = asyncio.create_task(app._submit_text('наступне питання'))
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertIsNone(app._waiting_input)
        app._enqueue_command.assert_not_awaited()

    async def test_only_one_waiting_delivery(self):
        app = self.app()
        task = asyncio.create_task(app._submit_text('перше'))
        await asyncio.sleep(0)
        await app._submit_text('друге')
        app.command_idle.set()
        await asyncio.wait_for(task, 1)
        app._enqueue_command.assert_awaited_once_with('перше', 'text', 1.0)
