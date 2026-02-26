"""
Media Skills - Управление звуком, воспроизведением, скриншотами
"""

import datetime
import pyperclip


def _get_pyautogui():
    """Ленивая загрузка pyautogui (требует display)."""
    try:
        import pyautogui
        return pyautogui
    except Exception:
        return None


def volume_up(text=None):
    """増大 гучність."""
    pag = _get_pyautogui()
    if pag:
        pag.press('volumeup')
    return "Гучніше."


def volume_down(text=None):
    """Зменшує гучність."""
    pag = _get_pyautogui()
    if pag:
        pag.press('volumedown')
    return "Тихіше."


def media_play_pause(text=None):
    """Грає/паузує медіа."""
    pag = _get_pyautogui()
    if pag:
        pag.press("playpause")
    return "Ок."


def media_next(text=None):
    """Наступна дорівка."""
    pag = _get_pyautogui()
    if pag:
        pag.press("nexttrack")
    return "Далі."


def media_prev(text=None):
    """Попередня дорівка."""
    pag = _get_pyautogui()
    if pag:
        pag.press("prevtrack")
    return "Назад."


def click_play(text=None):
    """Синонім для play_pause."""
    return media_play_pause()


def take_screenshot(text=None):
    """Робить скріншот екрану."""
    pag = _get_pyautogui()
    if not pag:
        return "Помилка: pyautogui не доступен."
    
    filename = f"screen_{datetime.datetime.now().strftime('%M%S')}.png"
    try:
        pag.screenshot(filename)
        return f"Скріншот збережен: {filename}"
    except Exception as e:
        return f"Помилка скріншоту: {e}"


def look_at_screen():
    """Робить скріншот для аналізу AI (зору)."""
    pag = _get_pyautogui()
    if not pag:
        return None
    
    filename = f"vision_{datetime.datetime.now().strftime('%M%S%f')}.png"
    try:
        pag.screenshot(filename)
        return filename
    except Exception as e:
        print(f"Screenshot error: {e}")
        return None


def read_clipboard(text=None, voice=None, listener=None):
    """Читає зміст буфера обміну."""
    try:
        c = pyperclip.paste()
        if not c: 
            return "Пусто."
        print(f"📋: {c[:20]}...")
        return f"У буфері: {c}"
    except: 
        return "Помилка буфера."
