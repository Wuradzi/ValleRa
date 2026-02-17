#!/usr/bin/env python3
"""
ValleRa TTS Cache - Pre-recorded audio responses
Uses local audio_cache directory in project.
"""
import os
import asyncio
import edge_tts
from pathlib import Path

# Кеш аудіо файлів - local directory in project
AUDIO_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audio_cache")
VOICE = "uk-UA-OstapNeural"

# Попередньо записані відповіді
PRE_RECORDED = {
    # Базові відповіді
    "слухаю": "listening.mp3",
    "я слухаю": "listening.mp3",
    "запускаю": "launching.mp3",
    "запускаю {name}": "launching.mp3",
    "не знайшов": "not_found.mp3",
    "не розумію": "dont_understand.mp3",
    "допомога": "help.mp3",
    "таймер на {value} {unit} запущено": "timer_set.mp3",
    "вимикаю": "shutting_down.mp3",
    "блоковано": "locked.mp3",
    "помилка": "error.mp3",
    "ok": "ok.mp3",
    "все добре": "all_good.mp3",
    
    # Попередні вітання
    "привіт": "hello.mp3",
    "доброго ранку": "good_morning.mp3",
    "добрий день": "good_afternoon.mp3",
    "добрий вечір": "good_evening.mp3",
    "до побачення": "goodbye.mp3",
    
    # Статус
    "cpu {percent}%": "cpu_status.mp3",
    "ram {percent}%": "ram_status.mp3",
    
    # Нотатки
    "записано": "note_saved.mp3",
    "нотатки очищено": "notes_cleared.mp3",
    
    # Переклад
    "переклад збережено": "translation_saved.mp3",
}


def ensure_cache_dir():
    """Створює директорію для кешу."""
    Path(AUDIO_CACHE_DIR).mkdir(parents=True, exist_ok=True)


async def generate_audio(text, filename):
    """Генерує аудіо для тексту."""
    communicate = edge_tts.Communicate(text, VOICE)
    filepath = os.path.join(AUDIO_CACHE_DIR, filename)
    await communicate.save(filepath)
    return filepath


async def pre_record_all():
    """Попередньо записує всі базові відповіді."""
    ensure_cache_dir()
    
    print("🎙️ Генерування попередньо записаних відповідей...")
    
    # Базові відповіді
    recordings = [
        ("listening.mp3", "Слухаю!"),
        ("launching.mp3", "Запускаю."),
        ("not_found.mp3", "Не знайшов."),
        ("dont_understand.mp3", "Не розумію."),
        ("help.mp3", "Ось список команд."),
        ("timer_set.mp3", "Таймер запущено."),
        ("shutting_down.mp3", "Вимикаю..."),
        ("locked.mp3", "Блоковано."),
        ("error.mp3", "Помилка."),
        ("ok.mp3", "Ок."),
        ("all_good.mp3", "Все добре."),
        ("hello.mp3", "Привіт!"),
        ("good_morning.mp3", "Доброго ранку!"),
        ("good_afternoon.mp3", "Добрий день!"),
        ("good_evening.mp3", "Добрий вечір!"),
        ("goodbye.mp3", "До побачення!"),
        ("note_saved.mp3", "Записано."),
        ("notes_cleared.mp3", "Нотатки очищено."),
        ("translation_saved.mp3", "Переклад збережено."),
    ]
    
    for filename, text in recordings:
        filepath = os.path.join(AUDIO_CACHE_DIR, filename)
        if not os.path.exists(filepath):
            print(f"  🎵 {filename}")
            await generate_audio(text, filename)
        else:
            print(f"  ✅ {filename} (вже є)")
    
    print(f"\n✅ Попередньо записані відповіді збережено в {AUDIO_CACHE_DIR}")


def get_audio_path(keyword):
    """Шукає аудіо файл за ключовим словом."""
    ensure_cache_dir()
    
    # Точний збіг
    if keyword in PRE_RECORDED:
        filename = PRE_RECORDED[keyword]
        filepath = os.path.join(AUDIO_CACHE_DIR, filename)
        if os.path.exists(filepath):
            return filepath
    
    # Частковий збіг
    for key, filename in PRE_RECORDED.items():
        if key in keyword or keyword in key:
            filepath = os.path.join(AUDIO_CACHE_DIR, filename)
            if os.path.exists(filepath):
                return filepath
    
    return None


async def text_to_audio(text, cache_key=None):
    """Перетворює текст на аудіо з кешуванням."""
    ensure_cache_dir()
    
    # Нормалізуємо текст
    normalized = " ".join(text.lower().split())
    
    # Шукаємо в кеші
    if cache_key:
        cached_file = os.path.join(AUDIO_CACHE_DIR, f"{cache_key}.mp3")
        if os.path.exists(cached_file):
            return cached_file
    
    # Генеруємо та кешуємо
    import hashlib
    filename = hashlib.md5(normalized.encode()).hexdigest()[:16] + ".mp3"
    filepath = os.path.join(AUDIO_CACHE_DIR, filename)
    
    if not os.path.exists(filepath):
        communicate = edge_tts.Communicate(text, VOICE)
        await communicate.save(filepath)
    
    return filepath


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "generate":
        # Генерувати всі попередньо записані файли
        asyncio.run(pre_record_all())
    else:
        # Показати статус
        ensure_cache_dir()
        print(f"📁 Audio cache: {AUDIO_CACHE_DIR}")
        print(f"📁 Files: {len(os.listdir(AUDIO_CACHE_DIR))}")
