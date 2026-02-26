import os
import datetime
import webbrowser
import psutil
import json
import requests
import subprocess
import platform
import pyperclip
import threading
from thefuzz import fuzz 
from duckduckgo_search import DDGS
import shlex

# Lazy import for pyautogui
def _get_pyautogui():
    try:
        import pyautogui
        return pyautogui
    except Exception:
        return None

SYSTEM = platform.system()
IS_WINDOWS = SYSTEM == "Windows"
IS_LINUX = SYSTEM == "Linux"

if IS_WINDOWS:
    import ctypes

APPS_CACHE = {} 
APPS_SCANNED = False

def _ensure_app_index():
    global APPS_CACHE, APPS_SCANNED
    if APPS_SCANNED: return
    
    print(f"📂 Індексація програм ({SYSTEM})...")
    if IS_WINDOWS:
        paths = [
            r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs",
            os.path.expandvars(r"%AppData%\Microsoft\Windows\Start Menu\Programs")
        ]
        for path in paths:
            if not os.path.exists(path): continue
            for root, dirs, files in os.walk(path):
                for file in files:
                    if file.endswith((".lnk", ".url")):
                        name = file.lower().replace(".lnk", "").replace(".url", "")
                        APPS_CACHE[name] = os.path.join(root, file)

    elif IS_LINUX:
        paths = [
            "/usr/share/applications",
            os.path.expanduser("~/.local/share/applications"),
            "/var/lib/flatpak/exports/share/applications",
            "/var/lib/snapd/desktop/applications",
            "/snap/bin"
        ]
        for path in paths:
            if not os.path.exists(path): continue
            if path == "/snap/bin":
                for file in os.listdir(path):
                    APPS_CACHE[file.lower()] = os.path.join(path, file)
                continue
            for file in os.listdir(path):
                if file.endswith(".desktop"):
                    try:
                        full_path = os.path.join(path, file)
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                        name, exec_cmd = None, None
                        for line in content.split("\n"):
                            if line.startswith("Name=") and not name:
                                name = line.replace("Name=", "").strip().lower()
                            if line.startswith("Exec=") and not exec_cmd:
                                exec_cmd = line.replace("Exec=", "").strip().split("%")[0].split("@@")[0].strip()
                        if name and exec_cmd:
                            APPS_CACHE[name] = exec_cmd
                    except: continue
    APPS_SCANNED = True

def _simplify_name(name):
    clean = name.lower()
    for prefix in ["org.", "com.", "net.", "io.", "snap."]:
        if clean.startswith(prefix): clean = clean.replace(prefix, "")
    clean = clean.replace(".desktop", "").replace("-", " ").replace("_", " ")
    for trash in ["desktop", "client", "launcher", "studio", "viewer"]:
        clean = clean.replace(trash, "")
    return clean.strip()

def search_internet(text):
    query = text.replace("знайди інфу", "").replace("розкажи про", "").strip()
    try:
        results = DDGS().text(query, max_results=3)
        if not results: return ""
        return "\n".join([f"- {r['title']}: {r['body']}" for r in results])
    except: return ""

def open_program(text, voice=None, listener=None):
    _ensure_app_index()
    ignore_words = ["відкрий", "запусти", "включи", "open", "launch", "start", "програму", "апку", "будь ласка"]
    query = text.lower()
    for word in ignore_words: query = query.replace(word, "")
    query = query.strip()
    
    if not query: return "Яку програму?"
    
    aliases = {"браузер": "firefox", "хром": "google chrome", "код": "vscode", "редактор": "vscode"}
    if query in aliases: query = aliases[query]

    best_name, best_cmd, best_ratio = None, None, 0
    
    for app_name, app_cmd in APPS_CACHE.items():
        simple_app = _simplify_name(app_name)
        ratio = 100 if simple_app == query else fuzz.ratio(query, simple_app)
        if ratio > best_ratio:
            best_ratio, best_name, best_cmd = ratio, app_name, app_cmd
    
    if best_ratio >= 75:
        try:
            subprocess.Popen(shlex.split(best_cmd), start_new_session=True)
            return f"Запускаю {best_name}."
        except: return "Помилка запуску."
    
    from shutil import which
    if which(query):
        subprocess.Popen([query], start_new_session=True)
        return f"Запускаю {query}."
        
    return f"Не знайшов '{query}'."

def is_app_name(text):
    _ensure_app_index()
    open_words = ["відкрий", "запусти", "включи", "open", "launch"]
    if not any(word in text.lower() for word in open_words): return False
    
    clean = text.lower()
    for w in open_words: clean = clean.replace(w, "").strip()
    if not clean: return False
    
    for app_name in APPS_CACHE.keys():
        if fuzz.ratio(clean, _simplify_name(app_name)) >= 85: return True
    return False

# === СИСТЕМНІ ===
def turn_off_pc(text=None):
    if IS_WINDOWS: subprocess.Popen(["shutdown", "/s", "/t", "30"])
    else: subprocess.Popen(["systemctl", "poweroff"])
    return "Вимикаю комп'ютер..."

def cancel_shutdown(text=None):
    if IS_WINDOWS: subprocess.Popen(["shutdown", "/a"])
    else: subprocess.Popen(["shutdown", "-c"])
    return "Скасовано."

def lock_screen(text=None):
    try:
        if IS_WINDOWS: ctypes.windll.user32.LockWorkStation()
        else: subprocess.Popen(["cinnamon-screensaver-command", "--lock"])
        return "Блоковано."
    except: return "Помилка."

def close_app(text, voice=None, listener=None):
    q = text.lower().replace("закрий", "").replace("вбий", "").strip()
    for p in psutil.process_iter(['name']):
        try:
            if q in p.info['name'].lower():
                p.kill()
                return f"Вбив {q}."
        except: pass
    return f"Не знайшов {q}."

def read_clipboard(text=None, voice=None, listener=None):
    try:
        c = pyperclip.paste()
        return f"У буфері: {c}" if c else "Пусто."
    except: return "Помилка буфера."

def system_status(text=None):
    return f"CPU: {psutil.cpu_percent()}%"

def check_weather(text):
    ignore = ["погода", "weather", "скажи", "яка", "зараз", "у", "в"]
    city = text.lower()
    for word in ignore: city = city.replace(f" {word} ", " ").replace(word, "")
    city = city.strip()
    try:
        url = f"https://wttr.in/{city}?format=3&lang=uk" if city else "https://wttr.in/?format=3&lang=uk"
        r = requests.get(url, timeout=5)
        return r.text.strip() if r.status_code == 200 else "Помилка сервера."
    except: return "Немає інтернету."

# === МУЛЬТИМЕДІА ===
def get_time(text=None): return datetime.datetime.now().strftime("%H:%M")
def get_date(text=None): return str(datetime.date.today())
def volume_up(text=None): 
    if _get_pyautogui(): _get_pyautogui().press('volumeup')
    return "Гучніше."
def volume_down(text=None): 
    if _get_pyautogui(): _get_pyautogui().press('volumedown')
    return "Тихіше."
def media_play_pause(text=None): 
    if _get_pyautogui(): _get_pyautogui().press("playpause")
    return "Ок."
def media_next(text=None): 
    if _get_pyautogui(): _get_pyautogui().press("nexttrack")
    return "Далі."
def media_prev(text=None): 
    if _get_pyautogui(): _get_pyautogui().press("prevtrack")
    return "Назад."
def click_play(text=None): return media_play_pause()
def take_screenshot(text=None): 
    if _get_pyautogui():
        filename = f"screen_{datetime.datetime.now().strftime('%M%S')}.png"
        _get_pyautogui().screenshot(filename)
        return "Скрін є."
    return "Pyautogui не працює."
def look_at_screen():
    if _get_pyautogui():
        filename = f"vision_{datetime.datetime.now().strftime('%M%S%f')}.png"
        _get_pyautogui().screenshot(filename)
        return filename
    return None
def search_google(t): webbrowser.open(f"https://google.com/search?q={t.replace('гугл','').strip()}"); return "Шукаю."
def search_youtube_clip(t): webbrowser.open(f"https://www.youtube.com/results?search_query={t.replace('ютуб','').strip()}"); return "Ютуб."

# === ПАМ'ЯТЬ ТА НОТАТКИ ===
MEMORY_FILE = os.path.expanduser("~/.valera_memory.json")
NOTES_FILE = os.path.expanduser("~/.valera_notes.txt")

def _load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as f: return json.load(f)
    return {}

def remember_data(text, voice=None, listener=None):
    try:
        if ":" in text: key, value = text.split(":", 1)
        elif "=" in text: key, value = text.split("=", 1)
        else: return "Використай формат: 'запам'ятай ключ: значення'"
        key, value = key.replace("запам'ятай", "").strip().lower(), value.strip()
        memory = _load_memory()
        memory[key] = value
        with open(MEMORY_FILE, "w", encoding="utf-8") as f: json.dump(memory, f, ensure_ascii=False)
        return f"Запам'ятав: {key}."
    except Exception as e: return f"Помилка: {e}"

def recall_data(text, voice=None, listener=None):
    query = text.lower().replace("нагадай", "").replace("що ти знаєш", "").strip()
    memory = _load_memory()
    if not memory: return "Пам'ять порожня."
    if query in memory: return f"{query}: {memory[query]}"
    if not query or query == "про":
        return f"Пам'ятаю {len(memory)} записів: " + ", ".join(list(memory.keys())[:5])
    return "Не знайшов такого."

def add_note(text, voice=None, listener=None):
    note = text.lower()
    for w in ["запиши", "нотатку", "нотатка", "замітка", "додай"]: note = note.replace(w, "").strip()
    if not note: return "Що записати?"
    with open(NOTES_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}] {note}\n")
    return f"Записано."

def show_notes(text=None, voice=None, listener=None):
    if not os.path.exists(NOTES_FILE): return "Немає нотаток."
    with open(NOTES_FILE, "r", encoding="utf-8") as f:
        notes = f.read().strip()
    if not notes: return "Немає нотаток."
    return "Останні нотатки:\n" + "\n".join(notes.split("\n")[-3:])

def clear_notes(text=None, voice=None, listener=None):
    if os.path.exists(NOTES_FILE): os.remove(NOTES_FILE)
    return "Нотатки очищено."

def get_custom_knowledge(text): return ""

# === КОРИСНОСТІ (Таймер, Калькулятор, Переклад) ===
def timer(text, voice=None, listener=None):
    import re
    match = re.search(r'(\d+)', text)
    if not match: return "Скажи, скільки хвилин або секунд."
    
    val = int(match.group(1))
    seconds = val
    unit = "секунд"
    
    if "хвилин" in text.lower() or "хв" in text.lower(): 
        seconds = val * 60
        unit = "хвилин"
    elif "годин" in text.lower() or "год" in text.lower(): 
        seconds = val * 3600
        unit = "годин"
        
    def ring():
        if voice: voice.say(f"⏰ Увага! Таймер на {val} {unit} завершено!")
        else: print(f"\n⏰ ТАЙМЕР НА {val} {unit} ЗАВЕРШЕНО!")
        
    threading.Timer(seconds, ring).start()
    return f"Таймер на {val} {unit} запущено."

def calculator(text, voice=None, listener=None):
    expr = text.lower()
    for w in ["порахуй", "скільки", "буде", "дорівнює", "равно"]: expr = expr.replace(w, "").strip()
    expr = expr.replace("×", "*").replace("х", "*").replace("÷", "/").replace(":", "/")
    expr = expr.replace("плюс", "+").replace("мінус", "-").replace("помножити", "*").replace("поділити", "/")
    expr = ''.join(c for c in expr if c in "0123456789+-*/(). ")
    try:
        result = eval(expr)
        return f"{expr} = {int(result) if result == int(result) else result}"
    except: return "Не розумію вираз."

def list_processes(text=None, voice=None, listener=None):
    processes = sorted([(p.info['name'], p.info['cpu_percent']) for p in psutil.process_iter(['name', 'cpu_percent']) if p.info['cpu_percent'] > 0], key=lambda x: x[1], reverse=True)
    if not processes: return "Немає активних процесів."
    lines = [f"- {n}: {c:.1f}%" for n, c in processes[:5]]
    return "Топ процесів:\n" + "\n".join(lines)

def translate_text(text, voice=None, listener=None):
    target_lang, source_text = "uk", text.lower()
    if "на англійську" in text or "на english" in text:
        target_lang, source_text = "en", source_text.replace("на англійську", "").replace("на english", "")
    for w in ["переклади", "перекладіть", "переклад", "переведи"]: source_text = source_text.replace(w, "").strip()
    
    try:
        from googletrans import Translator
        result = Translator().translate(source_text, dest=target_lang)
        return f"Переклад: {result.text}"
    except: return "Немає перекладача. Встанови: pip install googletrans==4.0.0-rc1"