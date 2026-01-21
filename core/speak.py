import edge_tts
import asyncio
import pygame
import os

class VoiceEngine:
    def __init__(self):
        self.voice = 'uk-UA-OstapNeural'
        self.file = "response.mp3"
        self.audio_initialized = False
        try:
            pygame.mixer.init()
            self.audio_initialized = True
        except pygame.error as e:
            print(f"⚠️ Увага: аудіо-пристрій не знайдено. Помилка: {e}")

    async def _generate(self, text):
        communicate = edge_tts.Communicate(text, self.voice)
        await communicate.save(self.file)

    async def say_async(self, text):
        print(f"🤖 Валєра: {text}")
        
        try:
            await self._generate(text)
            
            if self.audio_initialized and pygame.mixer.get_init():
                pygame.mixer.music.load(self.file)
                pygame.mixer.music.play()
                
                while pygame.mixer.music.get_busy():
                    await asyncio.sleep(0.1)  # Асинхронне очікування
                
                pygame.mixer.music.unload()
            else:
                print("Аудіо не ініціалізовано, відтворення пропущено.")
            
        except Exception as e:
            print(f"❌ Помилка голосу: {e}")
        finally:
            if os.path.exists(self.file):
                try:
                    os.remove(self.file)
                except:
                    pass

    def say(self, text):
        # Синхронний виклик для сумісності
        asyncio.run(self.say_async(text))