import os
from dotenv import load_dotenv

# Завантажуємо змінні з файлу .env
load_dotenv()

# === ЗЧИТУЄМО КЛЮЧІ З .ENV ===
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

if not GOOGLE_API_KEY:
    print("⚠️ ПОМИЛКА: Не знайдено GOOGLE_API_KEY у файлі .env!")

# === НАЛАШТУВАННЯ АСИСТЕНТА ===
NAME = os.getenv("NAME", "Валєра")
# Слова-тригери (виправлено TIGGER -> TRIGGER)
TRIGGER_WORDS = ["валера", "валєра", "валерчик", "боте", "ей ти"]
LANGUAGE = "uk-UA"
SPEECH_RATE = 170

# === МОДЕЛІ (Те, чого не вистачало) ===
# Якщо в .env немає цих змінних, використаємо дефолтні
MAIN_MODEL = os.getenv("MAIN_MODEL", "gemini-2.0-flash")
VISION_MODEL = os.getenv("VISION_MODEL", "gemini-2.0-flash")
LOCAL_MODEL_LIGHT = os.getenv("LOCAL_MODEL", "gemma:2b")

# === ПАМ'ЯТЬ ===
HISTORY_LIMIT = 20