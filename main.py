# main.py
import config
from core.listen import Listener
from core.speak import VoiceEngine
from core.processor import CommandProcessor
import colorama
from colorama import Fore, Style
import time 

colorama.init(autoreset=True)

# ⏳ ЧАС УТРИМАННЯ УВАГИ
CONVERSATION_TIMEOUT = 30 

def main():
    print(Fore.CYAN + "=======================================")
    print(Fore.CYAN + f"🚀 {config.NAME} (Neuro-Core) Запущено")
    print(Fore.GREEN + "💪 Повний режим: всі функції доступні")
    print(Fore.CYAN + "=======================================")

    listener = Listener()
    voice = VoiceEngine()
    brain = CommandProcessor(voice, listener)
    
    voice.say(f"{config.NAME} на зв'язку.")

    last_interaction_time = 0   

    while True:
        try:
            # Розрахунок часу
            time_passed = time.time() - last_interaction_time
            is_active_dialog = time_passed < CONVERSATION_TIMEOUT
            time_left = int(CONVERSATION_TIMEOUT - time_passed)

            # Вивід статусу
            if is_active_dialog:
                print(Fore.YELLOW + f"\n👀 [Активний діалог] Слухаю все... (Залишилось {time_left}с)")
            else:
                print(Fore.BLUE + "\n💤 [Очікування] Скажи 'Валєра' для активації...")

            # Слухаємо (і не засуджуємо)
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
                    print(Fore.MAGENTA + f"⏳ Таймер оновлено! Діалог продовжено на {CONVERSATION_TIMEOUT}с.")
                    
                else:
                    # Ігноруємо шум
                    pass
                    
        except KeyboardInterrupt:
            print(Fore.RED + "\n🛑 Примусова зупинка.")
            break
        except Exception as e:
            print(Fore.RED + f"⚠️ Критична помилка: {e}")

if __name__ == "__main__":
    main()