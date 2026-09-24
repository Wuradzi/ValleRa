from __future__ import annotations

import os
import subprocess
import webbrowser
import time

from rapidfuzz import fuzz, process


class ApplicationController:
    def __init__(self, indexer):
        self.indexer = indexer

    def has_exact_name(self, query: str) -> bool:
        """Read-only fast-route check: never a fuzzy guess or execution receipt."""
        name = query.lower().strip()
        if name == "браузер":
            return True  # The executor's dedicated default-browser action.
        commands = {
            app["command"] for app in self.indexer.all()
            if any(name == variant.lower() for variant in [app["name"], *app.get("aliases", [])])
        }
        return len(commands) == 1

    def find(self, query: str, limit: int = 5) -> list[dict]:
        labels, mapping = [], {}
        for app in self.indexer.all():
            for variant in [app["name"], *app.get("aliases", [])]:
                label = variant.lower()
                labels.append(label)
                mapping[label] = app

        normalized = query.lower().strip()
        if normalized in mapping:
            return [{**mapping[normalized], "score": 100}]

        matches = process.extract(normalized, labels, scorer=fuzz.WRatio, limit=limit)
        result, seen = [], set()
        for label, score, _ in matches:
            app = mapping[label]
            if score >= 80 and app["command"] not in seen:
                result.append({**app, "score": score})
                seen.add(app["command"])
        return result

    @staticmethod
    def open_default_browser() -> bool:
        # Windows Shell treats "about" as a protocol name when no browser
        # executable is supplied, which opens the Microsoft Store chooser.
        # A normal HTTPS URL reliably delegates to the configured browser.
        return bool(webbrowser.open_new_tab("https://www.google.com/"))

    def open(self, app: dict) -> bool:
        if os.name == "nt":
            os.startfile(app["command"])
        else:
            subprocess.Popen([app["command"]], start_new_session=True)
        return True

    @staticmethod
    def verify_launch(app=None):
        """Bounded read-only check; shell acceptance alone is not a visible window."""
        if os.name != "nt":
            return {"process": False, "window": False}
        from services.apps.workplace import WorkplaceAgent
        from services.apps.workplace_windows import application_evidence, default_browser_executable
        command = app["command"] if app is not None else default_browser_executable()
        target = WorkplaceAgent._target(app["name"] if app else "Браузер", command)
        deadline = time.monotonic() + 2
        while True:
            evidence = application_evidence(target["path"])
            if evidence["window"] or time.monotonic() >= deadline:
                return evidence
            time.sleep(.15)
