import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from core.app import ValleRaApp

from services.files.file_service import FileService
from skills.files.skill import find_files, handle


class SearchCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_ui_cancel_is_correlated_and_confirmation_remains_prioritized(self):
        app = ValleRaApp.__new__(ValleRaApp)
        files = FileService([])
        app.services = {'files': files}
        app.web_ui = Mock()
        app.confirmation = SimpleNamespace(awaiting=True, request_id='confirmation', submit=Mock())
        with files._progress_lock:
            files._progress = dict(id='search-B', active=True, stage='searching')
        status, _ = await app.web_control('cancel_search', {'task_id': 'search-A'})
        self.assertEqual(status, 409)
        self.assertFalse(files._cancel_search.is_set())
        await app._submit_text('скасуй пошук', 'confirmation')
        app.confirmation.submit.assert_called_once_with('скасуй пошук', 'confirmation')
        self.assertFalse(files._cancel_search.is_set())
        status, _ = await app.web_control('cancel_search', {'task_id': 'search-B'})
        self.assertEqual(status, 200)
        self.assertTrue(files._cancel_search.is_set())

    async def test_cancel_discards_late_result_and_next_search_works(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'example.txt'
            path.write_text('test', encoding='utf-8')
            files = FileService([directory])
            entered, release = threading.Event(), threading.Event()
            original_walk = files._walk

            def slow_walk():
                entered.set()
                release.wait(3)
                yield path

            files._walk = slow_walk
            tasks = Mock()
            context = SimpleNamespace(services={'files': files, 'tasks': tasks})
            job = asyncio.create_task(find_files('example', '', context))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                snapshot = files.search_snapshot()
                self.assertTrue(snapshot['active'])
                self.assertFalse(files.cancel_search('stale-id'))
                self.assertTrue(files.cancel_search(snapshot['id']))
                self.assertEqual(files.search_snapshot()['stage'], 'cancelling')
                release.set()
                result = await asyncio.wait_for(job, 3)
                self.assertEqual(result.data['status'], 'cancelled')
                tasks.offer.assert_not_called()
                self.assertEqual(files.search_snapshot()['stage'], 'cancelled')
                self.assertFalse(files.cancel_search(snapshot['id']))
                self.assertFalse(files.search_status['resumable'])
                files._walk = original_walk
                self.assertEqual(files.search('example'), [path.resolve()])
                self.assertNotEqual(files.search_snapshot()['id'], snapshot['id'])
                self.assertEqual(files.search_snapshot()['stage'], 'completed')
                self.assertNotIn('roots', files.search_snapshot())
            finally:
                release.set()
                await asyncio.gather(job, return_exceptions=True)
                files.close()

    async def test_cancelled_open_or_delete_never_reaches_executor(self):
        for command in ('відкрий файл example', 'видали файл example', 'знайди найбільші файли'):
            files = Mock(search_status={'cancelled': True})
            files.search.return_value = [Path('example.txt')]
            files.largest.return_value = [Path('example.txt')]
            tasks = Mock()
            result = await handle(command, SimpleNamespace(raw_text=command), {'files': files, 'tasks': tasks})
            self.assertEqual(result.data['status'], 'cancelled')
            tasks.offer.assert_not_called()
            tasks.open_file.assert_not_called()
            files.open.assert_not_called()
            files.delete_to_trash.assert_not_called()

    async def test_worker_failure_finishes_progress(self):
        files = FileService([])
        def broken():
            raise RuntimeError('fixture')
            yield
        files._walk = broken
        with self.assertRaises(RuntimeError):
            files.search()
        self.assertEqual(files.search_snapshot()['stage'], 'failed')
        self.assertFalse(files.search_snapshot()['active'])
