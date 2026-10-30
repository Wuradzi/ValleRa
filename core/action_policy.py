"""Existing natural-action checks, separate from proposal and execution.

ALLOW means only eligibility to enter the executor. Validation, confirmation,
and verification still belong to the executor/tools and cannot be bypassed.
"""
from dataclasses import dataclass
from enum import Enum
import re
import time

from core.command_intent import (CommandIntent, MULTI_ACTION, SENSITIVE_REQUEST, original_search_intent,
                                 preserves_search_intent)
from core.natural_turn import direct_request, application_name_reply, request_scope
from core.dialogue_decision import DecisionKind
from core.action_proposal import PendingActionProposal, acceptance_matches, PROPOSABLE


class PolicyOutcome(str, Enum):
    ALLOW = 'allow'
    CLARIFY = 'clarify'
    SAFE_NO_ACTION = 'safe_no_action'


@dataclass(frozen=True, slots=True)
class ActionPermission:
    outcome: PolicyOutcome
    intent: CommandIntent | None = None
    response: str = ''
    turn_id: str | None = None
    route: str = 'direct'


# Tools whose executor shows the exact arguments in its own confirmation; the
# offer itself only has to name the kind of action.
OFFER_VERBS = {
    'web_search': r'знайд|знайт|пошук|шука',
    'find_files': r'знайд|знайт|пошук|шука|файл',
    'open_app': r'відкри|запуст',
    'weather': r'погод',
}
# «Яку програму відкрити?» asks for a parameter; it is not a yes/no offer.
_OPEN_QUESTION = re.compile(r'\b(?:яку|який|яке|які|якого|якій|що|де|куди|коли|скільки|навіщо|чому)\b', re.I)
_AFFIRMATION = re.compile(r'^(?:так|давай|добре|гаразд|згоден|згодна|звісно|авжеж|ага)\b', re.I)
_REFUSAL = re.compile(r'\b(?:не|ні|скасуй|скасувати|відміна|потім|пізніше)\b', re.I)


def _last_sentence(text):
    parts = [part for part in re.split(r'(?<=[.!?…])\s+', text.strip()) if part]
    return parts[-1] if parts else ''


def accepts_assistant_offer(previous_reply, text, tool):
    """«Так, треба знайти» right after Raven's own plain-CHAT offer of that kind of action.

    Only an affirmative, unquoted, single-action reply qualifies, and only when the
    last sentence of Raven's previous reply asked about the same kind of action.
    The model's arguments are then shown in the executor's confirmation.
    """
    if tool not in OFFER_VERBS or not isinstance(previous_reply, str) or not isinstance(text, str):
        return False
    value = re.sub(r'^(?:рейвен|рейвене|raven)[, ]+', '', ' '.join(text.split()), flags=re.I)
    if (not 1 <= len(value) <= 120 or '?' in value or any(char in value for char in '"«»“”`{}')
            or not _AFFIRMATION.match(value) or _REFUSAL.search(value)
            or SENSITIVE_REQUEST.search(value) or MULTI_ACTION.search(value)):
        return False
    offer = _last_sentence(previous_reply)
    return bool(re.search(r'\b(?:' + OFFER_VERBS[tool] + r')\w*', offer, re.I)
                and re.search(r'\?|\bчи\b', offer, re.I) and not _OPEN_QUESTION.search(offer))


def turn_permission(action_eligible, incomplete, *, stop=False):
    """The existing pre-routing quality gate, including the stop exception."""
    if action_eligible or stop:
        return PolicyOutcome.ALLOW
    return PolicyOutcome.CLARIFY if incomplete else PolicyOutcome.SAFE_NO_ACTION


def _accepts_offer_instead_of_request(intent, request, text, scoped, assistant_offer):
    return (intent is not None and not scoped and not direct_request(request, intent.tool)
            and accepts_assistant_offer(assistant_offer, text, intent.tool))


def _permission(decision, outcome, intent=None, response='', route='direct'):
    return ActionPermission(outcome, intent, response, decision.turn_id, route)


def natural_action_permission(decision, request, text, pending, scoped, *, action_eligible=True,
                              contextual_proposal=None, assistant_offer=None):
    if not action_eligible or decision.kind != DecisionKind.ACTION_CANDIDATE:
        return _permission(decision, PolicyOutcome.SAFE_NO_ACTION)
    intent = decision.intent
    if decision.origin == 'contextual_followup':
        # The orchestration owner must consume its live lease before executing.
        # Never use model-generated replacement arguments for an acceptance.
        if (isinstance(contextual_proposal, PendingActionProposal)
                and decision.proposal_id == contextual_proposal.proposal_id
                and intent == contextual_proposal.intent and intent.tool in PROPOSABLE
                and acceptance_matches(text, intent.tool)):
            return _permission(decision, PolicyOutcome.ALLOW, intent)
        return _permission(decision, PolicyOutcome.CLARIFY, response=
            'Уточніть, яку дію ви хочете виконати; нічого не виконано.')
    expected_scope = pending['tool'] if scoped else request_scope(request)
    if intent is not None and expected_scope and intent.tool != expected_scope:
        return _permission(decision, PolicyOutcome.CLARIFY, response=
            'Запропонована дія не відповідає типу вашого запиту. Уточніть прохання; нічого не виконано.')
    if _accepts_offer_instead_of_request(intent, request, text, scoped, assistant_offer):
        # The user's words are an acceptance, not a query: the executor's
        # confirmation shows the model's exact arguments before anything runs.
        return _permission(decision, PolicyOutcome.ALLOW, intent, route='assistant_offer')
    if (intent is not None and intent.tool == 'open_app' and pending is not None
            and time.monotonic() < pending['expires'] and not direct_request(text, intent.tool)):
        name = application_name_reply(text)
        if name is not None:
            intent = CommandIntent('open_app', {'name': name})
        elif scoped:
            return _permission(decision, PolicyOutcome.CLARIFY, response=
                'Назвіть, будь ласка, програму, яку відкрити.')
    else:
        pending = None
    if intent is None or (not direct_request(request, intent.tool)
                          and not scoped
                          and not (pending and application_name_reply(text) is not None)):
        return _permission(decision, PolicyOutcome.CLARIFY, response=
            'Не впевнений, що це пряме прохання виконати дію. Уточніть, будь ласка; нічого не виконано.')
    intent = original_search_intent(request, intent)
    if not preserves_search_intent(request, intent):
        return _permission(decision, PolicyOutcome.CLARIFY, response=
            'Уточніть точний запит пошуку: не можу надійно зберегти його зміст.')
    return _permission(decision, PolicyOutcome.ALLOW, intent)
