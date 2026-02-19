#!/usr/bin/env python3
"""
ValleRa Text Mode - Test assistant with text commands
Run: python main_text.py
"""
import sys
import os

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.processor import CommandProcessor
from core.speak import VoiceEngine

# Mock voice that just prints
class MockVoice:
    def say(self, text):
        print(f"🔊 Голос: {text}")

class MockListener:
    def listen(self):
        return None

def main():
    print("=" * 50)
    print("🎯 ValleRa - Текстовий режим тестування")
    print("=" * 50)
    print()
    print("Доступні команди:")
    print("  - час / дата")
    print("  - таймер 5 хвилин")
    print("  - порахуй 2+2")
    print("  - відкрий браузер")
    print("  - допомога")
    print("  - нотатка купити хліб")
    print("  - переклади hello")
    print("  - погода")
    print("  - вихід")
    print()
    
    # Initialize
    voice = MockVoice()
    listener = MockListener()
    brain = CommandProcessor(voice, listener)
    
    print("✅ Валера готова!")
    print()
    
    # Interactive loop
    while True:
        try:
            user_input = input("👤 Ти: ").strip()
            
            if not user_input:
                continue
            
            if user_input.lower() in ["вихід", "exit", "quit", "q"]:
                print("👋 До побачення!")
                break
            
            print(f"\n⚡ Обробка: {user_input}")
            brain.process(user_input)
            print()
            
        except KeyboardInterrupt:
            print("\n👋 До побачення!")
            break
        except Exception as e:
            print(f"❌ Помилка: {e}")

if __name__ == "__main__":
    main()
