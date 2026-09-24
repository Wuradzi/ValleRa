from __future__ import annotations

import pyautogui

from core.models import SkillResult


def can_handle(command, services):
    return command in {
        "збільш гучність",
        "зменш гучність",
        "вимкни звук",
        "увімкни звук",
        "пауза",
        "продовж відтворення",
        "наступний трек",
        "попередній трек",
    }


async def handle(command, context, services):
    if "збільш" in command and "гуч" in command:
        pyautogui.press("volumeup", presses=3)
        response = "Гучність збільшено."
    elif "зменш" in command and "гуч" in command:
        pyautogui.press("volumedown", presses=3)
        response = "Гучність зменшено."
    elif command in {"вимкни звук", "увімкни звук"}:
        pyautogui.press("volumemute")
        response = "Звук перемкнено."
    elif "наступн" in command:
        pyautogui.press("nexttrack")
        response = "Наступний трек."
    elif "попередн" in command:
        pyautogui.press("prevtrack")
        response = "Попередній трек."
    else:
        pyautogui.press("playpause")
        response = "Відтворення перемкнено."
    return SkillResult(True, response, {"command_type": "media"})
