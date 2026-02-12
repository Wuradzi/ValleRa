import os
import datetime
import pyautogui
import webbrowser
import psutil
import json
import requests
import subprocess
import platform
import pyperclip
from thefuzz import fuzz 
from duckduckgo_search import DDGS

# === ВИЗНАЧЕННЯ СИСТЕМИ ===
SYSTEM = platform.system()
IS_WINDOWS = SYSTEM == "Windows"
IS_LINUX = SYSTEM == "Linux"

if IS_WINDOWS:
    import ctypes

APPS_CACHE = {} 
APPS_SCANNED = False

def search_internet(text):
    """Шукає інформацію в DuckDuckGo і повертає текст для читання."""
    query = text.replace("знайди інфу", "").replace("розкажи про", "").strip()
    print(f"🌎 Сканую інтернет: {query}")
    try:
        # Шукаємо 3 найкращі результати
        results = DDGS().text(query, max_results=3)
        if not results:
            return ""
        
        # Збираємо текст у купу
        summary = []
        for r in results:
            summary.append(f"- {r['title']}: {r['body']}")
        
        return "\n".join(summary)
    except Exception as e:
        print(f"Search error: {e}")
        return ""

def _ensure_app_index():
    global APPS_CACHE, APPS_SCANNED
    if APPS_SCANNED: return
    
    print(f"📂 Індексація програм ({SYSTEM})...")
    
    if IS_WINDOWS:
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
            
            # Обробка бінарників snap
            if path == "/snap/bin":
                for file in os.listdir(path):
                    APPS_CACHE[file.lower()] = os.path.join(path, file)
                continue

            # Обробка .desktop файлів
            for file in os.listdir(path):
                if file.endswith(".desktop"):
                    try:
                        full_path = os.path.join(path, file)
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                        
                        name = None
                        exec_cmd = None
                        
                        for line in content.split("\n"):
                            if line.startswith("Name=") and not name:
                                name = line.replace("Name=", "").strip().lower()
                            if line.startswith("Exec=") and not exec_cmd:
                                raw = line.replace("Exec=", "").strip()
                                exec_cmd = raw.split("%")[0].strip()
                                exec_cmd = exec_cmd.split("@@")[0].strip()
                                
                        if name and exec_cmd:
                            APPS_CACHE[name] = exec_cmd
                            file_key = file.lower().replace(".desktop", "")
                            APPS_CACHE[file_key] = exec_cmd
                    except: continue

    APPS_SCANNED = True
    print(f"✅ Програм в індексі: {len(APPS_CACHE)}")

def _simplify_name(name):
    """
    Перетворює технічну назву на людську для порівняння.
    org.telegram.desktop -> telegram
    code-oss -> code
    """
    clean = name.lower()
    for prefix in ["org.", "com.", "net.", "io.", "snap."]:
        if clean.startswith(prefix):
            clean = clean.replace(prefix, "")
    
    clean = clean.replace(".desktop", "").replace("-", " ").replace("_", " ")
    
    for trash in ["desktop", "client", "launcher", "studio", "viewer"]:
        clean = clean.replace(trash, "")
        
    return clean.strip()

def open_program(text, voice=None, listener=None):
    _ensure_app_index()
    
    ignore_words = ["відкрий", "запусти", "включи", "open", "launch", "start", "програму", "апку", "валера", "будь ласка"]
    query = text.lower()
    for word in ignore_words:
        query = query.replace(word, "")
    query = query.strip()
    
    if not query: return "Яку програму?"
    
    aliases = {
        "word": "libreoffice writer",
        "excel": "libreoffice calc",
        "powerpoint": "libreoffice impress",
        "браузер": "firefox",
        "хром": "google chrome"
    }
    if query in aliases:
        query = aliases[query]

    print(f"🔎 Шукаю (Fuzzy): '{query}'")
    
    best_name = None
    best_cmd = None
    best_ratio = 0
    
    for app_name, app_cmd in APPS_CACHE.items():
        simple_app = _simplify_name(app_name)
        
        ratio = fuzz.partial_ratio(query, simple_app)
        
        if simple_app == query:
            ratio = 100
            
        if ratio > best_ratio:
            best_ratio = ratio
            best_name = app_name
            best_cmd = app_cmd
    
    THRESHOLD = 75 
    
    if (best_ratio < THRESHOLD) and IS_LINUX:
        from shutil import which
        candidates = [query, query.replace(" ", "-")]
        for cand in candidates:
            if which(cand):
                print(f"🚀 Знайдено в system PATH: {cand}")
                subprocess.Popen(cand, shell=True, start_new_session=True)
                return f"Запускаю {cand}."

    if best_ratio >= THRESHOLD:
        print(f"✅ Знайдено: {best_name} (Схожість: {best_ratio}%) -> {best_cmd}")
        try:
            if IS_WINDOWS:
                os.startfile(best_cmd)
            else:
                subprocess.Popen(best_cmd, shell=True, start_new_session=True)
            return f"Запускаю {best_name}."
        except: return "Помилка запуску."
    
    return f"Не знайшов нічого схожого на {query}."

def is_app_name(text):
    """
    Перевіряє, чи схожий текст на назву програми.
    Потрібно для processor.py, щоб не плутати команди з балачками.
    """
    _ensure_app_index()
    clean = text.lower()
    ignore = ["запусти", "відкрий", "включи", "open", "launch", "app"]
    for w in ignore: clean = clean.replace(w, "").strip()
    if not clean: return False

    for app_name in APPS_CACHE.keys():
        simple = _simplify_name(app_name)
        if fuzz.partial_ratio(clean, simple) > 80:
            return True
            
    if IS_LINUX:
        from shutil import which
        if which(clean): return True
        
    return False

def turn_off_pc(text=None):
    if IS_WINDOWS: os.system("shutdown /s /t 30")
    else: subprocess.Popen(["systemctl", "poweroff"])
    return "Вимикаю..."

def cancel_shutdown(text=None):
    if IS_WINDOWS: os.system("shutdown /a")
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
        if not c: return "Пусто."
        print(f"📋: {c[:20]}...")
        return f"У буфері: {c}"
    except: return "Помилка буфера."

def system_status(text=None):
    return f"CPU: {psutil.cpu_percent()}%"

def check_weather(text):
    ignore_words = ["погода", "weather", "скажи", "яка", "зараз", "у", "в"]
    city = text.lower()
    for word in ignore_words:
        city = city.replace(f" {word} ", " ").replace(word, "")
    
    city = city.strip()
    
    print(f"🌍 Дивлюсь погоду для: '{city}'")

    try:
        if city:
            url = f"https://wttr.in/{city}?format=3&lang=uk"
        else:
            url = "https://wttr.in/?format=3&lang=uk"
            
        r = requests.get(url, timeout=5)
        
        if r.status_code == 200:
            return r.text.strip()
        else:
            return "Сайт погоди не відповідає."
            
    except Exception as e:
        print(f"Weather Error: {e}")
        return "Не можу з'єднатися з сервером погоди."

def get_time(text=None): return datetime.datetime.now().strftime("%H:%M")
def get_date(text=None): return str(datetime.date.today())
def volume_up(text=None): pyautogui.press('volumeup'); return "Гучніше."
def volume_down(text=None): pyautogui.press('volumedown'); return "Тихіше."
def media_play_pause(text=None): pyautogui.press("playpause"); return "Ок."
def media_next(text=None): pyautogui.press("nexttrack"); return "Далі."
def media_prev(text=None): pyautogui.press("prevtrack"); return "Назад."
def click_play(text=None): return media_play_pause()
def take_screenshot(text=None): 
    pyautogui.screenshot(f"screen_{datetime.datetime.now().strftime('%M%S')}.png")
    return "Скрін є."
def search_google(t): webbrowser.open(f"https://google.com/search?q={t.replace('гугл','').strip()}"); return "Шукаю."
def search_youtube_clip(t): webbrowser.open(f"https://www.youtube.com/results?search_query={t.replace('ютуб','').strip()}"); return "Ютуб."
def get_custom_knowledge(t): return ""
def remember_data(t,v,l): return "Записав."
def recall_data(t,v,l): return "Не знаю."
def teach_alias(t,v,l): return ""
def teach_response(t,v,l): return ""