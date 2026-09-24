from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.atomic_json import AtomicJSONFile


class ReminderStore:
    def __init__(self, path: Path):
        self.file = AtomicJSONFile(path, {"items": []})

    def add(self, text: str, due_at: datetime, recurrence=None, interval_seconds=None) -> dict:
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=timezone.utc)
        item = {
            "id": f"rem_{uuid.uuid4().hex[:12]}",
            "text": text,
            "due_at": due_at.isoformat(),
            "recurrence": recurrence,
            "interval_seconds": interval_seconds,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_triggered_at": None,
        }
        state = self.file.load()
        state["items"].append(item)
        self.file.save(state)
        return item

    def due(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        return [
            item for item in self.file.load()["items"]
            if item["status"] == "pending"
            and datetime.fromisoformat(item["due_at"]).astimezone(timezone.utc) <= now
        ]

    def missed(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        state = self.file.load()
        result = []
        for item in state["items"]:
            if (
                item["status"] == "pending"
                and datetime.fromisoformat(item["due_at"]).astimezone(timezone.utc) < now
            ):
                result.append(dict(item))
                next_due = self._next_due(item, now)
                if next_due is None:
                    item["status"] = "missed"
                else:
                    item["due_at"] = next_due.isoformat()
                    item["last_triggered_at"] = now.isoformat()
        if result:
            self.file.save(state)
        return result

    def mark_triggered(self, reminder_id: str) -> None:
        state = self.file.load()
        now = datetime.now(timezone.utc)
        for item in state["items"]:
            if item["id"] != reminder_id:
                continue
            item["last_triggered_at"] = now.isoformat()
            next_due = self._next_due(item, now)
            if next_due is None:
                item["status"] = "triggered"
            else:
                item["due_at"] = next_due.isoformat()
            self.file.save(state)
            return

    @staticmethod
    def _next_due(item: dict, now: datetime) -> datetime | None:
        recurrence = item.get("recurrence")
        if recurrence == "daily":
            step = timedelta(days=1)
        elif recurrence == "weekly":
            step = timedelta(weeks=1)
        elif recurrence == "interval" and item.get("interval_seconds"):
            step = timedelta(seconds=max(1, int(item["interval_seconds"])))
        else:
            return None

        due = datetime.fromisoformat(item["due_at"])
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        due = due.astimezone(timezone.utc)
        while due <= now:
            due += step
        return due

    def cancel(self, query: str) -> int:
        state = self.file.load()
        count = 0
        for item in state["items"]:
            if query.lower() in item["text"].lower() and item["status"] in {"pending", "missed"}:
                item["status"] = "cancelled"
                count += 1
        self.file.save(state)
        return count

    def mark_completed(self, query: str) -> bool:
        state = self.file.load()
        for item in state["items"]:
            if query.lower() in item["text"].lower():
                item["status"] = "completed"
                self.file.save(state)
                return True
        return False

    def all(self) -> list[dict]:
        return self.file.load()["items"]
