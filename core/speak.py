import edge_tts
import asyncio
import pygame
import os
from core.tts_cache import get_audio_path, text_to_audio, AUDIO_CACHE_DIR

class VoiceEngine:
    def __init__(self):
        self.voice = 'uk-UA-OstapNeural'
        self.cache_dir = AUDIO_CACHE_DIR
        self.audio_initialized = False
        try:
            pygame.mixer.init()
            self.audio_initialized = True
        except: pass

    async def say_async(self, text):
        print(f"🤖 Валєра: {text}")
        cached_audio = get_audio_path(text)
        
        try:
            filepath = cached_audio if cached_audio else await text_to_audio(text)
            if self.audio_initialized and pygame.mixer.get_init():
                pygame.mixer.music.load(filepath)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy(): await asyncio.sleep(0.1)
                pygame.mixer.music.unload()
        except: pass

    def say(self, text): asyncio.run(self.say_async(text))