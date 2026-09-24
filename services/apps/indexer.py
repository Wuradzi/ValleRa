from __future__ import annotations

import os
from pathlib import Path

from core.atomic_json import AtomicJSONFile


class ApplicationIndexer:
    def __init__(self, path: Path, aliases: dict[str, str]):
        self.file = AtomicJSONFile(path, {"applications": [], "manual": []})
        self.aliases = aliases

    def rebuild(self) -> list[dict]:
        state = self.file.load()
        found: dict[str, dict] = {}
        roots = [
            Path(os.getenv("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
            Path(os.getenv("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
            Path.home() / "Desktop",
            Path(os.getenv("PUBLIC", "C:/Users/Public")) / "Desktop",
        ]

        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if path.suffix.lower() not in {".lnk", ".url", ".exe", ".bat", ".cmd"}:
                    continue
                found[path.stem.lower()] = {
                    "name": path.stem,
                    "command": str(path),
                    "aliases": [],
                    "source": "indexed",
                }

        if os.name == "nt":
            try:
                import winreg
                registry_roots = [
                    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                ]
                for hive, key_path in registry_roots:
                    try:
                        with winreg.OpenKey(hive, key_path) as parent:
                            for index in range(winreg.QueryInfoKey(parent)[0]):
                                sub_name = winreg.EnumKey(parent, index)
                                try:
                                    with winreg.OpenKey(parent, sub_name) as subkey:
                                        name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                                        try:
                                            command = winreg.QueryValueEx(subkey, "DisplayIcon")[0]
                                        except OSError:
                                            command = ""
                                        command = str(command).split(",")[0].strip('"')
                                        if name and command and Path(command).exists():
                                            found[str(name).lower()] = {
                                                "name": str(name),
                                                "command": command,
                                                "aliases": [],
                                                "source": "registry",
                                            }
                                except OSError:
                                    continue
                    except OSError:
                        continue
            except Exception:
                pass

        for item in state.get("manual", []):
            found[item["name"].lower()] = item

        for alias, target in self.aliases.items():
            if not target:
                continue
            matches = [
                app for app in found.values()
                if target.lower() in app["name"].lower()
            ]
            if not matches:
                continue
            app = min(
                matches,
                key=lambda item: (
                    item["name"].lower() != target.lower(),
                    len(item["name"]),
                ),
            )
            if alias not in app["aliases"]:
                app["aliases"].append(alias)

        state["applications"] = sorted(found.values(), key=lambda app: app["name"].lower())
        self.file.save(state)
        return state["applications"]

    def add_manual(self, name: str, command: str, aliases: list[str]) -> None:
        state = self.file.load()
        state.setdefault("manual", []).append({
            "name": name,
            "command": command,
            "aliases": aliases,
            "source": "manual",
        })
        self.file.save(state)
        self.rebuild()

    def all(self) -> list[dict]:
        return self.file.load().get("applications", [])
