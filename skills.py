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

def open_program(text, voice=None, listener=None):
    """
    Розумний запуск програм з уточненням.
    """
    _ensure_app_index() # Переконуємось, що кеш є
    
    # 1. Чистка: прибираємо слова-команди
    ignore_words = ["відкрий", "запусти", "включи", "open", "launch", "start", "програму", "апку", "валера", "будь ласка"]
    query = text.lower()
    for word in ignore_words:
        query = query.replace(word, "")
    query = query.strip()
    
    if not query:
        if voice and listener:
            voice.say("Яку саме програму відкрити?")
            answer = listener.listen()
            if answer:
                query = answer.lower()
            else:
                return "Я нічого не почув."
        else:
            return "Яку програму треба відкрити?"

    print(f"🔎 Шукаю програму: '{query}'")
    best_match = None
    
    # Шукаємо найкращий збіг
    for app_name, app_path in APPS_CACHE.items():
        if query in app_name:
            if best_match is None or len(app_name) < len(best_match):
                best_match = app_name
                target_path = app_path

    if best_match:
        try:
            os.startfile(target_path)
            return f"Запускаю {best_match}."
        except Exception as e:
            return "Файл знайдено, але Windows не дає його запустити."
    else:
        return f"Я не знайшов програми з назвою {query}."

def is_app_name(text):
    _ensure_app_index()
    clean = text.lower()
    ignore_words = ["запусти", "відкрий", "включи", "open", "launch", "start", "програму", "апку", "будь ласка", "валера"]
    for word in ignore_words:
        clean = clean.replace(word, "").strip()
    
    if not clean: return False
        
    for app_name in APPS_CACHE.keys():
        if clean in app_name: 
            return True
    return False

def look_at_screen(text=None):
    """Робить скріншот і повертає шлях"""
    try:
        path = "vision_buffer.png"
        pyautogui.screenshot(path)
        return path
    except Exception as e:
        print(f"Screen error: {e}")
        return None


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

def media_play_pause(text=None):
    pyautogui.press("playpause")
    return "Ок."

def media_next(text=None):
    pyautogui.press("nexttrack")
    return "Наступний трек."

def media_prev(text=None):
    pyautogui.press("prevtrack")
    return "Попередній трек."

def click_play(text=None):
    return media_play_pause()

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

# === ПАМ'ЯТЬ (JSON) ===
MEMORY_FILE = "core/memory.json"

def _load_memory():
    if not os.path.exists(MEMORY_FILE): return {}
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except: return {}

def _save_memory(data):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def remember_data(text, voice=None, listener=None):
    """Команда: Запам'ятай [ключ] [значення]"""
    clean = text.lower().replace("запам'ятай", "").replace("запиши", "").strip()
    parts = clean.split(" ", 1) # Ділимо по першому пробілу
    
    if len(parts) < 2: return "Що саме запам'ятати? Скажи: запам'ятай код 1234."
    
    key, value = parts[0], parts[1]
    data = _load_memory()
    data[key] = value
    _save_memory(data)
    return f"Записав: {key} — {value}."

def recall_data(text, voice=None, listener=None):
    """Команда: Нагадай [ключ]"""
    clean = text.lower().replace("нагадай", "").replace("що ти знаєш про", "").strip()
    data = _load_memory()
    
    if clean in data:
        return f"{clean}: {data[clean]}"
    
    # Шукаємо схоже
    for k, v in data.items():
        if clean in k: return f"Знайшов {k}: {v}"
        
    return "Я нічого такого не пам'ятаю."

def teach_alias(text, voice=None, listener=None):
    return "Функція навчання поки в розробці." # Заглушка, щоб не крашилось

def teach_response(text, voice=None, listener=None):
    return "Функція навчання відповідей поки в розробці."