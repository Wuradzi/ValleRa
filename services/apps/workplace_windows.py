"""Read-only Windows evidence for the bounded workplace scenario."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path

import psutil


def default_browser_executable():
    query = ctypes.WinDLL("shlwapi").AssocQueryStringW
    query.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR,
                      wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    query.restype = ctypes.c_long
    length = wintypes.DWORD(32768)
    buffer = ctypes.create_unicode_buffer(length.value)
    # ASSOCF_IS_PROTOCOL | ASSOCF_NOFIXUPS; ASSOCSTR_EXECUTABLE.
    if query(0x1000 | 0x100, 2, "https", "open", buffer, ctypes.byref(length)) != 0 or not buffer.value:
        raise ValueError("Не вдалося визначити EXE браузера за замовчуванням. Назвіть конкретний браузер.")
    if Path(buffer.value).name.casefold() in {"openwith.exe", "rundll32.exe", "launchwinapp.exe"}:
        raise ValueError("Windows не повернула EXE браузера за замовчуванням. Налаштуйте браузер для HTTPS або назвіть його явно у списку робочого місця.")
    return buffer.value


def application_evidence(path):
    """A visible window is evidence, not proof of login/project/application readiness."""
    import win32gui
    import win32process

    wanted = os.path.normcase(os.path.realpath(path))
    pids = set()
    for process in psutil.process_iter(["exe"]):
        try:
            exe = process.info.get("exe")
            if exe and os.path.normcase(os.path.realpath(exe)) == wanted:
                pids.add(process.pid)
        except (psutil.Error, OSError):
            continue
    found = False

    def visit(hwnd, _):
        nonlocal found
        try:
            if (win32gui.IsWindowVisible(hwnd) and not win32gui.IsIconic(hwnd)
                    and win32process.GetWindowThreadProcessId(hwnd)[1] in pids):
                found = True
        except win32gui.error:
            pass  # Windows can disappear while being enumerated.

    if pids:
        win32gui.EnumWindows(visit, None)
    return {"process": bool(pids), "window": found}
