import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from services.llm.base import ProviderStatus
from services.llm.manager import LLMManager


class LLMStartupSelectionTests(unittest.IsolatedAsyncioTestCase):
    def _manager(self):
        manager = object.__new__(LLMManager)
        manager.settings = SimpleNamespace(
            llm_order=["gemini", "groq", "ollama"],
        )
        manager.providers = {
            "gemini": SimpleNamespace(api_key="configured", model="gemini-model"),
            "groq": SimpleNamespace(api_key="", model="groq-model"),
            "ollama": SimpleNamespace(api_key="", model=""),
        }
        manager.available_order = []
        manager.active_name = None
        manager._cooldowns = {}
        return manager

    def test_timed_out_configured_provider_remains_lazy_candidate(self):
        manager = self._manager()
        statuses = {
            "gemini": ProviderStatus("gemini", False, "timeout"),
            "groq": ProviderStatus("groq", False, "API-ключ відсутній"),
            "ollama": ProviderStatus("ollama", False, "not running"),
        }

        with patch("builtins.print"):
            manager.select_startup_provider(statuses)

        self.assertEqual(manager.available_order, ["gemini"])
        self.assertEqual(manager.active_name, "gemini")

    def test_confirmed_provider_precedes_unconfirmed_provider(self):
        manager = self._manager()
        manager.providers["groq"].api_key = "configured"
        statuses = {
            "gemini": ProviderStatus("gemini", False, "timeout"),
            "groq": ProviderStatus("groq", True, "groq-model"),
            "ollama": ProviderStatus("ollama", False, "not running"),
        }

        with patch("builtins.print"):
            manager.select_startup_provider(statuses)

        self.assertEqual(manager.available_order, ["groq", "gemini"])
        self.assertEqual(manager.active_name, "groq")

    async def test_healthcheck_skips_unconfigured_providers(self):
        manager = self._manager()
        manager.providers["gemini"].healthcheck = AsyncMock(
            return_value=ProviderStatus("gemini", True, "gemini-model")
        )
        manager.providers["groq"].healthcheck = AsyncMock()
        manager.providers["ollama"].healthcheck = AsyncMock()

        statuses = await manager.check_all()

        self.assertTrue(statuses["gemini"].available)
        manager.providers["gemini"].healthcheck.assert_awaited_once_with()
        manager.providers["groq"].healthcheck.assert_not_awaited()
        manager.providers["ollama"].healthcheck.assert_not_awaited()

