#!/usr/bin/env python3
# main.py
import config
from core.listen import Listener
from core.speak import VoiceEngine
from core.processor import CommandProcessor
import colorama
from colorama import Fore, Style
import time 
import platform
import os
import sys
from contextlib import contextmanager

# На Linux autoreset іноді працює інакше, але init() корисний
colorama.init(autoreset=True)

CONVERSATION_TIMEOUT = 30 

# === 🔇 УНІВЕРСАЛЬНИЙ ГЛУШНИК (ALSA + JACK) ===
@contextmanager
def ignore_stderr():
    """Перенаправляє потік помилок C-рівня в /dev/null"""
    try:
        # Відкриваємо "чорну діру"
        devnull = os.open(os.devnull, os.O_WRONLY)
        # Зберігаємо оригінальний stderr (щоб потім відновити)
        old_stderr = os.dup(2)
        sys.stderr.flush()
        # Перенаправляем stderr у devnull
        os.dup2(devnull, 2)
        os.close(devnull)
        try:
            yield
        finally:
            # Відновлюємо stderr назад
            os.dup2(old_stderr, 2)
            os.close(old_stderr)
    except Exception:
        # Якщо щось пішло не так (наприклад, на Windows), просто працюємо як є
        yield
# ===============================================

def main():
    os_name = platform.system()
    print(Fore.CYAN + "=======================================")
    print(Fore.CYAN + f"🚀 {config.NAME} (Neuro-Core) Запущено на {os_name}")
    print(Fore.GREEN + "💪 Повний режим: всі функції доступні")
    print(Fore.CYAN + "=======================================")

    try:
        # Глушимо шум під час ініціалізації мікрофона
        with ignore_stderr():
            listener = Listener()
            voice = VoiceEngine()
        
        brain = CommandProcessor(voice, listener)
    except Exception as e:
        print(Fore.RED + f"❌ Помилка ініціалізації: {e}")
        return
    
    voice.say(f"{config.NAME} на зв'язку.")

    last_interaction_time = 0

    while True:
        try:
            time_passed = time.time() - last_interaction_time
            is_active_dialog = time_passed < CONVERSATION_TIMEOUT
            time_left = int(CONVERSATION_TIMEOUT - time_passed)

            if is_active_dialog:
                print(Fore.YELLOW + f"\n👀 [Активний діалог] Слухаю... ({time_left}с)")
            else:
                print(Fore.BLUE + "\n💤 [Очікування] Скажи 'Валєра'...")

            # 🔥 ГЛУШИМО ШУМ ПІД ЧАС ПРОСЛУХОВУВАННЯ
            with ignore_stderr():
                user_input = listener.listen()
            
            if user_input:
                text = user_input.lower()
                triggers = ["валера", "валєра", "валерчик", "valera", "бот"]
                
                has_trigger = any(trigger in text for trigger in triggers)
                
                if has_trigger or is_active_dialog:
                    print(Fore.WHITE + f"🗣️ Почув: {user_input}")
                    print(Fore.GREEN + "⚡ Обробка...")

                    brain.process(text)
                    
                    last_interaction_time = time.time()
                    print(Fore.MAGENTA + f"⏳ Таймер оновлено!")
                    
        except KeyboardInterrupt:
            print(Fore.RED + "\n🛑 Примусова зупинка.")
            break
        except Exception as e:
            print(Fore.RED + f"⚠️ Критична помилка: {e}")

if __name__ == "__main__":
    main()