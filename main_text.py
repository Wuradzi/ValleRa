#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.processor import CommandProcessor

class MockVoice:
    def say(self, text): print(f"🔊 Голос: {text}")

class MockListener:
    def listen(self): return None

def main():
    print("=" * 50)
    print("🎯 ValleRa - Текстовий режим")
    print("=" * 50)
    voice = MockVoice()
    listener = MockListener()
    brain = CommandProcessor(voice, listener)
    print("✅ Готово! Вводь команди.\n")
    
    while True:
        try:
            user_input = input("👤 Ти: ").strip()
            if not user_input: continue
            if user_input.lower() in ["вихід", "exit", "q"]: break
            brain.process(user_input)
            print()
        except KeyboardInterrupt: break

if __name__ == "__main__":
    main()