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
import shlex

# === ВИЗНАЧЕННЯ СИСТЕМИ ===
SYSTEM = platform.system()
IS_WINDOWS = SYSTEM == "Windows"
IS_LINUX = SYSTEM == "Linux"

if IS_WINDOWS:
    import ctypes

APPS_CACHE = {} 
APPS_SCANNED = False
PENDING_CONFIRMATION = None  # Для підтвердження небезпечних команд

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

PENDING_CONFIRMATION = None  # For program launch confirmation

def open_program(text, voice=None, listener=None):
    global PENDING_CONFIRMATION
    
    _ensure_app_index()
    
    # Check for confirmation first
    if PENDING_CONFIRMATION and PENDING_CONFIRMATION["type"] == "program":
        if text and ("так" in text.lower() or "відкрий" in text.lower()):
            cmd = PENDING_CONFIRMATION["cmd"]
            try:
                subprocess.Popen(shlex.split(cmd), start_new_session=True)
                PENDING_CONFIRMATION = None
                return f"Запускаю {PENDING_CONFIRMATION['name']}."
            except:
                PENDING_CONFIRMATION = None
                return "Помилка запуску."
        else:
            PENDING_CONFIRMATION = None
    
    ignore_words = ["відкрий", "запусти", "включи", "open", "launch", "start", "програму", "апку", "будь ласка"]
    query = text.lower()
    for word in ignore_words:
        query = query.replace(word, "")
    query = query.strip()
    
    if not query: return "Яку програму?"
    
    # High confidence aliases
    aliases = {
        "браузер": "firefox",
        "хром": "google chrome",
        "код": "vscode",
        "редактор": "vscode",
    }
    if query in aliases:
        query = aliases[query]

    print(f"🔎 Шукаю програму: '{query}'")
    
    best_name = None
    best_cmd = None
    best_ratio = 0
    
    for app_name, app_cmd in APPS_CACHE.items():
        simple_app = _simplify_name(app_name)
        
        # Exact match gets 100%
        if simple_app == query:
            ratio = 100
        else:
            ratio = fuzz.ratio(query, simple_app)  # Use ratio, not partial_ratio
        
        if ratio > best_ratio:
            best_ratio = ratio
            best_name = app_name
            best_cmd = app_cmd
    
    # Higher threshold for less false positives
    HIGH_THRESHOLD = 90
    LOW_THRESHOLD = 75
    
    # Check PATH as fallback
    if best_ratio < LOW_THRESHOLD:
        from shutil import which
        if which(query):
            PENDING_CONFIRMATION = {"type": "program", "cmd": query, "name": query}
            return f"Знайшов '{query}' в системі. Відкрити? Скажи 'так'."
    
    if best_ratio >= HIGH_THRESHOLD:
        print(f"✅ Знайдено: {best_name} (Схожість: {best_ratio}%)")
        try:
            subprocess.Popen(shlex.split(best_cmd), start_new_session=True)
            return f"Запускаю {best_name}."
        except: return "Помилка запуску."
    
    if best_ratio >= LOW_THRESHOLD:
        # Ask for confirmation on lower confidence matches
        PENDING_CONFIRMATION = {"type": "program", "cmd": best_cmd, "name": best_name}
        return f"Можливо, ти маєш на увазі '{best_name}'? (Схожість: {best_ratio}%) Скажи 'так' для запуску."
    
    return f"Не знайшов програми '{query}'."

def is_app_name(text):
    """
    Перевіряє, чи схожий текст ВИКЛЮЧНО на назву програми.
    """
    _ensure_app_index()
    
    # First check if user explicitly asked to open something
    open_words = ["відкрий", "запусти", "включи", "open", "launch"]
    has_open_intent = any(word in text.lower() for word in open_words)
    
    if not has_open_intent:
        return False  # Don't assume it's a program if user didn't ask to open
    
    clean = text.lower()
    ignore = ["запусти", "відкрий", "включи", "open", "launch"]
    for w in ignore: clean = clean.replace(w, "").strip()
    
    if not clean: return False
    
    # Only match with high confidence
    for app_name in APPS_CACHE.keys():
        simple = _simplify_name(app_name)
        if fuzz.ratio(clean, simple) >= 90:
            return True
    
    return False

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
    try:
        if IS_WINDOWS: ctypes.windll.user32.LockWorkStation()
        else: subprocess.Popen(["cinnamon-screensaver-command", "--lock"])
        return "Блоковано."
    except: return "Помилка."

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
            import pyautogui
            pyautogui.moveRel(1, 0)
            pyautogui.moveRel(-1, 0)
            return "Активовано."
        return "Не вдалося активувати."
    except Exception as e:
        return f"Помилка: {e}"

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
    filename = f"screen_{datetime.datetime.now().strftime('%M%S')}.png"
    pyautogui.screenshot(filename)
    return filename

def look_at_screen():
    """Takes screenshot and returns path for AI vision."""
    filename = f"vision_{datetime.datetime.now().strftime('%M%S%f')}.png"
    try:
        pyautogui.screenshot(filename)
        return filename
    except Exception as e:
        print(f"Screenshot error: {e}")
        return None

def search_google(t): webbrowser.open(f"https://google.com/search?q={t.replace('гугл','').strip()}"); return "Шукаю."
def search_youtube_clip(t): webbrowser.open(f"https://www.youtube.com/results?search_query={t.replace('ютуб','').strip()}"); return "Ютуб."
MEMORY_FILE = os.path.expanduser("~/.valera_memory.json")

def _load_memory():
    """Завантажує пам'ять з файлу."""
    try:
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except:
        pass
    return {}

def _save_memory(data):
    """Зберігає пам'ять у файл."""
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except:
        pass

def remember_data(text, voice=None, listener=None):
    """Запам'ятовує дані. Формат: 'ключ: значення' або 'ключ = значення'"""
    try:
        # Шукаємо розділювач
        if ":" in text:
            key, value = text.split(":", 1)
        elif "=" in text:
            key, value = text.split("=", 1)
        else:
            # Якщо немає розділювача, все стає ключем
            return "Використай формат: 'запам'ятай ключ: значення'"
        
        key = key.strip().lower()
        value = value.strip()
        
        memory = _load_memory()
        memory[key] = value
        _save_memory(memory)
        
        return f"Запам'ятав: {key} = {value}"
    except Exception as e:
        return f"Помилка: {e}"

def recall_data(text, voice=None, listener=None):
    """Згадує запам'ятані дані."""
    query = text.lower().strip()
    memory = _load_memory()
    
    if not memory:
        return "Пам'ять порожня."
    
    # Шукаємо точний збіг
    if query in memory:
        return f"{query}: {memory[query]}"
    
    # Шукаємо частковий збіг
    for key, value in memory.items():
        if query in key or key in query:
            return f"{key}: {value}"
    
    # Показуємо все, якщо нічого не знайдено
    if "що ти знаєш" in query or "все" in query:
        if len(memory) <= 5:
            items = "\n".join([f"- {k}: {v}" for k, v in memory.items()])
            return f"Пам'ятаю:\n{items}"
        else:
            count = len(memory)
            keys = ", ".join(list(memory.keys())[:5])
            return f"Пам'ятаю {count} записів: {keys}..."
    
    return f"Не знайшов '{query}' в пам'яті."

def get_custom_knowledge(text):
    """Повертає користувацькі знання (заглушка для майбутнього)."""
    return ""

def teach_alias(t,v,l): return ""
def teach_response(t,v,l): return ""

# === НОВІ ФУНКЦІЇ ===

TIMERS = {}
NOTES_FILE = os.path.expanduser("~/.valera_notes.txt")

def get_time(text=None): return datetime.datetime.now().strftime("%H:%M")
def get_date(text=None): return str(datetime.date.today())

def timer(text, voice=None, listener=None):
    """Таймер: 'таймер 5 хвилин' або 'нагадай через 10 секунд'"""
    import re
    import time
    
    # Шукаємо число в тексті
    match = re.search(r'(\d+)', text)
    if not match:
        return "Скажи, скільки хвилин або секунд."
    
    value = int(match.group(1))
    duration = value
    
    # Визначаємо одиниці
    if "секунд" in text.lower() or "сек" in text.lower():
        duration = value
        unit = "секунд"
    elif "хвилин" in text.lower() or "хв" in text.lower():
        duration = value * 60
        unit = "хвилин"
    elif "годин" in text.lower() or "год" in text.lower():
        duration = value * 3600
        unit = "годин"
    else:
        # За замовчуванням - хвилини
        duration = value * 60
        unit = "хвилин"
    
    end_time = time.time() + duration
    TIMERS["active"] = end_time
    
    return f"Таймер на {value} {unit} запущено. Сповіщу через {value} {unit}."

def check_timers():
    """Перевіряє таймери (для виклику з циклу)."""
    import time
    if "active" in TIMERS:
        if time.time() >= TIMERS["active"]:
            del TIMERS["active"]
            return True
    return False

def calculator(text, voice=None, listener=None):
    """Простий калькулятор: 'порахуй 2+2' або 'скільки буде 10*5'"""
    import re
    
    # Витягуємо математичний вираз
    expr = text.lower()
    ignore_words = ["порахуй", "скільки", "буде", "скільки", "дорівнює", "равно"]
    for w in ignore_words:
        expr = expr.replace(w, "").strip()
    
    # Замінюємо слова на символи
    expr = expr.replace("×", "*").replace("х", "*").replace("×", "*")
    expr = expr.replace("÷", "/").replace(":", "/")
    expr = expr.replace("плюс", "+").replace("мінус", "-").replace("помножити", "*").replace("поділити", "/")
    
    # Залишаємо тільки цифри та оператори
    allowed = "0123456789+-*/(). "
    expr = ''.join(c for c in expr if c in allowed)
    
    try:
        result = eval(expr)
        # Форматуємо результат
        if result == int(result):
            result = int(result)
        return f"{expr} = {result}"
    except:
        return "Не розумію вираз. Скажи: 'порахуй 2+2'"

def list_processes(text=None, voice=None, listener=None):
    """Показує запущені процеси."""
    try:
        processes = []
        for p in psutil.process_iter(['name', 'cpu_percent']):
            try:
                info = p.info
                if info['cpu_percent'] > 0:
                    processes.append((info['name'], info['cpu_percent']))
            except:
                pass
        
        # Сортуємо за CPU
        processes.sort(key=lambda x: x[1], reverse=True)
        
        # Топ 10
        top = processes[:10]
        if not top:
            return "Немає активних процесів."
        
        lines = ["Топ процесів за CPU:"]
        for name, cpu in top[:5]:
            lines.append(f"- {name}: {cpu:.1f}%")
        
        return "\n".join(lines)
    except Exception as e:
        return f"Помилка: {e}"

def add_note(text, voice=None, listener=None):
    """Додає нотатку: 'запиши нотатку купити хліб'"""
    note = text.lower()
    ignore = ["запиши", "нотатку", "нотатка", "замітка", "додай", "запам'ятай"]
    for w in ignore:
        note = note.replace(w, "").strip()
    
    if not note:
        return "Що записати?"
    
    try:
        with open(NOTES_FILE, "a", encoding="utf-8") as f:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            f.write(f"[{timestamp}] {note}\n")
        return f"Записано: '{note}'"
    except Exception as e:
        return f"Помилка запису: {e}"

def show_notes(text=None, voice=None, listener=None):
    """Показує всі нотатки."""
    try:
        if not os.path.exists(NOTES_FILE):
            return "Немає збережених нотаток."
        
        with open(NOTES_FILE, "r", encoding="utf-8") as f:
            notes = f.read()
        
        if not notes.strip():
            return "Немає нотаток."
        
        # Показуємо останні 5
        lines = notes.strip().split("\n")[-5:]
        result = ["Останні нотатки:"]
        result.extend(lines)
        return "\n".join(result)
    except Exception as e:
        return f"Помилка: {e}"

def clear_notes(text=None, voice=None, listener=None):
    """Очищує всі нотатки."""
    try:
        if os.path.exists(NOTES_FILE):
            os.remove(NOTES_FILE)
        return "Нотатки очищено."
    except Exception as e:
        return f"Помилка: {e}"
