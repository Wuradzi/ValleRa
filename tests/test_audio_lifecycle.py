import asyncio
import multiprocessing
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from config import ProjectPaths, Settings
from core.app import ValleRaApp
from core.models import RecognitionResult, SkillResult, TurnEnvelope
from core.performance import DISABLED_PERFORMANCE, TurnTiming
from core.speak import Speaker
from services.audio.whisper_process import ProcessWhisperRecognizer, _worker
from services.audio.refinement import RefinementWait


class InterruptingConnection:
    """Deliver SIGINT only to the isolated child, never the parent console."""

    def __init__(self, connection):
        self.connection = connection

    def recv(self):
        signal.raise_signal(signal.SIGINT)
        self.connection.send("survived")
        return "close", ()

    def close(self):
        self.connection.close()


def slow_worker(connection, settings):
    connection.recv()
    time.sleep(60)


def echo_worker(connection, settings):
    try:
        while True:
            command, args = connection.recv()
            result = (
                (True, "ready")
                if command == "prepare"
                else RecognitionResult("привіт", 1, "whisper")
            )
            connection.send((result, (True, "ready"), 0.01))
    except (EOFError, OSError):
        pass


def delayed_echo_worker(connection, settings):
    try:
        while True:
            command, args = connection.recv()
            if command == 'prepare':
                result = (True, 'ready')
            else:
                time.sleep(.15)
                result = RecognitionResult(args[0].decode('ascii'), .99, 'whisper')
            connection.send((result, (True, 'ready'), .15))
    except (EOFError, OSError):
        pass


class ProcessLifecycleTests(unittest.TestCase):
    def test_bounded_wait_drains_old_pipe_reply_and_reuses_same_process(self):
        worker = ProcessWhisperRecognizer(self.settings(), worker_target=delayed_echo_worker)
        wait = RefinementWait()
        try:
            self.assertTrue(worker.prepare()[0])
            pid = worker._process.pid
            self.assertEqual(wait.run(worker, b'old', 16000, 5, 20, lambda: False)[1], 'timeout')
            self.assertEqual(wait.run(worker, b'not-sent', 16000, 5, 20, lambda: False)[1], 'busy')
            self.assertTrue(wait._active.wait(5))
            result, outcome, _ = wait.run(worker, b'new', 16000, 500, 1000, lambda: False)
            self.assertEqual(outcome, 'done')
            self.assertEqual(result.text, 'new')
            self.assertEqual(worker._process.pid, pid)
        finally:
            worker.close()

    def test_worker_ignores_its_own_sigint_and_exits_cleanly(self):
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=_worker, args=(InterruptingConnection(child), self.settings()))
        process.start()
        child.close()
        try:
            self.assertTrue(parent.poll(10))
            self.assertEqual(parent.recv(), "survived")
            process.join(timeout=5)
            self.assertEqual(process.exitcode, 0)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=3)
            process.close()
            parent.close()

    def test_worker_catches_keyboard_interrupt_during_pipe_read(self):
        connection = SimpleNamespace(recv=Mock(side_effect=KeyboardInterrupt), close=Mock())
        with patch("services.audio.whisper_process.signal.signal") as handler:
            _worker(connection, self.settings())
        handler.assert_called_once_with(signal.SIGINT, signal.SIG_IGN)
        connection.close.assert_called_once()

    def settings(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return Settings(ProjectPaths.from_root(Path(temp.name)))

    def test_model_worker_is_reused_and_closed(self):
        worker = ProcessWhisperRecognizer(self.settings(), worker_target=echo_worker)
        try:
            self.assertTrue(worker.prepare()[0])
            pid = worker._process.pid
            self.assertEqual(worker.transcribe(b"\x00\x00", 16000).text, "привіт")
            self.assertEqual(worker._process.pid, pid)
        finally:
            worker.close()
        self.assertIsNone(worker._process)

    def test_shutdown_interrupts_native_work_instead_of_waiting_for_it(self):
        worker = ProcessWhisperRecognizer(self.settings(), worker_target=slow_worker)
        thread = threading.Thread(target=worker.prepare, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while worker._process is None and time.monotonic() < deadline:
                time.sleep(0.01)
            started = time.monotonic()
            worker.close()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertLess(time.monotonic() - started, 4)
        finally:
            worker.close()


class VoiceTurnContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_envelope_flags_are_independent_and_immutable(self):
        from dataclasses import FrozenInstanceError
        from itertools import product
        app = self.app()
        for incomplete, truncated, unreliable in product((False, True), repeat=3):
            recognition = RecognitionResult('raw', .9, 'whisper',
                fragmented=incomplete or truncated or unreliable,
                incomplete=incomplete or unreliable, capture_truncated=truncated,
                utterance_incomplete=incomplete, recognition_unreliable=unreliable)
            turn = await app._enqueue_command('repaired', 'voice', .9, recognition=recognition)
            self.assertEqual(turn.transcript, 'raw')
            self.assertEqual(turn.text, 'repaired')
            self.assertEqual(turn.stt_engine, 'whisper')
            self.assertEqual(turn.utterance_incomplete, incomplete)
            self.assertEqual(turn.capture_truncated, truncated)
            self.assertEqual(turn.recognition_unreliable, unreliable)
            self.assertEqual(turn.action_eligible, not recognition.fragmented)
            self.assertEqual(turn.clarification_required, recognition.incomplete)
            self.assertEqual(turn.fragmented, recognition.fragmented)
            with self.assertRaises(FrozenInstanceError):
                turn.action_eligible = True
            # Mutating the STT result later cannot mutate the dispatched snapshot.
            recognition.text = 'late'
            self.assertEqual(turn.transcript, 'raw')

    async def test_positional_queue_items_are_not_a_runtime_compatibility_path(self):
        app = self.app()
        for size in (3, 4, 5, 6, 7):
            await app.command_queue.put(('legacy', 'voice', 1) + (None,) * (size - 3))
        task = asyncio.create_task(app._command_loop())
        try:
            await asyncio.wait_for(app.command_queue.join(), 1)
            app.processor.process.assert_not_awaited()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def test_console_binds_first_character_not_enter(self):
        from core.console import correlated_console_input
        current = ['A']
        def read(*args):
            current[0] = 'B'
            return 'так'
        with patch('core.console.os.name', 'nt'), patch('core.console.sys.stdin.isatty', return_value=True), \
                patch.dict(sys.modules, {'msvcrt': SimpleNamespace(kbhit=Mock(side_effect=[False, True]))}), \
                patch('core.console.console_print'), patch('core.console.time.sleep'), \
                patch('core.console.console_input', side_effect=read):
            self.assertEqual(correlated_console_input('>', lambda: current[0]), ('так', 'A'))

    def test_redirected_console_binds_before_blocking_read(self):
        from core.console import correlated_console_input
        current = ['A']
        def read(*args):
            current[0] = 'B'
            return 'так'
        with patch('core.console.sys.stdin.isatty', return_value=False), \
                patch('core.console.console_input', side_effect=read):
            self.assertEqual(correlated_console_input('>', lambda: current[0]), ('так', 'A'))

    async def test_voice_reply_keeps_id_from_before_stt(self):
        app = self.app()
        app.confirmation.awaiting = True
        app.confirmation.request_id = 'A'
        app.confirmation.submit.side_effect = lambda text, request_id, **kwargs: request_id == app.confirmation.request_id
        async def capture(*args):
            app.confirmation.request_id = 'B'
            app.running = False
            return RecognitionResult('так', .99)
        app._listen_for_turn = AsyncMock(side_effect=capture)
        await app._voice_loop()
        app.confirmation.submit.assert_called_once_with('так', 'A', confidence=.99)
        app.confirmation.wait_until_response_processed.assert_not_awaited()
        self.assertTrue(app.command_queue.empty())

    async def test_stale_text_cannot_become_command_or_new_confirmation(self):
        from core.confirmation import ConfirmationService
        app = self.app()
        app.confirmation = ConfirmationService(AsyncMock())
        task = asyncio.create_task(app.confirmation.ask('B'))
        try:
            await app.confirmation.wait_until_requested()
            await app._submit_text('так', 'old-A')
            self.assertTrue(app.confirmation._responses.empty())
            self.assertTrue(app.command_queue.empty())
            await app._submit_text('так', app.confirmation.request_id)
            self.assertTrue(await asyncio.wait_for(task, 1))
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_ui_valid_id_and_web_text_delivery_keep_correlation(self):
        import json
        from core.web_ui import LocalWebUI
        app = self.app()
        app.web_ui = Mock()
        app.confirmation.awaiting = True
        status, _ = await app.web_control('confirm', {'request_id': 'request', 'accept': True})
        self.assertEqual(status, 200)
        app.confirmation.submit.assert_called_once_with('так', 'request')
        app.confirmation.submit.reset_mock()
        status, _ = await app.web_control('confirm', {'request_id': 'request', 'accept': False})
        self.assertEqual(status, 200)
        app.confirmation.submit.assert_called_once_with('ні', 'request')
        receiver = SimpleNamespace(_submit_text=AsyncMock())
        ui = LocalWebUI(receiver)
        status, _ = await ui._api('POST', '/api/action', json.dumps(
            {'action': 'message', 'text': 'так', 'request_id': 'old-A'}).encode())
        self.assertEqual(status, 202)
        await ui.submission
        receiver._submit_text.assert_awaited_once_with('так', 'old-A')

    def test_dispatch_storage_is_bounded_and_retired_ids_never_return(self):
        from core.dispatch_guard import DispatchGuard
        guard = DispatchGuard(capacity=2)
        old, other = guard.issue(), guard.issue()
        with self.assertRaises(RuntimeError):
            guard.issue()
        self.assertTrue(guard.claim(old))
        for _ in range(1000):
            self.assertTrue(guard.claim(guard.issue()))
        self.assertFalse(guard.claim(old))
        self.assertEqual(guard.pending, {other})
        self.assertFalse(DispatchGuard().claim(other))

    async def test_cancelled_enqueue_retires_its_ticket(self):
        app = self.app()
        app.command_queue = asyncio.Queue(maxsize=1)
        await app._enqueue_command('first', 'text', 1)
        pending = asyncio.create_task(app._enqueue_command('second', 'text', 1))
        await asyncio.sleep(0)
        self.assertEqual(len(app._dispatch_guard.pending), 2)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertEqual(len(app._dispatch_guard.pending), 1)

    """Production loop contracts; fake capture/processor, no external actions."""

    def app(self):
        app = object.__new__(ValleRaApp)
        app.running = app.microphone_enabled = True
        app._microphone_epoch = 0
        app._mic_ready = asyncio.Event()
        app._mic_ready.set()
        app.command_idle = asyncio.Event()
        app.command_idle.set()
        app.command_queue = asyncio.Queue()
        app.web_ui = None
        app._tasks = []
        app.performance = DISABLED_PERFORMANCE
        app.settings = SimpleNamespace(stt_command_confidence_threshold=.55,
                                       stt_chat_confidence_threshold=.5, confirmation_timeout_seconds=15)
        app.services = {'state': {'mode': 'chat'}}
        app.speaker = SimpleNamespace(say=AsyncMock(), busy=False, generation=0)
        app.confirmation = SimpleNamespace(awaiting=False, request_id='request', ask=AsyncMock(), submit=Mock(return_value=True),
                                           wait_until_response_processed=AsyncMock())
        app._wait_for_input_slot = AsyncMock()
        app._wait_for_speaker = AsyncMock()

        async def process(*args, **kwargs):
            app.running = False
            return SkillResult(True)

        app.processor = SimpleNamespace(process=AsyncMock(side_effect=process))
        return app

    async def capture_once(self, app, result):
        async def capture(*args):
            app.running = False  # One acquisition, not an endless microphone loop.
            return result
        app._listen_for_turn = AsyncMock(side_effect=capture)
        await asyncio.wait_for(app._voice_loop(), 1)

    async def test_one_capture_enqueues_and_dispatches_once_preserving_guard(self):
        for fragmented, incomplete in ((False, False), (True, False), (True, True)):
            with self.subTest(fragmented=fragmented, incomplete=incomplete):
                app = self.app()
                timing = TurnTiming(DISABLED_PERFORMANCE)
                result = RecognitionResult('тестова репліка', .9, timing=timing,
                                           fragmented=fragmented, incomplete=incomplete)
                await self.capture_once(app, result)
                self.assertEqual(app.command_queue.qsize(), 1)
                item = app.command_queue.get_nowait()
                app.command_queue.task_done()
                self.assertIsInstance(item, TurnEnvelope)
                self.assertEqual(item.text, result.text)
                self.assertEqual(item.transcript, result.text)
                self.assertEqual(item.source, 'voice')
                self.assertEqual(item.stt_engine, 'vosk')
                self.assertEqual(item.confidence, .9)
                self.assertIs(item.timing, timing)
                self.assertEqual(item.action_eligible, not fragmented)
                self.assertEqual(item.utterance_incomplete, incomplete)
                self.assertIn(item.turn_id, app._dispatch_guard.pending)
                self.assertEqual(item.session_id, app._dispatch_guard.session_id)
                await app.command_queue.put(item)
                app.running = True
                await asyncio.wait_for(app._command_loop(), 1)
                app.processor.process.assert_awaited_once_with(item, app.confirmation.ask)
                self.assertIs(app.processor.process.await_args.args[0], item)
                self.assertTrue(app.command_queue.empty())
                await asyncio.wait_for(app.command_queue.join(), 1)

    async def test_empty_and_conflicting_stt_have_no_dispatch(self):
        for result in (RecognitionResult('', 0, 'interrupted'), RecognitionResult('дія', .9, 'conflict')):
            app = self.app()
            await self.capture_once(app, result)
            self.assertTrue(app.command_queue.empty())
            app.processor.process.assert_not_awaited()

    async def test_low_confidence_voice_never_reaches_processor(self):
        for confidence in (.1, float('nan')):
            app = self.app()
            await app._enqueue_command('відкрий браузер', 'voice', confidence)
            task = asyncio.create_task(app._command_loop())
            try:
                await asyncio.wait_for(app.command_queue.join(), 1)
                app.processor.process.assert_not_awaited()
                app.confirmation.ask.assert_not_awaited()
                app.speaker.say.assert_awaited_once()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_stale_ui_confirmation_id_cannot_approve_current_action(self):
        app = self.app()
        app.confirmation.awaiting = True
        app.confirmation.request_id = 'current'
        status, _ = await app.web_control('confirm', {'request_id': 'obsolete', 'accept': True})
        self.assertEqual(status, 409)
        app.confirmation.submit.assert_not_called()

    async def test_confirmation_voice_reply_is_not_a_command_dispatch(self):
        app = self.app()
        app.confirmation.awaiting = True
        await self.capture_once(app, RecognitionResult('ні', .9))
        app.confirmation.submit.assert_called_once_with('ні', 'request', confidence=.9)
        app.confirmation.wait_until_response_processed.assert_awaited_once()
        self.assertTrue(app.command_queue.empty())
        app.processor.process.assert_not_awaited()

    async def test_enqueue_preserves_all_existing_guard_arguments(self):
        for fragmented, incomplete in ((False, False), (True, False), (True, True)):
            app = self.app()
            turn = await app._enqueue_command('fixture', 'voice', 1.0,
                recognition=RecognitionResult('fixture', 1, fragmented=fragmented, incomplete=incomplete))
            await asyncio.wait_for(app._command_loop(), 1)
            received = app.processor.process.await_args.args[0]
            self.assertEqual(received.turn_id, turn.turn_id)
            self.assertEqual(received.action_eligible, not fragmented)
            self.assertEqual(received.clarification_required, incomplete)

    async def test_stale_capture_is_discarded_before_new_turn(self):
        app = self.app()
        entered, release = threading.Event(), threading.Event()

        def capture(*args):
            entered.set()
            release.wait(2)
            return RecognitionResult('stale action', 1)

        app.listener = SimpleNamespace(listen_once=capture, interrupt=Mock())
        old = asyncio.create_task(app._listen_for_turn(15, None))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            app._microphone_epoch += 1
            release.set()
            result = await asyncio.wait_for(old, 1)
            self.assertEqual(result.text, '')
            self.assertFalse(app._voice_inflight)
            await self.capture_once(app, result)
            self.assertTrue(app.command_queue.empty())
            app.running = True
            await self.capture_once(app, RecognitionResult('new turn', .9))
            app.running = True
            await asyncio.wait_for(app._command_loop(), 1)
            app.processor.process.assert_awaited_once()
            self.assertEqual(app.processor.process.await_args.args[0].text, 'new turn')
        finally:
            release.set()
            if not old.done():
                old.cancel()
            await asyncio.gather(old, return_exceptions=True)

    async def test_duplicate_operational_turn_dispatches_once(self):
        app = self.app()
        app.processor.process.side_effect = None
        app.processor.process.return_value = SkillResult(True)
        await app._enqueue_command('fixture', 'voice', .9)
        item = app.command_queue.get_nowait()
        app.command_queue.task_done()
        await app.command_queue.put(item)
        await app.command_queue.put(item)
        task = asyncio.create_task(app._command_loop())
        try:
            await asyncio.wait_for(app.command_queue.join(), 1)
            app.processor.process.assert_awaited_once()
            self.assertFalse(app._dispatch_guard.pending)
            # Retired IDs stay invalid even after many newer turns; no LRU expiry.
            for _ in range(100):
                await app._enqueue_command('fixture', 'voice', .9)
                await asyncio.wait_for(app.command_queue.join(), 1)
            await app.command_queue.put(item)
            await asyncio.wait_for(app.command_queue.join(), 1)
            self.assertEqual(app.processor.process.await_count, 101)
            self.assertFalse(app._dispatch_guard.pending)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class SpeakerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_app_cancellation_closes_native_workers(self):
        speaker = self.speaker()
        worker = ProcessWhisperRecognizer(speaker.settings, worker_target=slow_worker)
        app = object.__new__(ValleRaApp)
        app.settings = speaker.settings
        app.web_ui = None
        app.running = True
        app.text_only = True
        app.speaker = speaker
        app.listener = SimpleNamespace(interrupt=Mock(), close=worker.close)
        app.llm = SimpleNamespace(provisional_statuses=Mock(return_value={}), select_startup_provider=Mock(), close=AsyncMock())
        app.metrics = SimpleNamespace(record=Mock())
        app.services = {"pentest": SimpleNamespace(deactivate=AsyncMock())}

        async def idle():
            await asyncio.Event().wait()

        async def initialize():
            await asyncio.to_thread(worker.prepare)

        app._text_input_loop = app._command_loop = app._reminder_loop = idle
        app._initialize_backends = initialize
        task = asyncio.create_task(app.run())
        try:
            async with asyncio.timeout(3):
                while worker._process is None:
                    await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            self.assertIsNone(worker._process)
            self.assertTrue(speaker._worker_task.done())
            app.llm.close.assert_awaited_once()
        finally:
            worker.close()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    def test_default_windows_output_uses_direct_speech_without_wav(self):
        speaker = self.speaker()
        with (
            patch("core.speak.os.name", "nt"),
            patch.object(speaker, "_generate_with_windows_speech") as synth,
        ):
            speaker._speak_sync("Привіт")
        synth.assert_called_once_with("Привіт", None)

    def speaker(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return Speaker(Settings(ProjectPaths.from_root(Path(temp.name))))

    async def test_known_answer_is_queued_in_phrases(self):
        speaker = self.speaker()
        await speaker.say("Перша фраза. Друга фраза.")
        self.assertEqual(speaker._queue.get_nowait().text, "Перша фраза.")
        self.assertEqual(speaker._queue.get_nowait().text, "Друга фраза.")

    async def test_close_terminates_owned_tts_child_and_drops_pending_audio(self):
        speaker = self.speaker()
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        speaker._tts_process = process
        try:
            await speaker.say("Цю репліку не треба відтворювати.")
            await speaker.start()
            await asyncio.wait_for(speaker.close(), 3)
            process.wait(timeout=2)
            self.assertTrue(speaker._worker_task.done())
            self.assertTrue(speaker._queue.empty())
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()

    async def test_text_reply_starting_during_capture_discards_audio(self):
        app = object.__new__(ValleRaApp)
        app.microphone_enabled = True
        app._microphone_epoch = 0
        finished = threading.Event()
        app.listener = SimpleNamespace(
            listen_once=lambda *_: finished.wait(2) and RecognitionResult("self echo", 1),
            interrupt=Mock(side_effect=finished.set),
        )
        app.speaker = SimpleNamespace(busy=False, say=AsyncMock())
        app.command_idle = asyncio.Event()
        app.command_idle.set()
        task = asyncio.create_task(app._listen_for_turn(10, None))
        await asyncio.sleep(0.02)
        app.speaker.busy = True
        result = await asyncio.wait_for(task, 1)
        self.assertEqual(result.text, "")
        app.listener.interrupt.assert_called_once()
