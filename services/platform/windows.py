"""Existing Windows operations; receives requests after Core authorization."""
import os
import subprocess
from pathlib import Path


def open_application(app):
    os.startfile(app['command'])
    return True


def open_file(path):
    os.startfile(path)


def window_controller():
    from services.windows.window_controller import WindowController
    return WindowController()


def system_action(action):
    if action == 'lock':
        import ctypes
        return bool(ctypes.windll.user32.LockWorkStation())
    commands = {'shutdown': ['shutdown', '/s', '/t', '60'], 'cancel_shutdown': ['shutdown', '/a']}
    return subprocess.run(commands[action], check=False, capture_output=True).returncode == 0


def play_wav(path):
    import winsound
    winsound.PlaySound(str(path), winsound.SND_FILENAME)


def drive_roots():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetLogicalDrives.restype = wintypes.DWORD
    kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel.GetDriveTypeW.restype = wintypes.UINT
    mask = kernel.GetLogicalDrives()
    return [Path(f'{chr(65 + index)}:/') for index in range(26)
            if mask & (1 << index) and kernel.GetDriveTypeW(f'{chr(65 + index)}:\\') in {2, 3}]
