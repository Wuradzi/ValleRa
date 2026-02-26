# config.py
import os
from dotenv import load_dotenv

load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    print("⚠️ ПОМИЛКА: Не знайдено GOOGLE_API_KEY у файлі .env!")

NAME = os.getenv("NAME", "Валєра")
TRIGGER_WORDS = ["валера", "валєра", "валерчик", "боте", "ей ти"]
LANGUAGE = "uk-UA"

MAIN_MODEL = os.getenv("MAIN_MODEL", "gemini-2.0-flash")
VISION_MODEL = os.getenv("VISION_MODEL", "gemini-2.0-flash")
LOCAL_MODEL_LIGHT = os.getenv("LOCAL_MODEL", "gemma:2b")

HISTORY_LIMIT = 20