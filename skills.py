import datetime
import webbrowser
import subprocess
import pyautogui
import os
import psutil
import ctypes
import time
import requests
from geopy.geocoders import Nominatim
import json
import glob

try:
    from duckduckgo_search import DDGS
except ImportError:
    from ddgs import DDGS

MEMORY_FILE = "core/memory.json"
APPS_CACHE = {}

def clean_command(text, triggers):
    for trigger in triggers:
        text = text.replace(trigger, "")
    return text.strip()

# ===========================================
# ПАМ'ЯТЬ
# ===========================================

def _load_db():
    """Службова функція: читає базу з файлу"""
    if not os.path.exists(MEMORY_FILE):
        return {}
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def _save_db(data):
    """Службова функція: пише базу у файл"""
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def remember_data(text):
    """
    Команда: "Запам'ятай: [ключ] [значення]"
    Приклад: "Запам'ятай: пароль вайфай 1111"
    """
    # Відрізаємо слово "запам'ятай"
    # Очікуємо формат: "запам'ятай [щось] [значення]"
    clean_text = text.lower().replace("запам'ятай", "").replace("запиши", "").strip()
    
    # Спробуємо розділити по слову "це" або просто взяти перше слово як ключ
    # Але найпростіше - розділити по першому пробілу, якщо користувач каже "код 1234"
    parts = clean_text.split(" ", 1)
    
    if len(parts) < 2:
        return "Я не зрозумів, що саме запам'ятати. Скажи: 'Запам'ятай [назва] [значення]'."
    
    key = parts[0].strip()   # Наприклад: "пароль"
    value = parts[1].strip() # Наприклад: "1234"
    
    db = _load_db()
    db[key] = value
    _save_db(db)
    
    return f"Записав у пам'ять: {key} — {value}"

def recall_data(text):
    """
    Команда: "Що ти знаєш про [ключ]" або "Нагадай [ключ]"
    """
    db = _load_db()
    
    # Шукаємо ключове слово у запиті
    # Якщо користувач каже "Нагадай пароль", ми шукаємо "пароль" у базі
    found_keys = []
    
    for key, value in db.items():
        if key in text.lower():
            found_keys.append(f"{key}: {value}")
            
    if found_keys:
        return "Ось що я пам'ятаю: " + ", ".join(found_keys)
    else:
        # Якщо нічого конкретного не знайдено, перевіримо чи не питають "що ти пам'ятаєш" (все)
        if "все" in text or "список" in text:
            if not db:
                return "Моя пам'ять поки що пуста."
            return "У моїй базі є: " + ", ".join(db.keys())
            
        return "Я нічого такого не пам'ятаю."

def forget_data(text):
    """
    Команда: "Забудь [ключ]"
    """
    db = _load_db()
    deleted = []
    
    for key in list(db.keys()):
        if key in text.lower():
            del db[key]
            deleted.append(key)
            
    if deleted:
        _save_db(db)
        return f"Я стер з пам'яті інформацію про: {', '.join(deleted)}"
    else:
        return "Я не знайшов такого запису, щоб видалити."


# ==========================================
# РЕЖИМИ РОБОТИ
# ==========================================

def mode_study(text):
    """
    🎓 Режим навчання: 
    - Відкриває ChatGPT
    - Запускає Word
    """
    print("🎓 Активується режим навчання...")
    
    webbrowser.open("https://chatgpt.com")
    
    try:
        os.system("start winword")
    except:
        print("❌ Не вдалося відкрити Word.")
    
        
    return "Режим навчання увімкнено."


def mode_gaming(text):
    """
    🎮 Ігровий режим:
    - Запускає Steam
    - Згортає всі вікна (щоб очистити робочий стіл)
    """
    print("🎮 Активується ігровий режим...")
    
    pyautogui.hotkey('win', 'd')
    time.sleep(1)
    
    os.system("start steam://open/main")
    
        
    return "Ігровий режим активовано."

# ==========================================
# ІНТЕРНЕТ
# ==========================================

def search_internet(text):
    """
    Гуглить через DuckDuckGo (ddgs).
    """
    print(f"🔎 Отримав запит на очистку: '{text}'")

    triggers = [
        "розкажи мені про", "розкажи про", "знайди інформацію про", 
        "знайди інфу про", "інформація про", "хто такий", "що таке", 
        "де знаходиться", "погугли", "знайди", "валера"
    ]
    
    triggers.sort(key=len, reverse=True)
    
    query = text.lower()
    for t in triggers:
        if t in query:
            query = query.replace(t, "")
    
    query = query.strip()
    
    if not query:
        return None

    print(f"🌍 Валера реально шукає: '{query}'")

    try:
        results = DDGS().text(query, region="ua-uk", max_results=3)
        
        if not results:
            print("⚠️ Пошук повернув нуль.")
            return None

        knowledge_base = ""
        for res in results:
            title = res.get('title', '')
            body = res.get('body', '')
            href = res.get('href', '')
            knowledge_base += f"📌 {title}\n📄 {body}\n🔗 {href}\n\n"
            
        return knowledge_base

    except Exception as e:
        print(f"❌ Помилка DDGS: {e}")
        return None
    
def search_youtube_clip(text):
    """
    Тільки шукає, але не вмикає.
    """
    triggers = ["знайди", "ютубі", "на", "кліп", "відео", "групи", "пісню"]
    query = text
    for t in triggers:
        query = query.replace(t, "")
    
    query = query.strip()
    
    url = f"https://www.youtube.com/results?search_query={query}"
    webbrowser.open(url)
    
    time.sleep(2) 
    pyautogui.press('f11')
    
    return f"Ось що я знайшов по запиту {query}. Яке вмикаємо?"

def click_video_by_number(text):
    """
    Клікає по відео 1, 2 або 3, спираючись на їх координати.
    """
    screen_width, screen_height = pyautogui.size()
    
    target_x = screen_width * 0.4 
    
    start_y = screen_height * 0.30 
    gap = 200

    if "перше" in text or "один" in text or "1" in text:
        target_y = start_y
        video_num = "перше"
    elif "друге" in text or "два" in text or "2" in text:
        target_y = start_y + gap
        video_num = "друге"
    elif "третє" in text or "три" in text or "3" in text:
        target_y = start_y + (gap * 2)
        video_num = "третє"
    else:
        return "Яке відео? Скажіть 'перше', 'друге' або 'третє'."

    pyautogui.moveTo(target_x, target_y, duration=0.5)
    pyautogui.click()
    
    return f"Вмикаю {video_num} відео."

def check_weather(text):
    """
    1. Знаходить місто (навіть у відмінку) через Geopy.
    2. Отримує координати.
    3. Питає погоду по координатах.
    """
    ignore_words = ["яка", "погода", "скажи", "прогноз", " в ", " у ", "зараз", "валера", "валєра"]
    city_query = text.lower()
    for word in ignore_words:
        city_query = city_query.replace(word, "")
    city_query = city_query.strip()
    
    if not city_query:
        city_query = "Луцьк"

    print(f"🌍 Шукаю на карті: {city_query}")

    try:
        geolocator = Nominatim(user_agent="ValeraVoiceAssistant")
        location = geolocator.geocode(city_query)

        if location is None:
            return f"Я не знайшов міста {city_query} на карті."

        clean_city_name = location.address.split(",")[0]
        
        lat = location.latitude
        lon = location.longitude
        print(f"📍 Координати: {lat}, {lon} ({clean_city_name})")

        url = f"https://wttr.in/{lat},{lon}?format=4&lang=uk"
        
        response = requests.get(url, timeout=3)
        
        if response.status_code == 200:
            weather_text = response.text.strip()
            return f"Погода в локації {clean_city_name}: {weather_text}"
        else:
            return "Не вдалося отримати дані від метеостанції."

    except Exception as e:
        print(f"❌ Помилка погоди: {e}")
        return "Виникла помилка з визначенням місця."


def search_google(text):
    triggers = ["гугл", "google", "знайди", "пошукай", "в інтернеті", "загугли"]
    
    query = text.lower()
    for trigger in triggers:
        query = query.replace(trigger, "")
    
    query = query.strip()
    
    if not query:
        return "А що саме шукати? Ви не сказали."

    url = f"https://www.google.com/search?q={query}"
    
    webbrowser.open(url)
    
    return f"Шукаю {query}."

# ==========================================
# БАЗОВІ (Приймають text, але не використовують його)
# ==========================================
# Важливо: Всі функції тепер мають приймати аргумент 'text', 
# навіть якщо він їм не треба (щоб не було помилки в processor.py)

def get_time(text=None):
    now = datetime.datetime.now()
    return f"Зараз {now.hour} година {now.minute} хвилин."

def get_date(text=None):
    today = datetime.date.today()
    return f"Сьогодні {today.strftime('%d %B %Y')}."


def open_notepad(text=None):
    try:
        subprocess.Popen('notepad.exe')
        return "Блокнот відкрито."
    except: return "Помилка."

def open_calculator(text=None):
    try:
        subprocess.Popen('calc.exe')
        return "Калькулятор тут."
    except: return "Помилка."

def volume_up(text=None):
    for _ in range(5): pyautogui.press('volumeup')
    return "Гучніше."

def volume_down(text=None):
    for _ in range(5): pyautogui.press('volumedown')
    return "Тихіше."

def take_screenshot(text=None):
    # Створюємо унікальне ім'я з часом
    filename = f"screen_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    screenshot = pyautogui.screenshot()
    screenshot.save(filename)
    return "Скріншот збережено."

def stop_program(text=None):
    return "goodbye"


# ==========================================
# МОНІТОРИНГ СИСТЕМИ (Діагностика)
# ==========================================

def system_status(text=None):
    # Заряд батареї
    battery = psutil.sensors_battery()
    percent = battery.percent if battery else "невідомо"
    
    # Навантаження на ЦП і пам'ять
    cpu_usage = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory().percent
    
    return f"Доповідаю: заряд {percent}%, процесор навантажений на {cpu_usage}%, оперативка зайнята на {memory}%."

def get_battery(text=None):
    battery = psutil.sensors_battery()
    if not battery:
        return "Не бачу батареї, ми працюємо від розетки?"
    
    status = "заряджається" if battery.power_plugged else "розряджається"
    return f"Заряд {battery.percent} відсотків. Живлення {status}."

# ==========================================
# КЕРУВАННЯ ПРОЦЕСАМИ
# ==========================================

def _build_app_index():
    """
    Службова функція: сканує меню Пуск і складає список програм.
    """
    global APPS_CACHE
    if APPS_CACHE:
        return # Якщо вже сканували, не робимо це знову
    
    print("📂 Індексую встановлені програми...")
    
    # Шляхи до меню Пуск (System + User)
    paths = [
        r"C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs",
        os.path.expandvars(r"%AppData%\\Microsoft\\Windows\\Start Menu\\Programs")
    ]
    
    for path in paths:
        for root, dirs, files in os.walk(path):
            for file in files:
                if file.endswith(".lnk") or file.endswith(".url"):
                    clean_name = file.lower().replace(".lnk", "").replace(".url", "")
                    full_path = os.path.join(root, file)
                    APPS_CACHE[clean_name] = full_path
                    
    print(f"✅ Знайдено {len(APPS_CACHE)} програм.")

def open_program(text, voice=None, listener=None):
    """
    Розумний запуск програм з уточненням.
    """
    _build_app_index()
    
    # Чистка
    ignore = ["відкрий", "запусти", "включи", "програму", "валера", "будь ласка"]
    query = text.lower()
    for word in ignore:
        query = query.replace(word, "")
    query = query.strip()
    
    # === ГОЛОВНА ЗМІНА: ЯКЩО НАЗВИ НЕМАЄ ===
    if not query:
        if voice and listener:
            voice.say("Яку програму відкрити?")
            
            print("👂 Слухаю уточнення...")
            answer = listener.listen()
            
            if answer:
                query = answer.lower()
                print(f"🗣️ Користувач уточнив: {query}")
            else:
                return "Я нічого не почув. Скасування."
        else:
            return "Яку програму треба відкрити?"

    print(f"🔎 Шукаю програму: '{query}'")
    best_match = None
    
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
            return "Файл знайдено, але не вдалося запустити."
    else:
        return f"Я не знайшов програми з назвою {query}."

def is_app_name(text):
    """
    Перевіряє, чи є текст назвою програми.
    Повертає True, якщо знайдено збіг.
    """
    # Переконуємось, що індекс побудований
    _build_app_index()
    
    query = text.lower().strip()
    
    # Проходимо по всіх відомих програмах
    for app_name in APPS_CACHE.keys():
        # Якщо те, що ми сказали, є в назві програми (наприклад "стім" в "steam")
        if query in app_name and len(query) > 2: # >2 щоб не реагував на "я", "ти"
            return True
            
    return False

def close_app(text):
    apps = {
        "браузер": "firefox.exe",
        "хром": "chrome.exe",
        "телеграм": "Telegram.exe",
        "стім": "steam.exe",
        "калькулятор": "calc.exe", 
        "блокнот": "notepad.exe",  
        "діскорд": "Discord.exe",
        "ворд": "WINWORD.EXE"
    }
    
    command_lower = text.lower()
    closed_something = False
    
    for app_name, process_name in apps.items():
        if app_name in command_lower:
            os.system(f"taskkill /f /im {process_name}")
            closed_something = True
    
    if closed_something:
        return "Закрив, шеф."
    else:
        return "Не зрозумів, яку саме програму закрити."

# ==========================================
# 🔒 РОЗДІЛ 7: БЕЗПЕКА ТА ЖИВЛЕННЯ
# ==========================================

def lock_screen(text=None):
    # Блокування Windows (Win + L)
    ctypes.windll.user32.LockWorkStation()
    return "Систему заблоковано."

def turn_off_pc(text=None):
    # Вимкнення через 30 секунд (щоб встигнути скасувати, якщо передумав)
    os.system("shutdown /s /t 30")
    return "Вимкнення живлення через 30 секунд. Прощавайте."

def cancel_shutdown(text=None):
    os.system("shutdown /a")
    return "Вимкнення скасовано. Працюємо далі."

def restart_pc(text=None):
    os.system("shutdown /r /t 30")
    return "Йду на перезавантаження."

# ==========================================
# 👁️ РОЗДІЛ 8: КОМП'ЮТЕРНИЙ ЗІР
# ==========================================

def click_target(target_name):
    """
    Шукає картинку на екрані і клікає по ній.
    target_name: назва файлу без шляху (наприклад, 'play_button.png')
    """
    # Шлях до папки з картинками
    image_path = os.path.join("assets", target_name)
    
    if not os.path.exists(image_path):
        return f"Я не знаю, як виглядає {target_name}. Додайте картинку в папку assets."

    try:

        location = pyautogui.locateCenterOnScreen(image_path, confidence=0.8, grayscale=True)
        
        if location:
            pyautogui.moveTo(location) 
            pyautogui.click()          
            return "Бачу ціль. Натискаю."
        else:
            return "Дивлюсь на екран, але не бачу цього об'єкта."
            
    except Exception as e:
        return f"Помилка зору: {e}"

def click_play(text=None):
    return click_target("play_button.png")

def find_video(text=None):
    return click_target("youtube_logo.png")

# ==========================================
# 🎤 МІКРОФОН (КАЛІБРАЦІЯ)
# ==========================================

def recalibrate_mic(text, voice=None, listener=None):
    """
    Запускає повторну калібрацію мікрофону.
    """
    if not listener:
        return "Помилка: я не маю доступу до мікрофону."
    
    if voice:
        voice.say("Тссс... Слухаю тишу.")
    
    print("\n🎧 Перекалібровка...")
    # Викликаємо метод калібрації з класу Listener
    listener.calibrate_noise()
    
    return "Мікрофон налаштовано."


# ==========================================
# 🧠 НАВЧАННЯ (ALIAS + QA)
# ==========================================

LEARNING_FILE = "core/learning.json"

def _load_learning():
    if not os.path.exists(LEARNING_FILE):
        return {"aliases": {}, "qa": {}}
    try:
        with open(LEARNING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"aliases": {}, "qa": {}}

def _save_learning(data):
    # Переконуємось, що папка core існує
    os.makedirs(os.path.dirname(LEARNING_FILE), exist_ok=True)
    with open(LEARNING_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def teach_alias(text, voice=None, listener=None):
    """
    Вчить синоніми програм.
    Приклад: "Запам'ятай що танки це world of tanks"
    """
    # Чистимо текст
    clean_text = text.lower().replace("запам'ятай що", "").replace("вивчи", "").strip()
    
    if " це " not in clean_text:
        return "Скажи так: 'Запам'ятай що [коротка назва] це [повна назва]'"
    
    parts = clean_text.split(" це ")
    alias = parts[0].strip()      
    real_name = parts[1].strip()  
    
    data = _load_learning()
    data["aliases"][alias] = real_name
    _save_learning(data)
    
    return f"Записав: {alias} -> {real_name}"

def teach_response(text, voice=None, listener=None):
    """
    Вчить відповіді.
    Приклад: "Якщо я скажу привіт відповідай здоров"
    """
    clean_text = text.lower().replace("якщо я скажу", "").strip()
    
    if "відповідай" not in clean_text:
        return "Скажи так: 'Якщо я скажу [фраза] відповідай [відповідь]'"
        
    parts = clean_text.split("відповідай")
    trigger = parts[0].strip()
    response = parts[1].strip()
    
    data = _load_learning()
    data["qa"][trigger] = response
    _save_learning(data)
    
    return f"Домовилися. Тепер я знаю, що відповідати на '{trigger}'."

def get_custom_knowledge(text):
    """
    Шукає інформацію у папці knowledge.
    Якщо знаходить файл, де згадуються слова із запиту — повертає його зміст.
    """
    knowledge_dir = "core/knowledge"
    if not os.path.exists(knowledge_dir):
        return ""

    text = text.lower()
    found_info = ""

    # Проходимо по всіх .txt файлах
    for filename in os.listdir(knowledge_dir):
        if filename.endswith(".txt"):
            path = os.path.join(knowledge_dir, filename)
            
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                    
                    # Перевіряємо, чи є ключові слова з файлу у запиті
                    # Наприклад, якщо файл називається "розклад.txt", а ти спитав "який розклад"
                    name_without_ext = filename.replace(".txt", "").lower()
                    
                    if name_without_ext in text or name_without_ext in content.lower()[:50]: 
                         # (тут можна зробити розумніший пошук, але для початку вистачить назви файлу)
                         found_info += f"\n--- Інформація з файлу {filename} ---\n{content}\n"
            except:
                pass


    return found_info

def look_at_screen(text=None):
    """
    Робить скріншот і зберігає його для аналізу ШІ.
    """
    filename = "vision_buffer.png"
    print("📸 Роблю знімок для аналізу...")
    
    try:
        screenshot = pyautogui.screenshot()
        screenshot.save(filename)
        return filename 
    except Exception as e:
        print(f"Помилка скріншоту: {e}")
        return None