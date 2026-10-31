"""Contextual understanding fixes from the live session of 30.10.2026.

A conversational lead-in must not hide a direct request, a search query keeps
the user's words without «інформацію про», and an affirmative reply to Raven's
own plain-CHAT offer reaches the executor's confirmation with exact arguments.
"""
import unittest
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.action_policy import accepts_assistant_offer
from core.command_intent import CommandIntent
from core.models import SkillResult, TurnEnvelope
from core.natural_turn import NaturalTurn, final_request
from services.web.intents import search_query
from services.web.answers import WebAnswerService
from tests.processor_gate_matrix import build

OFFER = ("Рісорджіменто — це період об'єднання Італії. "
         "Розкажи, чи потрібно знайти додаткову інформацію про нього в інтернеті.")


def turn(text, source="text"):
    return TurnEnvelope(turn_id="context", session_id="context", source=source, text=text,
                        transcript=text, stt_engine="text", confidence=1.0)


class FinalRequestTests(unittest.TestCase):
    def test_conversational_lead_in_does_not_hide_request(self):
        self.assertEqual(
            final_request("Щоб тобі таке сказати, я навіть не знаю. Знайди мені інформацію про катану."),
            "Знайди мені інформацію про катану.")
        self.assertEqual(final_request("Привіт. Відкрий браузер."), "Відкрий браузер.")

    def test_risky_lead_in_keeps_whole_text(self):
        for text in ("Не відкривай браузер. Відкрий блокнот.", "Якщо буде час. Відкрий браузер.",
                     "Він сказав. Відкрий браузер.", "Мій пароль 1234. Відкрий браузер.",
                     "Я шукав це вчора. Знайди погоду.", "Добре. Не відкривай браузер.",
                     "Скажи «так». Відкрий браузер.", "Відкрий браузер"):
            with self.subTest(text=text):
                self.assertEqual(final_request(text), text)


class SearchQueryTests(unittest.TestCase):
    def test_information_phrase_and_trailing_punctuation_are_removed(self):
        self.assertEqual(search_query("знайди інформацію про ресорджменту."), "ресорджменту")
        self.assertEqual(search_query("знайди мені детальну інформацію про катану в інтернеті."), "катану")
        self.assertEqual(search_query("знайди мені інформацію про реставрацію Майджі."), "реставрацію Майджі")

    def test_user_words_are_not_rewritten(self):
        self.assertEqual(search_query("знайди рецепт без цукру"), "рецепт без цукру")
        self.assertEqual(search_query("знайди інформацію"), "інформацію")
        self.assertEqual(search_query("знайди дані про населення Луцька"), "дані про населення Луцька")


class AssistantOfferTests(unittest.TestCase):
    def test_affirmative_reply_to_matching_offer(self):
        for reply in ("Так, треба знайти додаткову інформацію.", "Так", "Давай", "Рейвен, так, знайди"):
            with self.subTest(reply=reply):
                self.assertTrue(accepts_assistant_offer(OFFER, reply, "web_search"))
        self.assertTrue(accepts_assistant_offer("Хочеш, я знайду відгуки?", "Так", "web_search"))

    def test_no_offer_or_refusal_never_accepts(self):
        cases = (
            (OFFER, "Так, але не зараз", "web_search"),
            (OFFER, "Ні", "web_search"),
            (OFFER, "Так?", "web_search"),
            (OFFER, "Так, відкрий браузер і знайди погоду", "web_search"),
            (OFFER, "Таксі", "web_search"),
            (OFFER, "Так", "open_app"),                     # offer was about search
            ("Яку програму відкрити?", "так", "open_app"),   # parameter question
            ("Тобі подобається музика?", "Так", "web_search"),
            ("Чи знайти це? Чим ще допомогти?", "Так", "web_search"),  # last sentence decides
            (OFFER, "Так, мій пароль 1234", "web_search"),
            (None, "Так", "web_search"),
            (OFFER, "Так", "window_control"),               # no exact-argument confirmation
        )
        for offer, reply, tool in cases:
            with self.subTest(offer=offer, reply=reply, tool=tool):
                self.assertFalse(accepts_assistant_offer(offer, reply, tool))


class ProcessorContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.processor, _router, self.llm, _memory, _speaker = build("chat", True)
        self.processor.services["enabled_skills"] |= {"web"}
        self.execute = AsyncMock(return_value=SkillResult(True, "знайдено", {"command_type": "web_search"}))
        patcher = patch("core.processor.execute_intent", self.execute)
        patcher.start()
        self.addCleanup(patcher.stop)

    def model_action(self, tool, **arguments):
        self.llm.converse = AsyncMock(return_value=NaturalTurn("action", intent=CommandIntent(tool, arguments)))

    async def test_failed_search_context_reaches_natural_turn_without_execution(self):
        web = WebAnswerService(self.llm)
        web.remember_lookup('кулькова ручка', SkillResult(True, 'Недостатньо даних.', {
            'command_type': 'web_answer', 'sources_read': 1,
            'coverage': {'status': 'insufficient', 'missing': ['визначення']},
            'summary_failure': 'insufficient_evidence',
            'web_results': [{'title': 'Нерелевантна сторінка'}]}))
        self.processor.services['web_answers'] = web
        self.llm.converse = AsyncMock(return_value=NaturalTurn('chat', 'Причину не встановлено.'))
        await self.processor.process(turn('Чому ти знайшов іншу тему?'), AsyncMock())
        context = self.llm.converse.await_args.kwargs['web_context']
        self.assertEqual(context['status'], 'insufficient_answer')
        self.assertEqual(context['source_titles'], ['Нерелевантна сторінка'])
        self.assertEqual(context['failure_reason'], 'insufficient_evidence')
        self.execute.assert_not_awaited()

    async def test_yes_to_own_offer_reaches_confirmation_with_model_arguments(self):
        self.processor._last_reply = OFFER
        self.model_action("web_search", query="Рісорджіменто")
        result = await self.processor.process(turn("Так, треба знайти додаткову інформацію."), AsyncMock())
        self.execute.assert_awaited_once()
        self.assertEqual(self.execute.await_args.args[0].arguments, {"query": "Рісорджіменто"})
        self.assertEqual(result.data["intent_route"], "assistant_offer")

    async def test_yes_without_offer_stays_a_clarification(self):
        self.processor._last_reply = "Музика — це мистецтво звуків. Тобі подобається джаз?"
        self.model_action("web_search", query="джаз")
        result = await self.processor.process(turn("Так, треба знайти додаткову інформацію."), AsyncMock())
        self.execute.assert_not_awaited()
        self.assertEqual(result.data["command_type"], "intent_clarification")

    async def test_lead_in_request_binds_query_to_user_words(self):
        self.model_action("web_search", query="катана")
        await self.processor.process(
            turn("Щоб тобі таке сказати, я навіть не знаю. Знайди мені інформацію про катану.", "voice"),
            AsyncMock())
        self.execute.assert_awaited_once()
        self.assertEqual(self.execute.await_args.args[0].arguments, {"query": "катану"})

    async def test_negated_lead_in_still_blocks_action(self):
        self.model_action("open_app", name="блокнот")
        result = await self.processor.process(turn("Не відкривай браузер. Відкрий блокнот."), AsyncMock())
        self.execute.assert_not_awaited()
        self.assertEqual(result.data["command_type"], "intent_clarification")


class WebFailureContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = 10
        self.page = {'href': 'https://example.com/page', 'title': 'Інша тема',
                     'text': 'RAW PAGE BODY ' * 20, 'published': None, 'retrieved': '2026-10-30'}
        self.llm = SimpleNamespace(summarize_web=AsyncMock(return_value=json.dumps({
            'claims': [], 'coverage': {'status': 'insufficient', 'missing': ['визначення']},
            'caveat': 'Прочитані сторінки описують іншу тему.'})))
        self.reader = SimpleNamespace(read=AsyncMock(return_value=self.page))
        self.web = WebAnswerService(self.llm, self.reader, clock=lambda: self.clock)
        self.rows = [{'href': self.page['href'], 'title': 'Результат', 'body': 'RAW SNIPPET'}]

    async def test_insufficient_summary_keeps_titles_assessment_and_known_failure(self):
        await self.web.answer('кулькова ручка', self.rows)
        context = self.web.dialogue_context()
        self.assertEqual(context['status'], 'insufficient_answer')
        self.assertEqual(context['summary'], '')
        self.assertEqual(context['coverage'], {'status': 'insufficient', 'missing': ['визначення']})
        self.assertEqual(context['caveat'], 'Прочитані сторінки описують іншу тему.')
        self.assertEqual(context['source_titles'], ['Інша тема'])
        self.assertEqual(context['failure_reason'], 'insufficient_evidence')
        self.assertNotIn('RAW', json.dumps(context))

    async def test_generation_failure_is_not_mislabeled_as_irrelevance(self):
        self.llm.summarize_web.side_effect = TimeoutError()
        await self.web.answer('питання', self.rows)
        context = self.web.dialogue_context()
        self.assertEqual(context['failure_reason'], 'request_failed')
        self.assertEqual(context['coverage']['status'], 'unassessed')
        self.assertEqual(context['caveat'], '')
        self.assertEqual(context['source_titles'], ['Інша тема'])

    async def test_expiry_replacement_and_clear_do_not_leak_old_titles(self):
        await self.web.answer('питання', self.rows)
        self.clock += 301
        self.assertNotIn('source_titles', self.web.dialogue_context())
        self.web.remember_lookup('нове питання', SkillResult(True, 'Не знайдено', {
            'command_type': 'web_search_error', 'search_failure': 'timeout'}))
        context = self.web.dialogue_context()
        self.assertEqual(context['source_titles'], [])
        self.assertEqual(context['failure_reason'], 'timeout')
        self.web.clear()
        self.assertIsNone(self.web.dialogue_context())

    def test_title_metadata_is_bounded(self):
        self.web.remember_lookup('питання', SkillResult(True, 'Знайдено', {
            'resources_only': True, 'web_results': [{'title': 'X' * 400}] * 20}))
        titles = self.web.dialogue_context()['source_titles']
        self.assertEqual(len(titles), 5)
        self.assertTrue(all(len(title) == 200 for title in titles))


if __name__ == "__main__":
    unittest.main()
