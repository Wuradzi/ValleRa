from __future__ import annotations

import psutil
import os
import time


class WindowController:
    @staticmethod
    def _identity(window):
        import win32process
        pid = win32process.GetWindowThreadProcessId(window._hWnd)[1]
        process = psutil.Process(pid)
        return {"hwnd": int(window._hWnd), "pid": pid, "created": process.create_time(),
                "exe": process.exe(), "name": window.title}

    def candidates(self, query, aliases=()):
        """Read-only local window snapshots; no LLM handles or executable paths."""
        import pygetwindow as gw
        labels = {label.casefold() for label in (query, *aliases) if len(label.strip()) >= 2}
        found = []
        for window in gw.getAllWindows():
            try:
                if not window.title or not any(label in window.title.casefold() for label in labels):
                    continue
                if not (window.visible or window.isMinimized):
                    continue  # Hidden tray-only windows are not ordinary desktop windows.
                found.append(self._identity(window))
            except Exception:
                continue
            if len(found) >= 50:
                break
        return found

    def change_target(self, target, action):
        """Operate once on the approved snapshot, then observe actual window state."""
        if os.name != "nt" or action not in {"maximize", "minimize", "restore"}:
            return {"accepted": False, "verified": False, "status": "unsupported"}
        import pygetwindow as gw
        try:
            window = gw.Win32Window(target["hwnd"])
            current = self._identity(window)
            if any(current[key] != target[key] for key in ("hwnd", "pid", "created", "exe", "name")):
                return {"accepted": False, "verified": False, "status": "stale"}
        except Exception:
            return {"accepted": False, "verified": False, "status": "stale"}
        try:
            getattr(window, action)()
            if action != "minimize":
                try:
                    window.activate()
                except Exception:
                    pass  # Windows can refuse foreground activation; report separately.
            deadline = time.monotonic() + 2
            while True:
                if action == "maximize":
                    verified = window.isMaximized and not window.isMinimized
                elif action == "minimize":
                    verified = window.isMinimized
                else:
                    verified = window.visible and not window.isMinimized and not window.isMaximized
                if verified or time.monotonic() >= deadline:
                    return {"accepted": True, "verified": bool(verified),
                            "status": "verified" if verified else "unverified",
                            "foreground": bool(window.isActive) if action != "minimize" else False}
                time.sleep(.1)
        except Exception:
            # The request may have reached Windows before observation failed.
            return {"accepted": True, "verified": False, "status": "unverified"}

    @staticmethod
    def _find(query: str):
        import pygetwindow as gw
        return [
            window for window in gw.getAllWindows()
            if query.lower() in window.title.lower()
        ]

    def minimize(self, query: str) -> int:
        windows = self._find(query)
        for window in windows:
            window.minimize()
        return len(windows)

    def maximize(self, query: str) -> int:
        windows = self._find(query)
        for window in windows:
            window.maximize()
        return len(windows)

    def close(self, query: str) -> int:
        windows = self._find(query)
        for window in windows:
            window.close()
        return len(windows)

    @staticmethod
    def terminate_processes(query: str) -> int:
        count = 0
        for process in psutil.process_iter(["name"]):
            name = process.info.get("name") or ""
            if query.lower() in name.lower():
                process.terminate()
                count += 1
        return count
