import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from core.local_action_resolver import resolve_exact_app


class ExactAppResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_catalogue_match_preserves_target(self):
        lookup = Mock(return_value=True)
        intent = await resolve_exact_app('Відкрий Telegram', lookup, apps_enabled=True)
        self.assertEqual(intent.tool, 'open_app')
        self.assertEqual(intent.arguments, {'name': 'Telegram'})
        lookup.assert_called_once_with('Telegram')

    async def test_disabled_and_non_requests_do_not_lookup(self):
        lookup = Mock(return_value=True)
        for text, enabled in [('Відкрий Telegram', False), ('Не відкрий Telegram', True),
                              ('Я відкрив Telegram', True), ('Привіт', True)]:
            self.assertIsNone(await resolve_exact_app(text, lookup, apps_enabled=enabled))
        lookup.assert_not_called()

    async def test_only_literal_true_is_authoritative(self):
        for result in (False, None, 1, 'yes', Mock()):
            self.assertIsNone(await resolve_exact_app(
                'Відкрий Telegram', Mock(return_value=result), apps_enabled=True))
        self.assertIsNone(await resolve_exact_app('Відкрий Telegram', None, apps_enabled=True))

    async def test_lookup_failures_do_not_create_action(self):
        for error in (OSError(), ValueError(), TimeoutError()):
            self.assertIsNone(await resolve_exact_app(
                'Відкрий Telegram', Mock(side_effect=error), apps_enabled=True))

    async def test_cancellation_is_not_swallowed(self):
        with patch('core.local_action_resolver.asyncio.to_thread',
                   AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await resolve_exact_app('Відкрий Telegram', Mock(), apps_enabled=True)
