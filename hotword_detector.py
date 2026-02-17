#!/usr/bin/env python3
"""
ValleRa Hotword Detection - Always Listening Mode
Uses a background thread to listen for wake word continuously.
"""
import threading
import speech_recognition as sr
import time
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Configuration
HOTWORD_DETECTION_INTERVAL = 0.5  # Check every 0.5 seconds
WAKE_PHRASES = ["валера", "валєра", "валера", "боте", "волера"]

class HotwordDetector:
    """Background hotword detector using speech_recognition."""
    
    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.microphone = sr.Microphone()
        self.listening = False
        self.wake_event = threading.Event()
        self.running = False
        self.thread = None
        
        # Calibrate for ambient noise
        with self.microphone as source:
            print("🎤 Калібрую мікрофон для гарячого слова...")
            self.recognizer.adjust_for_ambient_noise(source, duration=1)
            print("✅ Мікрофон готовий.")
    
    def _listen_loop(self):
        """Background thread that listens continuously."""
        print("👂 Режим гарячого слова активний...")
        print("💡 Скажи 'Валєра' щоб активувати!")
        
        while self.running:
            try:
                with self.microphone as source:
                    # Short timeout for quick response
                    audio = self.recognizer.listen(source, timeout=1, phrase_time_limit=3)
                
                # Recognize speech
                try:
                    text = self.recognizer.recognize_google(audio, language="uk-UA").lower()
                    print(f"👂 Почув: '{text}'")
                    
                    # Check for wake phrase
                    for wake in WAKE_PHRASES:
                        if wake in text:
                            print(f"\n🔔 ВАЛЕРА! (detect: {wake})")
                            self.wake_event.set()
                            break
                            
                except sr.UnknownValueError:
                    pass  # Ignore unrecognized audio
                except sr.RequestError as e:
                    print(f"⚠️ STT Error: {e}")
                    
            except sr.WaitTimeoutError:
                pass  # Timeout is expected, continue listening
            except Exception as e:
                if self.running:
                    print(f"⚠️ Hotword error: {e}")
                time.sleep(0.1)
    
    def start(self):
        """Start hotword detection in background."""
        if self.thread and self.thread.is_alive():
            print("⚠️ Вже запущено!")
            return
        
        self.running = True
        self.wake_event.clear()
        self.thread = threading.Thread(target=self._listen_loop, daemon=True)
        self.thread.start()
        print("🚀 Гаряче слово запущено!")
    
    def wait_for_wake(self):
        """Wait for wake word detection."""
        return self.wake_event.wait()
    
    def clear_wake(self):
        """Clear wake event."""
        self.wake_event.clear()
    
    def stop(self):
        """Stop hotword detection."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        print("🛑 Гаряче слово зупинено.")
    
    def is_listening(self):
        """Check if still listening."""
        return self.running and self.thread and self.thread.is_alive()


def main():
    """Test hotword detection."""
    print("=" * 50)
    print("🎯 ValleRa Hotword Detection Test")
    print("=" * 50)
    
    detector = HotwordDetector()
    detector.start()
    
    try:
        print("\n⏳ Чекаю на 'Валєра'...\n")
        count = 0
        while count < 10:  # Test for 10 wake events
            if detector.wait_for_wake():
                count += 1
                print(f"\n✅ Пробудження #{count}!")
                print("👉 (Тут ValleRa Main активується)")
                
                # Simulate ValleRa main processing
                print("💭 ValleRa: 'Я слухаю...'\n")
                
                detector.clear_wake()
        
        print("\n🎉 Тест завершено!")
        
    except KeyboardInterrupt:
        print("\n🛑 Зупинка...")
    finally:
        detector.stop()


if __name__ == "__main__":
    main()
