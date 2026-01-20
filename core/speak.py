import edge_tts
import asyncio
import pygame
import os

class VoiceEngine:
    def __init__(self):
        try:
            pygame.mixer.init()
        except pygame.error:
            print("⚠️ Увага: аудіо-пристрій не знайдено (або це сервер).")

        self.voice = 'uk-UA-OstapNeural' # Можна змінити на жіночий 'uk-UA-PolinaNeural', якщо хочеш "подругу"
        self.file = "response.mp3"

    async def _generate(self, text):
        communicate = edge_tts.Communicate(text, self.voice)
        await communicate.save(self.file)

    def say(self, text):
        print(f"🤖 Валєра: {text}")
        
        try:
            asyncio.run(self._generate(text))
            
            if pygame.mixer.get_init():
                pygame.mixer.music.load(self.file)
                pygame.mixer.music.play()
                
                while pygame.mixer.music.get_busy():
                    pygame.time.Clock().tick(10)
                
                pygame.mixer.music.unload()
            
        except Exception as e:
            print(f"❌ Помилка голосу: {e}")