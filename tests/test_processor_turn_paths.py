"""Turn paths the gate matrix does not cover (audit 2026-10-29 #3, step 3).

Pins the post-processing contract kept by the decide/route split: an
interrupted chat returns early without metrics; a finished turn records
metrics exactly once and local actions update session state.
"""
import asyncio
import unittest

from core.models import SkillResult, TurnEnvelope
from tests.processor_gate_matrix import build


def turn(text):
    return TurnEnvelope(turn_id="paths", session_id="paths", source="text", text=text,
                        transcript=text, stt_engine="text", confidence=1.0)


def completed(processor):
    return [c for c in processor.metrics.record.call_args_list if c.args[0] == "command_completed"]


class ProcessorTurnPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_interrupted_chat_returns_early_without_completion_metric(self):
        processor, _router, llm, _memory, _speaker = build("chat", False)
        started = asyncio.Event()

        async def slow_chat(*_args, **_kwargs):
            started.set()
            await asyncio.sleep(30)

        llm.chat.side_effect = slow_chat
        task = asyncio.create_task(processor.process(turn("Розкажи історію"), None))
        await started.wait()
        self.assertTrue(processor.interrupt_conversation())
        result = await task

        self.assertEqual(result.data["command_type"], "chat_interrupted")
        self.assertEqual(completed(processor), [])
        self.assertIsNone(processor._chat_task)

    async def test_finished_chat_records_one_completion_metric(self):
        processor, _router, _llm, _memory, _speaker = build("chat", False)

        result = await processor.process(turn("Розкажи історію"), None)

        self.assertEqual(result.data["command_type"], "chat")
        self.assertEqual(len(completed(processor)), 1)
        self.assertEqual(completed(processor)[0].kwargs["provider"], "stub")

    async def test_local_open_action_updates_session_state_and_metric(self):
        processor, router, _llm, _memory, _speaker = build("chat", False)

        async def route(command, context, allowed_skills=None):
            return SkillResult(True, "Відкриваю браузер.",
                               {"command_type": "open_browser", "accepted": True})

        router.route = route
        await processor.process(turn("Команда: відкрий браузер"), None)

        state = processor.services["state"]
        self.assertEqual(state["last_open_action"]["command_type"], "open_browser")
        self.assertEqual(state["last_local_action"]["command"], "відкрий браузер")
        self.assertEqual(completed(processor)[0].kwargs["provider"], "local")

    async def test_compound_command_is_refused_without_routing(self):
        processor, router, _llm, _memory, _speaker = build("chat", False)

        result = await processor.process(turn("Команда: відкрий браузер і закрий телеграм"), None)

        self.assertEqual(result.data["command_type"], "compound_command")
        self.assertEqual(router.calls, [])

    async def test_stop_in_pentest_mode_stops_speech_without_routing(self):
        """J125: «стоп» is speech control in pentest mode too, never a scan command."""
        processor, router, _llm, _memory, speaker = build("pentest", False)

        result = await processor.process(turn("Стоп!"), None)

        self.assertEqual(result.data["command_type"], "speech_stopped")
        speaker.stop.assert_awaited_once()
        self.assertEqual(router.calls, [])

    async def test_prefixed_stop_in_pentest_mode_keeps_local_routing(self):
        processor, router, _llm, _memory, speaker = build("pentest", False)

        await processor.process(turn("Команда: стоп"), None)

        self.assertEqual(router.calls, [("стоп", None)])
        speaker.stop.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
