"""
Program Skills - Управление программами (открытие, закрытие, список процессов)
"""

import os
import platform
import psutil
import subprocess
import shlex
from thefuzz import fuzz
from shutil import which

SYSTEM = platform.system()
IS_WINDOWS = SYSTEM == "Windows"
IS_LINUX = SYSTEM == "Linux"

APPS_CACHE = {} 
APPS_SCANNED = False
PENDING_CONFIRMATION = None


def _ensure_app_index():
    """Индексирует установленные приложения."""
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
                    except: 
                        continue

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
    """Відкриває програму по назві."""
    global PENDING_CONFIRMATION
    
    _ensure_app_index()
    
    # Check for confirmation first
    if PENDING_CONFIRMATION and PENDING_CONFIRMATION.get("type") == "program":
        if text and ("так" in text.lower() or "відкрий" in text.lower()):
            cmd = PENDING_CONFIRMATION["cmd"]
            try:
                subprocess.Popen(shlex.split(cmd), start_new_session=True)
                PENDING_CONFIRMATION = None
                return f"Запускаю {PENDING_CONFIRMATION.get('name', '')}."
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
    
    if not query: 
        return "Яку програму?"
    
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
        
        if simple_app == query:
            ratio = 100
        else:
            ratio = fuzz.ratio(query, simple_app)
        
        if ratio > best_ratio:
            best_ratio = ratio
            best_name = app_name
            best_cmd = app_cmd
    
    HIGH_THRESHOLD = 90
    LOW_THRESHOLD = 75
    
    # Check PATH as fallback
    if best_ratio < LOW_THRESHOLD:
        if which(query):
            PENDING_CONFIRMATION = {"type": "program", "cmd": query, "name": query}
            return f"Знайшов '{query}' в системі. Відкрити? Скажи 'так'."
    
    if best_ratio >= HIGH_THRESHOLD:
        print(f"✅ Знайдено: {best_name} (Схожість: {best_ratio}%)")
        try:
            subprocess.Popen(shlex.split(best_cmd), start_new_session=True)
            return f"Запускаю {best_name}."
        except: 
            return "Помилка запуску."
    
    if best_ratio >= LOW_THRESHOLD:
        PENDING_CONFIRMATION = {"type": "program", "cmd": best_cmd, "name": best_name}
        return f"Можливо, ти маєш на увазі '{best_name}'? (Схожість: {best_ratio}%) Скажи 'так' для запуску."
    
    return f"Не знайшов програми '{query}'."


def is_app_name(text):
    """Перевіряє, чи схожий текст на назву програми."""
    _ensure_app_index()
    
    open_words = ["відкрий", "запусти", "включи", "open", "launch"]
    has_open_intent = any(word in text.lower() for word in open_words)
    
    if not has_open_intent:
        return False
    
    clean = text.lower()
    ignore = ["запусти", "відкрий", "включи", "open", "launch"]
    for w in ignore: 
        clean = clean.replace(w, "").strip()
    
    if not clean: 
        return False
    
    for app_name in APPS_CACHE.keys():
        simple = _simplify_name(app_name)
        if fuzz.ratio(clean, simple) >= 90:
            return True
    
    return False


def close_app(text, voice=None, listener=None):
    """Закриває програму по назві."""
    q = text.lower().replace("закрий", "").replace("вбий", "").strip()
    for p in psutil.process_iter(['name']):
        try:
            if q in p.info['name'].lower():
                p.kill()
                return f"Вбив {q}."
        except: 
            pass
    return f"Не знайшов {q}."


def list_processes(text=None, voice=None, listener=None):
    """Показує запущені процеси (топ за CPU)."""
    try:
        processes = []
        for p in psutil.process_iter(['name', 'cpu_percent']):
            try:
                info = p.info
                if info['cpu_percent'] > 0:
                    processes.append((info['name'], info['cpu_percent']))
            except:
                pass
        
        processes.sort(key=lambda x: x[1], reverse=True)
        
        top = processes[:10]
        if not top:
            return "Немає активних процесів."
        
        lines = ["Топ процесів за CPU:"]
        for name, cpu in top[:5]:
            lines.append(f"- {name}: {cpu:.1f}%")
        
        return "\n".join(lines)
    except Exception as e:
        return f"Помилка: {e}"
