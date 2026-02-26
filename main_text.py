#!/usr/bin/env python3
"""
ValleRa Text Mode - Test assistant with text commands (без AI)
Run: python main_text.py
"""
import sys
import os

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Импортуємо skills напряму
import skills
from thefuzz import fuzz

# Mock voice that just prints
class MockVoice:
    def say(self, text):
        print(f"🔊 Голос: {text}")

class MockListener:
    def listen(self):
        return None

class SimpleCommandProcessor:
    """Спрощена обробка команд без AI"""
    
    def __init__(self, voice_engine, listener):
        self.voice = voice_engine
        self.listener = listener
        
        # Швидкі команди (без інтернету)
        self.hard_commands = {
            ("час", "котра година"): skills.get_time,
            ("дата", "яке число", "яке число сьогодні"): skills.get_date,
            ("скрін", "фото екрану"): skills.take_screenshot,
            ("гучніше",): skills.volume_up,
            ("тихіше",): skills.volume_down,
            ("пауза", "продовжити", "музика", "стоп"): skills.media_play_pause,
            ("наступний", "наступна", "далі", "перемкни"): skills.media_next,
            ("попередній", "назад", "верни"): skills.media_prev,
            ("натисни", "клік"): skills.click_play,
            ("прочитай", "що в буфері", "озвуч"): skills.read_clipboard,
            ("статус", "система", "навантаження"): skills.system_status,
            ("закрий", "вбий"): skills.close_app,
            ("блокування", "заблокуй", "лок"): skills.lock_screen,
            ("запам'ятай", "запиши"): skills.remember_data,
            ("нагадай", "що ти знаєш"): skills.recall_data,
            ("буди", "прокинься", "активуй"): skills.wake_up_pc,
            ("таймер", "нагадай через", "через скільки"): skills.timer,
            ("порахуй", "скільки", "скільки буде"): skills.calculator,
            ("які процеси", "процеси", "запущені програми"): skills.list_processes,
            ("запиши нотатку", "додай нотатку", "нотатка"): skills.add_note,
            ("покажи нотатки", "нотатки", "що я записав"): skills.show_notes,
            ("очисти нотатки", "видали нотатки"): skills.clear_notes,
            ("переклади", "переклад", "переведи"): skills.translate_text,
            ("допомога", "команди", "що ти вмієш"): skills.get_help,
            ("погода", "прогноз"): skills.weather_forecast,
            ("знайди", "розкажи про"): skills.search_internet,
            ("гугл", "пошук"): skills.search_google,
            ("ютуб", "youtube"): skills.search_youtube_clip,
            ("відкрий", "запусти", "включи"): skills.open_program,
        }
    
    def process(self, text):
        """Обробляє текстову команду"""
        text_lower = text.lower()
        
        # Шукаємо точну команду
        for triggers, func in self.hard_commands.items():
            for trigger in triggers:
                if trigger in text_lower:
                    try:
                        result = func(text)
                        if result:
                            self.voice.say(result)
                        return
                    except Exception as e:
                        self.voice.say(f"Помилка: {e}")
                        return
        
        # Якщо не знайшли команду
        self.voice.say("Не розумію команду. Скажи 'допомога' для списку команд.")

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
    brain = SimpleCommandProcessor(voice, listener)
    
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

