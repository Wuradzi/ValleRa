"""Knowledge gap → offered web search (live test 31.10.2026, E252 follow-up).

The model must not invent facts: it returns LOOKUP (or a non-direct ACTION
web_search), Raven says it does not know for sure and the executor asks the
usual exact-query confirmation. Only web_search, never other tools, never with
secrets, a refusal of search or several actions. Both input paths are covered:
scored text and unscored Groq voice (the path used by the live session).
"""
import unittest
from unittest.mock import AsyncMock, patch

from core.action_policy import (KNOWLEDGE_GAP_NOTICE, PolicyOutcome, knowledge_gap_fallback,
                                knowledge_gap_permission, natural_action_permission)
from core.command_intent import CommandIntent, InvalidIntent
from core.dialogue_decision import DecisionKind, DialogueDecision
from core.models import SkillResult, TurnEnvelope
from core.natural_turn import NaturalTurn, TurnDecoder
from tests.processor_gate_matrix import build

OFFER = "Рісорджіменто — це період об'єднання Італії. Чи потрібно знайти додаткову інформацію в інтернеті?"


def decode(output):
    decoder = TurnDecoder()
    decoder.feed(output)
    return decoder.finish()


def lookup(query):
    return DialogueDecision(DecisionKind.KNOWLEDGE_GAP, intent=CommandIntent("web_search", {"query": query}),
                            turn_id="t")


class DecoderTests(unittest.TestCase):
    def test_lookup_is_data_only_web_search(self):
        turn = decode("LOOKUP\nреставрація Мейдзі\n")
        self.assertEqual(turn.kind, "lookup")
        self.assertEqual(turn.intent, CommandIntent("web_search", {"query": "реставрація Мейдзі"}))

    def test_invalid_or_mixed_lookup_is_rejected(self):
        for output in ("LOOKUP\nперший рядок\nдругий рядок", "LOOKUP\n{\"tool\": \"open_app\"}",
                       "LOOKUP\n" + "x" * 201, "LOOKUP\n   ", "Я подумаю. LOOKUP реставрація",
                       "CHAT\nЗараз зроблю LOOKUP реставрація"):
            with self.subTest(output=output[:40]):
                with self.assertRaises(InvalidIntent):
                    decode(output)


class KnowledgeGapPolicyTests(unittest.TestCase):
    def test_information_request_is_offered_as_search(self):
        for text in ("Розкажи мені коротко про реставрацію Майджі.", "Хто такий Гарібальді?",
                     "Що сталося в Луцьку 1429 року"):
            with self.subTest(text=text):
                permission = knowledge_gap_permission(lookup("тема"), text)
                self.assertIs(permission.outcome, PolicyOutcome.ALLOW)
                self.assertEqual(permission.route, "knowledge_gap")
                self.assertEqual(permission.intent.arguments, {"query": "тема"})

    def test_no_search_without_information_request_or_with_refusal_or_secrets(self):
        cases = (
            ("Я працював у браузері", "браузер"),
            ("так", "реставрація"),
            ("Не шукай в інтернеті, що таке Мейдзі?", "Мейдзі"),
            ("Розкажи без пошуку, хто такий Гарібальді", "Гарібальді"),
            ("Що за пароль 1234 у мене був?", "пароль 1234"),
            ("Хто такий Гарібальді?", "мій пароль від пошти"),
            ("Знайди погоду і відкрий браузер?", "погода"),
        )
        for text, query in cases:
            with self.subTest(text=text, query=query):
                permission = knowledge_gap_permission(lookup(query), text)
                self.assertIsNot(permission.outcome, PolicyOutcome.ALLOW)
                self.assertTrue(permission.response.startswith(KNOWLEDGE_GAP_NOTICE))

    def test_only_web_search_and_only_when_eligible(self):
        decision = DialogueDecision(DecisionKind.KNOWLEDGE_GAP, intent=CommandIntent("open_app", {"name": "браузер"}))
        self.assertIs(knowledge_gap_permission(decision, "Що таке браузер?").outcome, PolicyOutcome.SAFE_NO_ACTION)
        self.assertIs(knowledge_gap_permission(lookup("тема"), "Що це?", action_eligible=False).outcome,
                      PolicyOutcome.SAFE_NO_ACTION)

    def test_fallback_converts_only_refused_non_direct_web_search(self):
        text = "Розкажи мені коротко про реставрацію Майджі."
        search = DialogueDecision(DecisionKind.ACTION_CANDIDATE,
                                  intent=CommandIntent("web_search", {"query": "реставрація Мейдзі"}))
        refused = natural_action_permission(search, text, text, None, False)
        self.assertIs(refused.outcome, PolicyOutcome.CLARIFY)
        converted = knowledge_gap_fallback(refused, search, text, text, False)
        self.assertEqual((converted.outcome, converted.route), (PolicyOutcome.ALLOW, "knowledge_gap"))
        app = DialogueDecision(DecisionKind.ACTION_CANDIDATE, intent=CommandIntent("open_app", {"name": "браузер"}))
        refused_app = natural_action_permission(app, "Що таке браузер?", "Що таке браузер?", None, False)
        self.assertIs(knowledge_gap_fallback(refused_app, app, "Що таке браузер?", "Що таке браузер?", False),
                      refused_app)
        self.assertIs(knowledge_gap_fallback(refused, search, text, text, True), refused)  # scoped


class _ProcessorCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.processor, _router, self.llm, _memory, self.speaker = build("chat", True)
        self.processor.services["enabled_skills"] |= {"web"}
        self.execute = AsyncMock(return_value=SkillResult(True, "знайдено", {"command_type": "web_search"}))
        patcher = patch("core.processor.execute_intent", self.execute)
        patcher.start()
        self.addCleanup(patcher.stop)

    def model(self, kind, tool="web_search", **arguments):
        self.llm.converse = AsyncMock(return_value=NaturalTurn(kind, intent=CommandIntent(tool, arguments)))

    async def run_turn(self, text):
        return await self.processor.process(self.envelope(text), AsyncMock())

    def executed_query(self):
        self.execute.assert_awaited_once()
        return self.execute.await_args.args[0].arguments

    def noticed(self):
        return any(call.args == (KNOWLEDGE_GAP_NOTICE,) for call in self.speaker.say.await_args_list)


class ScoredPathTests(_ProcessorCase):
    def envelope(self, text):
        return TurnEnvelope(turn_id="gap", session_id="gap", source="text", text=text, transcript=text,
                            stt_engine="text", confidence=1.0)

    async def test_lookup_says_unknown_then_confirmation_with_model_query(self):
        self.model("lookup", query="реставрація Мейдзі")
        result = await self.run_turn("Розкажи мені коротко про реставрацію Майджі.")
        self.assertEqual(self.executed_query(), {"query": "реставрація Мейдзі"})
        self.assertTrue(self.noticed())
        self.assertEqual(result.data["intent_route"], "knowledge_gap")

    async def test_non_direct_action_search_is_a_knowledge_gap(self):
        self.model("action", query="реставрація Мейдзі")
        result = await self.run_turn("Розкажи мені коротко про реставрацію Майджі.")
        self.assertEqual(result.data["intent_route"], "knowledge_gap")

    async def test_refused_search_executes_nothing(self):
        self.model("lookup", query="Мейдзі")
        result = await self.run_turn("Не шукай в інтернеті, що таке Мейдзі?")
        self.execute.assert_not_awaited()
        self.assertEqual(result.data["command_type"], "intent_clarification")


class UnscoredGroqPathTests(_ProcessorCase):
    def envelope(self, text):
        return TurnEnvelope(turn_id="groq", session_id="groq", source="voice", text=text, transcript=text,
                            stt_engine="groq", confidence=0, confidence_available=False, action_eligible=False)

    async def test_lead_in_request_keeps_user_words(self):
        self.model("action", query="важливі постаті Рісорджіменто")
        result = await self.run_turn("Не знаю навіть. Знайди мені інформацію про важливі постаті в ресорджаменту.")
        self.assertEqual(self.executed_query(), {"query": "важливі постаті в ресорджаменту"})
        self.assertNotIn("intent_route", result.data)

    async def test_tell_me_request_becomes_knowledge_gap(self):
        self.model("action", query="реставрація Мейдзі")
        result = await self.run_turn("Розкажи мені коротко про реставрацію Майджі.")
        self.assertEqual(self.executed_query(), {"query": "реставрація Мейдзі"})
        self.assertEqual(result.data["intent_route"], "knowledge_gap")
        self.assertTrue(self.noticed())

    async def test_lookup_on_groq_path(self):
        self.model("lookup", query="Гарібальді")
        result = await self.run_turn("Хто такий Гарібальді?")
        self.assertEqual(result.data["intent_route"], "knowledge_gap")

    async def test_yes_to_own_offer_on_groq_path(self):
        self.processor._last_reply = OFFER
        self.model("action", query="Рісорджіменто")
        result = await self.run_turn("Так, треба знайти.")
        self.assertEqual(self.executed_query(), {"query": "Рісорджіменто"})
        self.assertEqual(result.data["intent_route"], "assistant_offer")

    async def test_hallucinated_or_negated_actions_stay_blocked(self):
        for text, tool, arguments in (
                ("Я працював у браузері", "open_app", {"name": "браузер"}),
                ("Не відкривай браузер. Відкрий блокнот.", "open_app", {"name": "блокнот"}),
                ("Я вчора шукав рецепт борщу", "web_search", {"query": "рецепт борщу"}),
                ("Так", "web_search", {"query": "рецепт"})):
            with self.subTest(text=text):
                self.execute.reset_mock()
                self.processor._last_reply = ""
                self.model("action", tool=tool, **arguments)
                result = await self.run_turn(text)
                self.execute.assert_not_awaited()
                self.assertEqual(result.data["command_type"], "intent_clarification")


if __name__ == "__main__":
    unittest.main()
