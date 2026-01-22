# skills.py
import os
import datetime
import pyautogui
import webbrowser
import psutil
import json
import requests
from geopy.geocoders import Nominatim
from duckduckgo_search import DDGS

APPS_CACHE = {}
APPS_SCANNED = False

# === СИСТЕМНІ УТИЛІТИ ===

def _ensure_app_index():
    global APPS_CACHE, APPS_SCANNED
    if APPS_SCANNED: return
    
    print("📂 Індексація програм (Windows)...")
    paths = [
        r"C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs",
        os.path.expandvars(r"%AppData%\\Microsoft\\Windows\\Start Menu\\Programs")
    ]
    for path in paths:
        if not os.path.exists(path): continue
        for root, dirs, files in os.walk(path):
            for file in files:
                if file.endswith((".lnk", ".url")):
                    name = file.lower().replace(".lnk", "").replace(".url", "")
                    APPS_CACHE[name] = os.path.join(root, file)
    APPS_SCANNED = True
    print(f"✅ Програм знайдено: {len(APPS_CACHE)}")

def open_program(text):
    _ensure_app_index()
    query = text.lower().replace("запусти", "").replace("відкрий", "").strip()
    
    best_match = None
    # Простий пошук входження
    for app in APPS_CACHE:
        if query in app:
            best_match = app
            break 
            
    if best_match:
        try:
            os.startfile(APPS_CACHE[best_match])
            return f"Запускаю {best_match}."
        except: return "Помилка запуску файлу."
    
    return "Не знайшов такої програми."

def is_app_name(text):
    _ensure_app_index()
    query = text.lower().strip()
    return any(query in app for app in APPS_CACHE)

def look_at_screen(text=None):
    """Робить скріншот і повертає шлях"""
    try:
        path = "vision_buffer.png"
        pyautogui.screenshot(path)
        return path
    except Exception as e:
        print(f"Screen error: {e}")
        return None

# === БАЗОВІ КОМАНДИ ===

def turn_off_pc(text=None):
    os.system("shutdown /s /t 30")
    return "Живлення вимкнеться за 30 секунд."

def cancel_shutdown(text=None):
    os.system("shutdown /a")
    return "Вимкнення скасовано."

def get_time(text=None):
    return f"Зараз {datetime.datetime.now().strftime('%H:%M')}."

def get_date(text=None):
    return f"Сьогодні {datetime.date.today()}."

def volume_up(text=None):
    for _ in range(5): pyautogui.press('volumeup')
    return "Гучніше."

def volume_down(text=None):
    for _ in range(5): pyautogui.press('volumedown')
    return "Тихіше."

def take_screenshot(text=None):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    pyautogui.screenshot(f"screen_{ts}.png")
    return "Фото збережено."

# === ІНТЕРНЕТ ===

def search_google(text):
    query = text.replace("гугл", "").replace("пошук", "").strip()
    webbrowser.open(f"https://google.com/search?q={query}")
    return f"Шукаю: {query}"

def search_youtube_clip(text):
    query = text.replace("ютуб", "").replace("відео", "").strip()
    webbrowser.open(f"https://www.youtube.com/results?search_query={query}")
    return f"Ютуб: {query}"

def check_weather(text):
    try:
        r = requests.get("https://wttr.in/?format=3")
        return r.text if r.status_code == 200 else "Не можу глянути погоду."
    except: return "Помилка з'єднання."

def get_custom_knowledge(text):
    # Тут можна читати .txt файли з папки knowledge
    return ""