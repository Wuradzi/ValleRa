#!/usr/bin/env python3
"""
Standalone ValleRa TTS Audio Generator
Run this to generate pre-recorded audio files for ValleRa.
"""
import asyncio
import edge_tts
import os
from pathlib import Path

# Кеш аудіо файлів
AUDIO_CACHE_DIR = os.path.expanduser("~/.valera_audio_cache")
VOICE = "uk-UA-OstapNeural"

# Попередньо записані відповіді
PRE_RECORDED = {
    # Базові відповіді
    "listening.mp3": "Слухаю!",
    "launching.mp3": "Запускаю.",
    "not_found.mp3": "Не знайшов.",
    "dont_understand.mp3": "Не розумію.",
    "help.mp3": "Ось що я вмію.",
    "timer_set.mp3": "Таймер запущено.",
    "shutting_down.mp3": "Вимикаю...",
    "locked.mp3": "Блоковано.",
    "error.mp3": "Помилка.",
    "ok.mp3": "Ок.",
    "all_good.mp3": "Все добре.",
    "hello.mp3": "Привіт!",
    "good_morning.mp3": "Доброго ранку!",
    "good_afternoon.mp3": "Добрий день!",
    "good_evening.mp3": "Добрий вечір!",
    "goodbye.mp3": "До побачення!",
    "note_saved.mp3": "Записано.",
    "notes_cleared.mp3": "Нотатки очищено.",
    "translation_saved.mp3": "Переклад збережено.",
    "searching.mp3": "Шукаю...",
    "weather.mp3": "Погода.",
    "cpu_status.mp3": "Процесор.",
    "memory.mp3": "Пам'ять.",
    "confirm_shutdown.mp3": "Точно вимкнути?",
    "timer_ended.mp3": "Таймер завершено.",
}


def ensure_cache_dir():
    """Створює директорію для кешу."""
    Path(AUDIO_CACHE_DIR).mkdir(parents=True, exist_ok=True)


async def generate_one(filename, text):
    """Генерує одне аудіо."""
    filepath = os.path.join(AUDIO_CACHE_DIR, filename)
    if os.path.exists(filepath):
        return f"✅ {filename}"
    
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(filepath)
    return f"🎵 {filename}"


async def pre_record_all():
    """Попередньо записує всі базові відповіді."""
    ensure_cache_dir()
    
    print("=" * 50)
    print("🎙️ ValleRa Audio Generator")
    print("=" * 50)
    print()
    
    tasks = []
    for filename, text in PRE_RECORDED.items():
        tasks.append(generate_one(filename, text))
    
    print("🎵 Генерування аудіо файлів...\n")
    
    results = await asyncio.gather(*tasks)
    
    print("\n" + "=" * 50)
    print("✅ Згенеровано файлів:", len(results))
    print("📁 Директорія:", AUDIO_CACHE_DIR)
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(pre_record_all())
