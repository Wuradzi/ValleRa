"""Platform-independent proposal boundary; a decision never executes a tool."""
from dataclasses import dataclass
from enum import Enum

from core.command_intent import CommandIntent


class DecisionKind(str, Enum):
    CHAT = 'chat'
    CLARIFY = 'clarify'
    ACTION_CANDIDATE = 'action'
    LOCAL_COMMAND = 'local_command'
    CONTROL = 'control'


@dataclass(frozen=True, slots=True)
class DialogueDecision:
    kind: DecisionKind
    response: str = ''
    intent: CommandIntent | None = None
    turn_id: str | None = None

    @classmethod
    def from_natural(cls, turn, turn_id=None):
        return cls(DecisionKind(turn.kind), turn.response, turn.intent, turn_id)
