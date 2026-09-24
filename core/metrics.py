from __future__ import annotations

import json
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class MetricsCollector:
    PRIVATE_FIELDS = {"text", "password", "secret", "api_key", "note_content"}

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(self, event: str, **fields: Any) -> None:
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **{k: v for k, v in fields.items() if k not in self.PRIVATE_FIELDS},
        }
        with self._lock, self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")

    def report(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"events": 0, "commands": 0, "success_rate": 0.0, "average_duration_ms": 0.0}

        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        completed = [row for row in rows if row.get("event") == "command_completed"]
        successful = sum(bool(row.get("success")) for row in completed)
        durations = [float(row["duration_ms"]) for row in completed if "duration_ms" in row]
        return {
            "events": len(rows),
            "commands": len(completed),
            "event_counts": dict(Counter(row.get("event", "unknown") for row in rows)),
            "success_rate": round(successful / len(completed) * 100, 2) if completed else 0.0,
            "average_duration_ms": round(sum(durations) / len(durations), 2) if durations else 0.0,
        }
