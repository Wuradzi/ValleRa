"""Deterministic voice-boundary acceptance; NOT acoustic accuracy measurements."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from core.confirmation import ConfirmationService
from core.models import RecognitionResult
from core.voice_diagnostics import backend_failure_reason, recognition_reason


class VoiceAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmation_matrix_never_authorizes_ambiguous_audio(self):
        base = RecognitionResult('так', 0, 'groq', confidence_available=False, fragmented=True)
        cases = [
            (base, True), (replace(base, text='відміна'), False),
            (replace(base, text='ні'), False), (replace(base, text='так ні'), None),
            (replace(base, text=''), None), (replace(base, text='Дякую за перегляд!'), None),
            (replace(base, capture_truncated=True), None),
            (replace(base, recognition_unreliable=True), None),
            (replace(base, utterance_incomplete=True), None),
            (replace(base, engine='interrupted', text='', confidence=0), None),
        ]
        for reply, expected in cases:
            with self.subTest(text=reply.text, expected=expected):
                service = ConfirmationService(AsyncMock(), timeout_seconds=2)
                task = asyncio.create_task(service.ask('Test action'))
                try:
                    await service.wait_until_requested()
                    score = service.voice_confidence(reply)
                    self.assertIs(service._decision(reply.text, score), expected)
                    self.assertFalse(service.submit(reply.text, 'stale', confidence=score))
                    service.submit(reply.text, service.request_id, confidence=score)
                    await asyncio.wait_for(service.wait_until_response_processed(), 1)
                    if expected is None:
                        self.assertFalse(task.done())
                        service.submit('відміна', service.request_id)
                        self.assertFalse(await task)
                    else:
                        self.assertIs(await task, expected)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    def test_diagnostics_distinguish_silence_failure_truncation_and_unscored(self):
        base = RecognitionResult('Тест', 0, 'groq', confidence_available=False, fragmented=True)
        for reply, expected in [
            (base, 'recognized_unscored'), (replace(base, text=''), 'empty_transcript'),
            (replace(base, recognition_unreliable=True), 'recognition_unreliable'),
            (replace(base, capture_truncated=True), 'capture_truncated'),
            (replace(base, utterance_incomplete=True), 'utterance_incomplete'),
            (replace(base, engine='interrupted'), 'interrupted'),
        ]:
            self.assertEqual(recognition_reason(reply), expected)

    def test_backend_diagnostics_never_leak_text_or_previous_job_error(self):
        backend = SimpleNamespace(last_error='private exception message')
        self.assertEqual(backend_failure_reason('done', backend), 'empty_or_unreliable')
        backend.last_error = 'non_ukrainian_transcript'
        self.assertEqual(backend_failure_reason('done', backend), 'non_ukrainian_transcript')
        self.assertEqual(backend_failure_reason('timeout', backend), 'timeout')
