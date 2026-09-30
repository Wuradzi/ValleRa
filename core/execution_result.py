"""Read-only typed view of legacy tool evidence, never inferred from prose."""
from dataclasses import dataclass
from enum import Enum


class ExecutionStatus(str, Enum):
    REJECTED = 'rejected'
    CANCELLED = 'cancelled'
    SUBMITTED = 'submitted'
    FAILED = 'failed'
    VERIFIED = 'verified'
    SUBMITTED_UNVERIFIED = 'submitted_unverified'
    UNKNOWN = 'unknown'


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    status: ExecutionStatus
    accepted: bool | None
    success: bool | None
    verified: bool | None

    @classmethod
    def from_data(cls, data):
        # Missing evidence remains unknown. handled is deliberately not used.
        accepted, success, verified = (data.get(key) for key in ('accepted', 'success', 'verified'))
        status = data.get('status')
        if status in {'cancelled', 'rejected', 'failed'}:
            state = ExecutionStatus(status)
        elif verified is True:
            state = ExecutionStatus.VERIFIED
        elif status == 'submitted' or accepted is True:
            state = (ExecutionStatus.SUBMITTED_UNVERIFIED if verified is False
                     else ExecutionStatus.SUBMITTED)
        elif data.get('command_type') in {'interpretation_invalid', 'interpretation_unsupported',
                                          'interpretation_unavailable', 'intent_clarification'}:
            state = ExecutionStatus.REJECTED
        elif accepted is False:
            state = ExecutionStatus.REJECTED
        elif success is False:
            state = ExecutionStatus.FAILED
        else:
            state = ExecutionStatus.UNKNOWN
        return cls(state, accepted, success, verified)
