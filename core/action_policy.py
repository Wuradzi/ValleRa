"""Existing natural-action checks, separate from proposal and execution.

ALLOW means only eligibility to enter the executor. Validation, confirmation,
and verification still belong to the executor/tools and cannot be bypassed.
"""
from dataclasses import dataclass
from enum import Enum
import time

from core.command_intent import CommandIntent, preserves_search_intent
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


def turn_permission(action_eligible, incomplete, *, stop=False):
    """The existing pre-routing quality gate, including the stop exception."""
    if action_eligible or stop:
        return PolicyOutcome.ALLOW
    return PolicyOutcome.CLARIFY if incomplete else PolicyOutcome.SAFE_NO_ACTION


def natural_action_permission(decision, request, text, pending, scoped, *, action_eligible=True,
                              contextual_proposal=None):
    def permission(outcome, intent=None, response=''):
        return ActionPermission(outcome, intent, response, decision.turn_id)

    if not action_eligible or decision.kind != DecisionKind.ACTION_CANDIDATE:
        return permission(PolicyOutcome.SAFE_NO_ACTION)
    intent = decision.intent
    if decision.origin == 'contextual_followup':
        # The orchestration owner must consume its live lease before executing.
        # Never use model-generated replacement arguments for an acceptance.
        if (isinstance(contextual_proposal, PendingActionProposal)
                and decision.proposal_id == contextual_proposal.proposal_id
                and intent == contextual_proposal.intent and intent.tool in PROPOSABLE
                and acceptance_matches(text, intent.tool)):
            return permission(PolicyOutcome.ALLOW, intent)
        return permission(PolicyOutcome.CLARIFY, response=
            'Уточніть, яку дію ви хочете виконати; нічого не виконано.')
    expected_scope = pending['tool'] if scoped else request_scope(request)
    if intent is not None and expected_scope and intent.tool != expected_scope:
        return permission(PolicyOutcome.CLARIFY, response=
            'Запропонована дія не відповідає типу вашого запиту. Уточніть прохання; нічого не виконано.')
    if (intent is not None and intent.tool == 'open_app' and pending is not None
            and time.monotonic() < pending['expires'] and not direct_request(text, intent.tool)):
        name = application_name_reply(text)
        if name is not None:
            intent = CommandIntent('open_app', {'name': name})
        elif scoped:
            return permission(PolicyOutcome.CLARIFY, response=
                'Назвіть, будь ласка, програму, яку відкрити.')
    else:
        pending = None
    if intent is None or (not direct_request(request, intent.tool)
                          and not scoped
                          and not (pending and application_name_reply(text) is not None)):
        return permission(PolicyOutcome.CLARIFY, response=
            'Не впевнений, що це пряме прохання виконати дію. Уточніть, будь ласка; нічого не виконано.')
    if not preserves_search_intent(request, intent):
        return permission(PolicyOutcome.CLARIFY, response=
            'Уточніть точний запит пошуку: не можу надійно зберегти його зміст.')
    return permission(PolicyOutcome.ALLOW, intent)
