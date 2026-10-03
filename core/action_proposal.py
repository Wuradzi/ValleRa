"""Session-only conversational proposals. Never a safety confirmation."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import re
import time
from uuid import uuid4

from core.command_intent import CommandIntent, InvalidIntent, validate_intent, _unique_object
from core.command_catalog import TOOL_SKILLS


class ReplyKind(str, Enum):
    ACCEPT = 'accept'
    REJECT = 'reject'
    MODIFY = 'modify'
    NEW_REQUEST = 'new_request'
    CHAT = 'chat'
    AMBIGUOUS = 'ambiguous'


@dataclass(frozen=True, slots=True)
class ProposalReply:
    proposal_id: str
    kind: ReplyKind
    arguments: tuple[tuple[str, str], ...] = ()


def parse_reply(body):
    try:
        data = json.loads(body, object_pairs_hook=_unique_object)
        if not isinstance(data, dict) or set(data) not in (
                {'proposal_id', 'resolution'}, {'proposal_id', 'resolution', 'arguments'}):
            raise ValueError()
        pid, kind = data['proposal_id'], ReplyKind(data['resolution'])
        if not isinstance(pid, str) or not re.fullmatch(r'[a-f0-9]{32}', pid):
            raise ValueError()
        args = data.get('arguments', {})
        if not isinstance(args, dict) or (kind is not ReplyKind.MODIFY and 'arguments' in data):
            raise ValueError()
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in args.items()):
            raise ValueError()
        return ProposalReply(pid, kind, tuple(sorted(args.items())))
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise InvalidIntent('invalid proposal reply') from exc


# Closed subset of existing validated tools; no shell, deletion or arbitrary path.
PROPOSABLE = {'web_search', 'open_app', 'find_files', 'weather', 'window_control', 'prepare_workplace'}


def proposal_question(intent):
    args = intent.arguments
    if intent.tool == 'web_search':
        return f"Пошукати в інтернеті: «{args['query']}»?"
    if intent.tool == 'open_app':
        return f"Відкрити програму «{args['name']}»?"
    if intent.tool == 'find_files':
        return f"Знайти файли «{args['query']}», тип {args['extension'] or 'будь-який'}?"
    if intent.tool == 'weather':
        period = {'now': 'зараз', 'today': 'сьогодні', 'tomorrow': 'завтра'}[args['period']]
        return f"Перевірити погоду: «{args['city']}», {period}?"
    if intent.tool == 'window_control':
        verb = {'maximize': 'Розгорнути', 'minimize': 'Згорнути', 'restore': 'Відновити'}[args['action']]
        return f"{verb} вікно «{args['name']}»?"
    return 'Підготувати збережене робоче місце?'


@dataclass(frozen=True, slots=True)
class PendingActionProposal:
    proposal_id: str
    origin_turn_id: str | None
    session_id: str
    tool: str
    arguments: tuple[tuple[str, str], ...]
    reason: str
    created_at: float
    expires_at: float

    @property
    def intent(self):
        return CommandIntent(self.tool, dict(self.arguments))

    def model_context(self):
        return {'proposal_id': self.proposal_id, 'tool': self.tool,
                'arguments': dict(self.arguments), 'question': proposal_question(self.intent)}


@dataclass(frozen=True, slots=True)
class ProposalLease:
    proposal: PendingActionProposal
    generation: int


class ProposalState:
    """One proposal, one next turn. Generation invalidates in-flight resolutions."""
    def __init__(self, ttl_seconds=120, clock=time.monotonic):
        self.ttl_seconds, self.clock = ttl_seconds, clock
        self.session_id = uuid4().hex  # Only for legacy string callers, not a turn ID.
        self.pending: PendingActionProposal | None = None
        self.generation = 0

    def clear(self):
        self.pending = None
        self.generation += 1

    def begin_turn(self, session_id):
        proposal = self.pending
        self.clear()  # Unrelated/failed/cancelled turns never leave reusable authority.
        if proposal and proposal.session_id == session_id and self.clock() < proposal.expires_at:
            return ProposalLease(proposal, self.generation)
        return None

    def current(self, lease):
        return bool(lease and lease.generation == self.generation
                    and self.clock() < lease.proposal.expires_at)

    def offer(self, intent, origin_turn_id, session_id, enabled, generation):
        if generation != self.generation:
            return None
        try:
            intent = validate_intent({'tool': intent.tool, 'arguments': intent.arguments})
        except (InvalidIntent, AttributeError, TypeError):
            return None
        if intent.tool not in PROPOSABLE or TOOL_SKILLS[intent.tool] not in enabled:
            return None
        now = self.clock()
        self.pending = PendingActionProposal(uuid4().hex, origin_turn_id, session_id,
            intent.tool, tuple(sorted(intent.arguments.items())), 'assistant_offer', now, now + self.ttl_seconds)
        return self.pending

    def consume(self, lease):
        if not self.current(lease):
            return None
        self.clear()
        return lease.proposal


def acceptance_matches(text, tool):
    """Second check on a model ACCEPT, not a keyword-triggered execution path.

    Requires a live unique proposal + correlated model decision + later safety
    approval. Questions, negation, quotations and extra instructions fail closed.
    """
    value = ' '.join(text.casefold().strip().split())
    if '?' in value or any(c in value for c in '"«»“”`{}'):
        return False
    value = value.strip(' .!,')
    value = re.sub(r'^(?:валера|валеро)[, ]+', '', value)
    value = re.sub(r'^(?:будь ласка[, ]+)', '', value)
    value = re.sub(r'[, ]+будь ласка$', '', value)
    if value in {'так', 'давай', 'гаразд', 'добре', 'згоден', 'згодна', 'зроби це', 'так зроби', 'давай зробимо'}:
        return True
    verbs = {
        'web_search': {'підбери', 'шукай', 'пошукай', 'знайди'},
        'find_files': {'шукай', 'пошукай', 'знайди'},
        'open_app': {'відкривай', 'відкрий', 'відкрий його', 'його', 'цей варіант'},
        'weather': {'перевір', 'перевіряй', 'подивись'},
        'window_control': {'зроби це'},
        'prepare_workplace': {'підготуй'},
    }
    return value in verbs.get(tool, set())


def needs_proposal(text):
    """Bare references cannot manufacture arguments when there is no owner."""
    return ' '.join(text.casefold().strip(' .!,').split()) in {
        'підбери', 'шукай', 'відкривай', 'зроби це', 'його', 'цей варіант', 'другий'}
