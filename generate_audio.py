import asyncio
import os
import edge_tts

AUDIO_CACHE_DIR = "audio_cache"
VOICE = "uk-UA-OstapNeural"

# Список фраз, які Валєра має відповідати миттєво
PHRASES = {
    "listening.mp3": "Слухаю",
    "launching.mp3": "Запускаю програму",
    "not_found.mp3": "На жаль, я цього не знайшов",
    "error.mp3": "Сталася помилка",
    "ok.mp3": "Окей",
    "calc.mp3": "Пишу код..."
}

async def generate():
    # Створюємо папку, якщо її немає
    os.makedirs(AUDIO_CACHE_DIR, exist_ok=True)
    print("🎙️ Створюю базовий кеш аудіо (потрібен інтернет)...")
    
    for filename, text in PHRASES.items():
        filepath = os.path.join(AUDIO_CACHE_DIR, filename)
        if not os.path.exists(filepath):
            print(f"⏳ Записую: '{text}' -> {filename}")
            comm = edge_tts.Communicate(text, VOICE)
            await comm.save(filepath)
        else:
            print(f"✅ {filename} вже існує.")
            
    print("🎉 Кеш успішно створено! Можна запускати Валєру.")

if __name__ == "__main__":
    asyncio.run(generate())