#!/usr/bin/env python3
# main.py
"""
ValleRa - Ukrainian Voice Assistant

A voice-controlled AI assistant for Linux/Windows with Gemini/Gemma AI integration.
"""
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
import psutil
import logging
from datetime import datetime
from contextlib import contextmanager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('valera.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

colorama.init(autoreset=True)

# Налаштування діалогу
CONVERSATION_TIMEOUT = 60  # 1 хвилина активного діалогу
EXTEND_TIMEOUT = 45  # Продовжувати ще на 45 сек після кожної команди

@contextmanager
def ignore_stderr():
    """Перенаправляє потік помилок C-рівня в /dev/null"""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        old_stderr = os.dup(2)
        sys.stderr.flush()
        os.dup2(devnull, 2)
        os.close(devnull)
        try:
            yield
        finally:
            os.dup2(old_stderr, 2)
            os.close(old_stderr)
    except Exception:
        yield

def get_active_window():
    """Отримує назву активного вікна."""
    try:
        import subprocess
        # Linux - використовуємо xdotool або wmctrl
        result = subprocess.run(['xdotool', 'getactivewindow', 'getwindowname'], 
                             capture_output=True, text=True, timeout=1)
        if result.returncode == 0:
            name = result.stdout.strip()
            if name and name != "N/A":
                # Скорочуємо довгі назви
                if len(name) > 40:
                    name = name[:37] + "..."
                return name
        
        # Альтернатива - PID процесу
        result = subprocess.run(['xdotool', 'getactivewindow', 'getwindowpid'], 
                             capture_output=True, text=True, timeout=1)
        if result.returncode == 0:
            pid = result.stdout.strip()
            try:
                proc = psutil.Process(int(pid))
                return proc.name()
            except:
                pass
    except:
        pass
    return None

def get_system_status():
    """Отримує короткий статус системи."""
    try:
        cpu = psutil.cpu_percent()
        memory = psutil.virtual_memory()
        battery = None
        try:
            battery = psutil.sensors_battery()
        except:
            pass
        
        status = f"CPU: {cpu}% | RAM: {memory.percent}%"
        if battery:
            status += f" | 🔋 {battery.percent}%"
        return status
    except:
        return None

def main():
    os_name = platform.system()
    hostname = platform.node()
    
    logger.info(f"ValleRa starting on {os_name}")
    print(Fore.CYAN + "=======================================")
    print(Fore.CYAN + f"🚀 {config.NAME} (Neuro-Core) Запущено на {os_name}")
    print(Fore.GREEN + "💪 Повний режим: всі функції доступні")
    print(Fore.CYAN + "=======================================")

    try:
        with ignore_stderr():
            listener = Listener()
            voice = VoiceEngine()
        
        brain = CommandProcessor(voice, listener)
    except Exception as e:
        logger.error(f"Initialization error: {e}")
        print(Fore.RED + f"❌ Помилка ініціалізації: {e}")
        return
    
    voice.say(f"{config.NAME} на зв'язку.")
    logger.info("ValleRa initialized and ready")

    last_interaction_time = 0
    last_status_time = 0
    
    while True:
        try:
            current_time = time.time()
            time_passed = current_time - last_interaction_time
            is_active_dialog = time_passed < CONVERSATION_TIMEOUT
            time_left = max(0, int(CONVERSATION_TIMEOUT - time_passed))

            # Отримуємо активне вікно кожні 2 секунди
            if int(current_time) % 2 == 0:
                active_window = get_active_window()
            else:
                active_window = None

            if is_active_dialog:
                print(Fore.YELLOW + f"\n👂 [Діалог] Слухаю... ({time_left}с)")
                if active_window:
                    print(Fore.BLUE + f"   📱 Вікно: {active_window}")
            else:
                print(Fore.BLUE + "\n💤 [Очікування] Скажи 'Валєра'...")

            with ignore_stderr():
                user_input = listener.listen()
            
            if user_input:
                text = user_input.lower()
                triggers = config.TRIGGER_WORDS
                
                has_trigger = any(trigger in text for trigger in triggers)
                
                if has_trigger or is_active_dialog:
                    logger.info(f"User said: {user_input}")
                    print(Fore.WHITE + f"\n🗣️ Почув: {user_input}")
                    
                    # Показуємо контекст
                    if active_window:
                        print(Fore.CYAN + f"   📱 Активне вікно: {active_window}")
                    
                    print(Fore.GREEN + "⚡ Обробка...")

                    brain.process(text)
                    
                    last_interaction_time = time.time()
                    print(Fore.MAGENTA + f"⏳ Діалог активний ще {EXTEND_TIMEOUT}с")
                    
        except KeyboardInterrupt:
            print(Fore.RED + "\n🛑 Примусова зупинка.")
            logger.info("ValleRa stopped by user")
            break
        except Exception as e:
            logger.error(f"Critical error: {e}")
            print(Fore.RED + f"⚠️ Критична помилка: {e}")

if __name__ == "__main__":
    main()