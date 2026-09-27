"""Runtime-local, single-use dispatch tickets; no transcript-based deduplication."""
from uuid import uuid4


class DispatchGuard:
    def __init__(self, capacity=64):
        self.capacity = capacity
        self._session = uuid4().hex
        self._sequence = 0
        self.pending: set[str] = set()

    def issue(self):
        if len(self.pending) >= self.capacity:
            raise RuntimeError("Too many pending turn dispatches")
        self._sequence += 1
        turn_id = f"{self._session}:{self._sequence}"
        self.pending.add(turn_id)
        return turn_id

    def claim(self, turn_id):
        if turn_id not in self.pending:
            return False
        self.pending.remove(turn_id)
        return True

    def discard(self, turn_id):
        self.pending.discard(turn_id)
