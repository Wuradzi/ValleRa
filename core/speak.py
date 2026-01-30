# core/speak.py
import edge_tts
import asyncio
import pygame
import os

class VoiceEngine:
    def __init__(self):
        self.voice = 'uk-UA-OstapNeural'
        
        # Визначаємо абсолютний шлях до файлу, щоб не губити його
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.file = os.path.join(base_dir, "response.mp3")
        
        self.audio_initialized = False
        try:
            # На Linux іноді треба явно вказати частоту, але зазвичай auto працює
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
                try:
                    pygame.mixer.music.load(self.file)
                    pygame.mixer.music.play()
                    
                    while pygame.mixer.music.get_busy():
                        await asyncio.sleep(0.1)
                    
                    pygame.mixer.music.unload()
                except pygame.error:
                    print("⚠️ Помилка відтворення аудіо (можливо, зайнятий пристрій).")
            else:
                print("🔇 (Режим без звуку)")
            
        except Exception as e:
            print(f"❌ Помилка TTS: {e}")
        finally:
            if os.path.exists(self.file):
                try:
                    os.remove(self.file)
                except: pass

    def say(self, text):
        asyncio.run(self.say_async(text))