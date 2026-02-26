import threading
import speech_recognition as sr
import time
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import TRIGGER_WORDS

class HotwordDetector:
    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.microphone = sr.Microphone()
        self.wake_event = threading.Event()
        self.running = False
        self.thread = None
        
        with self.microphone as source:
            print("🎤 Калібрую мікрофон для гарячого слова...")
            self.recognizer.adjust_for_ambient_noise(source, duration=1)
            print("✅ Мікрофон готовий.")
    
    def _listen_loop(self):
        print("👂 Режим гарячого слова активний...")
        while self.running:
            try:
                with self.microphone as source:
                    audio = self.recognizer.listen(source, timeout=1, phrase_time_limit=3)
                try:
                    text = self.recognizer.recognize_google(audio, language="uk-UA").lower()
                    if any(word in text for word in TRIGGER_WORDS):
                        self.wake_event.set()
                except: pass
            except sr.WaitTimeoutError: pass
            except Exception: pass
            time.sleep(0.1)

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._listen_loop, daemon=True)
        self.thread.start()
    
    def wait_for_wake(self):
        return self.wake_event.wait(timeout=0.5)
    
    def clear_wake(self):
        self.wake_event.clear()
    
    def stop(self):
        self.running = False
        if self.thread: self.thread.join(timeout=2)