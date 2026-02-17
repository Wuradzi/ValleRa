# core/speak.py
import edge_tts
import asyncio
import pygame
import os
from core.tts_cache import get_audio_path, text_to_audio, AUDIO_CACHE_DIR

class VoiceEngine:
    def __init__(self):
        self.voice = 'uk-UA-OstapNeural'
        
        # Визначаємо абсолютний шлях до файлу
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.temp_file = os.path.join(base_dir, "response.mp3")
        self.cache_dir = AUDIO_CACHE_DIR
        
        self.audio_initialized = False
        try:
            pygame.mixer.init()
            self.audio_initialized = True
        except pygame.error as e:
            print(f"⚠️ Увага: аудіо-пристрій не знайдено. Помилка: {e}")

    def _find_cached_audio(self, text):
        """Шукає попередньо записаний аудіо файл."""
        normalized = " ".join(text.lower().split())
        
        # Шукаємо в попередньо записаних
        audio_path = get_audio_path(normalized)
        if audio_path:
            return audio_path
        
        # Шукаємо в кеші за хешем
        import hashlib
        cache_key = hashlib.md5(normalized.encode()).hexdigest()[:16]
        cached_file = os.path.join(self.cache_dir, f"{cache_key}.mp3")
        if os.path.exists(cached_file):
            return cached_file
        
        return None

    async def _play_audio(self, filepath):
        """Відтворює аудіо файл."""
        if not self.audio_initialized or not pygame.mixer.get_init():
            print("🔇 (Режим без звуку)")
            return
        
        try:
            pygame.mixer.music.load(filepath)
            pygame.mixer.music.play()
            
            while pygame.mixer.music.get_busy():
                await asyncio.sleep(0.1)
            
            pygame.mixer.music.unload()
        except pygame.error as e:
            print(f"⚠️ Помилка відтворення: {e}")

    async def say_async(self, text):
        print(f"🤖 Валєра: {text}")
        
        # Спочатку шукаємо в кеші
        cached_audio = self._find_cached_audio(text)
        
        if cached_audio:
            print(f"💾 Кеш аудіо: {os.path.basename(cached_audio)}")
            await self._play_audio(cached_audio)
            return
        
        # Якщо немає в кеші - генеруємо та кешуємо
        print("🎙️ Генерую аудіо...")
        
        try:
            filepath = await text_to_audio(text)
            
            if self.audio_initialized and pygame.mixer.get_init():
                try:
                    pygame.mixer.music.load(filepath)
                    pygame.mixer.music.play()
                    
                    while pygame.mixer.music.get_busy():
                        await asyncio.sleep(0.1)
                    
                    pygame.mixer.music.unload()
                except pygame.error:
                    print("⚠️ Помилка відтворення аудіо.")
            else:
                print("🔇 (Режим без звуку)")
            
        except Exception as e:
            print(f"❌ Помилка TTS: {e}")

    def say(self, text):
        asyncio.run(self.say_async(text))

    async def pre_record_responses(self):
        """Попередньо записує всі базові відповіді."""
        from core.tts_cache import pre_record_all
        await pre_record_all()
