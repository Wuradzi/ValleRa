import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

from core.task_progress import TaskProgress, TaskProgressRegistry, file_search_progress, workplace_progress


class TaskProgressTests(unittest.TestCase):
    def task(self, kind='example', active=True, started=1):
        return TaskProgress('id', kind, 'Task', 'running', '', active, False, 'Between steps', started)

    def test_new_kind_uses_same_contract_and_correlated_cancellation(self):
        registry = TaskProgressRegistry()
        task = self.task()
        cancel = Mock(return_value=True)
        registry.register('example', lambda: task, cancel)
        self.assertEqual(registry.current().public()['kind'], 'example')
        self.assertFalse(registry.cancel('example', 'old'))
        self.assertFalse(registry.cancel('other', 'id'))
        cancel.assert_not_called()
        self.assertTrue(registry.cancel('example', 'id'))
        cancel.assert_called_once_with('id')
        task = replace(task, active=False)
        self.assertFalse(registry.cancel('example', 'id'))
        with self.assertRaises(ValueError):
            registry.register('example', lambda: task, cancel)

    def test_active_then_most_recent_terminal_without_history(self):
        registry = TaskProgressRegistry()
        first = self.task('first', True, 1)
        registry.register('first', lambda: first, Mock())
        registry.register('second', lambda: self.task('second', False, 2), Mock())
        self.assertEqual(registry.current().kind, 'first')
        first = replace(first, active=False)
        self.assertEqual(registry.current().kind, 'second')

    def test_executor_adapters_keep_results_and_cancel_semantics(self):
        search = SimpleNamespace(search_snapshot=lambda: dict(id='s',stage='partial',active=False,
            cancel_requested=False,checked=12,matches=3,started_at=4))
        result = file_search_progress(search)
        self.assertEqual(result.status, 'partial')
        self.assertIn('12', result.detail)
        workplace = SimpleNamespace(snapshot=lambda: dict(id='w',status='running',title='Apps',detail='verifying',
            active=True,cancel_requested=True,steps=[{'name':'App','status':'verifying','detail':''}]))
        result = workplace_progress(workplace)
        self.assertEqual(result.status, 'cancelling')
        self.assertEqual(result.steps[0]['status'], 'verifying')
        self.assertIn('не закриються', result.cancellation)
