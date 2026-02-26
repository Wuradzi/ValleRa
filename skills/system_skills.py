"""
System Skills - Системные команды (выключение, блокировка, статус)
"""

import os
import platform
import psutil
import subprocess

SYSTEM = platform.system()
IS_WINDOWS = SYSTEM == "Windows"
IS_LINUX = SYSTEM == "Linux"

if IS_WINDOWS:
    import ctypes

PENDING_CONFIRMATION = None


def turn_off_pc(text=None):
    """Вимикає комп'ютер. Потребує підтвердження."""
    global PENDING_CONFIRMATION
    
    if text and ("так" in text.lower() or "підтверди" in text.lower()):
        if PENDING_CONFIRMATION == "shutdown":
            if IS_WINDOWS: 
                subprocess.Popen(["shutdown", "/s", "/t", "30"])
            else:
                subprocess.Popen(["systemctl", "poweroff"])
            PENDING_CONFIRMATION = None
            return "Вимикаю..."
        else:
            PENDING_CONFIRMATION = None
    
    # Запитуємо підтвердження
    PENDING_CONFIRMATION = "shutdown"
    return "Точно вимкнути комп'ютер? Скажи 'так' для підтвердження."


def cancel_shutdown(text=None):
    """Скасовує вимкнення."""
    global PENDING_CONFIRMATION
    PENDING_CONFIRMATION = None
    
    if IS_WINDOWS: 
        subprocess.Popen(["shutdown", "/a"])
    else:
        subprocess.Popen(["shutdown", "-c"])
    return "Скасовано."


def lock_screen(text=None):
    """Блокує екран."""
    try:
        if IS_WINDOWS: 
            ctypes.windll.user32.LockWorkStation()
        else: 
            subprocess.Popen(["cinnamon-screensaver-command", "--lock"])
        return "Блоковано."
    except: 
        return "Помилка."


def wake_up_pc(text=None):
    """Будить комп'ютер (вимикає режим сну)."""
    try:
        if IS_LINUX:
            # Вимкнути DPMS (енергозбереження монітора)
            subprocess.Popen(["xset", "dpms", "force", "on"], 
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return "Комп'ютер активовано."
        elif IS_WINDOWS:
            # Windows - рухаємо мишею
            try:
                import pyautogui
                pyautogui.moveRel(1, 0)
                pyautogui.moveRel(-1, 0)
            except:
                pass
            return "Активовано."
        return "Не вдалося активувати."
    except Exception as e:
        return f"Помилка: {e}"


def system_status(text=None):
    """Показує статус системи."""
    try:
        cpu = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        return f"CPU: {cpu}% | RAM: {memory.percent}% ({memory.available // (1024**3)}GB вільно)"
    except Exception as e:
        return f"Помилка: {e}"
