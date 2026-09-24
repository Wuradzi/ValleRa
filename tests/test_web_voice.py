import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from core.command_router import CommandRouter
from core.processor import CommandProcessor
from core.skill_loader import LoadedSkill
from core.voice_text import repair_voice_text
from services.web.intents import weather_request
from services.web.search import WebSearchService
from services.web.weather import WeatherService
from skills.web import skill


WEATHER = {
    "current_condition": [
        {"temp_C": "19", "FeelsLikeC": "18", "humidity": "60", "lang_uk": [{"value": "Хмарно"}]}
    ],
    "weather": [
        {"date": "2026-09-03", "mintempC": "12", "maxtempC": "20"},
        {"date": "2026-09-04", "mintempC": "10", "maxtempC": "21"},
    ],
}


class WeatherVoiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.llm = SimpleNamespace(active_name="gemini", chat=AsyncMock(return_value="Розмова"))
        self.weather = SimpleNamespace(
            get=AsyncMock(return_value=WEATHER), summarize=WeatherService.summarize
        )
        self.web = SimpleNamespace(search=AsyncMock(return_value=[]))
        self.services = {
            "state": {"mode": "chat"},
            "weather": self.weather,
            "web": self.web,
            "memory": SimpleNamespace(relevant=Mock(return_value=[])),
        }
        router = CommandRouter(
            [LoadedSkill("web", "", ["знайди", "погода"], ["all"], skill, self.services)]
        )
        self.processor = CommandProcessor(
            SimpleNamespace(),
            router,
            self.llm,
            SimpleNamespace(say=AsyncMock()),
            SimpleNamespace(record=Mock()),
            self.services,
        )

    async def test_observed_voice_and_typed_forms_reach_weather_not_search(self):
        phrases = [
            "Команда знайдеє походу в Лучку.",
            "Команда, знайди погоду влучку.",
            "Команда знайди в інтернеті прогноз погоди в місті Луцьк",
            "Команда: яка погода в м. Луцьк?",
            "Команда: покажи погоду в Луцьку",
        ]
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                result = await self.processor.process(
                    repair_voice_text(phrase), AsyncMock(), "voice"
                )
                self.assertEqual(result.data["command_type"], "weather")
                self.assertIn("Луцьк", result.response)
                self.weather.get.assert_awaited_with("Луцьк")
        self.web.search.assert_not_awaited()
        self.llm.chat.assert_not_awaited()

    async def test_unprefixed_weather_stays_conversation(self):
        await self.processor.process("погода в Луцьку", AsyncMock(), "voice")
        self.llm.chat.assert_awaited_once()
        self.weather.get.assert_not_awaited()

    async def test_missing_city_does_not_guess_or_request_network(self):
        result = await self.processor.process("Команда: знайди погоду", AsyncMock())
        self.assertEqual(result.data["command_type"], "weather_missing_city")
        self.weather.get.assert_not_awaited()

    async def test_tomorrow_is_not_current_weather(self):
        result = await self.processor.process(
            "Команда: прогноз погоди в м. Луцьк на завтра", AsyncMock()
        )
        self.assertIn("2026-09-04", result.response)
        self.assertIn("від 10 до 21", result.response)
        self.assertNotIn("зараз", result.response)

    async def test_weather_error_does_not_fall_back_to_search(self):
        self.weather.get.side_effect = TimeoutError()
        with self.assertLogs("skills.web.skill", level="ERROR"):
            result = await self.processor.process("Команда: погода в Луцьку", AsyncMock())
        self.assertEqual(result.data["command_type"], "weather_error")
        self.web.search.assert_not_awaited()

    def test_unknown_city_is_not_fuzzy_rewritten(self):
        self.assertEqual(weather_request("погода в Новому Місті").city, "Новому Місті")
        self.assertIsNone(weather_request("що таке погода"))

    async def test_unsupported_forecast_asks_instead_of_giving_current(self):
        result = await self.processor.process("Команда: погода в Луцьку на тиждень", AsyncMock())
        self.assertIn("Уточніть період", result.response)
        self.weather.get.assert_not_awaited()


class SearchOutputTests(unittest.TestCase):
    def test_web_does_not_fall_back_for_local_files_or_secrets(self):
        self.assertFalse(skill.can_handle("знайди файл з назвою private", {}))
        self.assertFalse(skill.can_handle("знайди пароль від github", {}))
        self.assertFalse(skill.can_handle("знайди C:\\private\\note.txt", {}))
        self.assertTrue(skill.can_handle("знайди в інтернеті документацію python", {}))

    def test_long_search_snippets_are_bounded_and_links_preserved(self):
        rows = WebSearchService.clean_results(
            [
                {
                    "title": "<b>Ресурс</b>",
                    "body": "Довгий уривок. " * 200,
                    "href": "https://example.com/info",
                },
                {"title": "duplicate", "href": "https://example.com/info"},
                {"title": "unsafe", "href": "javascript:alert(1)"},
                {"title": "Наступний", "body": "Опис", "href": "https://example.org"},
            ]
        )
        self.assertEqual(len(rows), 2)
        self.assertLessEqual(len(rows[0]["body"]), 221)
        self.assertEqual(rows[0]["title"], "Ресурс")
        self.assertEqual(rows[0]["href"], "https://example.com/info")
        answer = WebSearchService.summarize(rows)
        self.assertLess(len(answer), 450)
        self.assertIn("уривок пошуку", answer)
        self.assertNotIn("Наступний", answer)
