from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.atomic_json import AtomicJSONFile


class NotesStore:
    def __init__(self, path: Path):
        self.file = AtomicJSONFile(path, {"items": []})

    def add(self, title: str, text: str, category: str = "general", tags=None) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        item = {
            "id": f"note_{uuid.uuid4().hex[:12]}",
            "title": title.strip(),
            "text": text.strip(),
            "category": category,
            "tags": tags or [],
            "created_at": now,
            "updated_at": now,
            "completed": False,
        }
        state = self.file.load()
        state["items"].append(item)
        self.file.save(state)
        return item

    def latest(self) -> dict | None:
        items = self.file.load()["items"]
        return items[-1] if items else None

    def search(self, query: str) -> list[dict]:
        query = query.lower()
        return [
            item for item in self.file.load()["items"]
            if query in f"{item['title']} {item['text']} {item['category']} {' '.join(item['tags'])}".lower()
        ]

    def edit(self, query: str, text: str) -> bool:
        state = self.file.load()
        for item in reversed(state["items"]):
            if query.lower() in item["title"].lower():
                item["text"] = text
                item["updated_at"] = datetime.now(timezone.utc).isoformat()
                self.file.save(state)
                return True
        return False

    def mark_completed(self, query: str) -> bool:
        state = self.file.load()
        for item in reversed(state["items"]):
            if query.lower() in item["title"].lower():
                item["completed"] = True
                item["updated_at"] = datetime.now(timezone.utc).isoformat()
                self.file.save(state)
                return True
        return False

    def delete(self, query: str) -> int:
        state = self.file.load()
        before = len(state["items"])
        state["items"] = [
            item for item in state["items"]
            if query.lower() not in item["title"].lower()
        ]
        self.file.save(state)
        return before - len(state["items"])
