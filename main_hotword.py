#!/usr/bin/env python3
"""
ValleRa - Ukrainian Voice Assistant with Hotword Detection
Run with: python main.py          # Normal mode (say "Валєра" each time)
Run with: python main_hotword.py   # Always listening mode (background hotword)
"""
import config
from core.listen import Listener
from core.speak import VoiceEngine
from core.processor import CommandProcessor
from hotword_detector import HotwordDetector
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

CONVERSATION_TIMEOUT = 60
EXTEND_TIMEOUT = 45

@contextmanager
def ignore_stderr():
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
    try:
        result = subprocess.run(['xdotool', 'getactivewindow', 'getwindowname'], 
                             capture_output=True, text=True, timeout=1)
        if result.returncode == 0:
            name = result.stdout.strip()
            if name and name != "N/A":
                if len(name) > 40:
                    name = name[:37] + "..."
                return name
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

def main_hotword():
    """Main function with always-listening hotword mode."""
    os_name = platform.system()
    
    print(Fore.CYAN + "=" * 50)
    print(Fore.CYAN + f"🚀 ValleRa (Hotword Mode)")
    print(Fore.CYAN + f"📍 {os_name} | Always Listening")
    print(Fore.CYAN + "=" * 50)
    
    # Initialize hotword detector
    print(Fore.YELLOW + "\n🎤 Ініціалізую гаряче слово...")
    hotword = HotwordDetector()
    
    try:
        with ignore_stderr():
            listener = Listener()
            voice = VoiceEngine()
        
        brain = CommandProcessor(voice, listener)
        
        voice.say(f"{config.NAME} на зв'язку в режимі гарячого слова!")
        logger.info("ValleRa initialized in hotword mode")
        
        # Start hotword detection
        hotword.start()
        
        print(Fore.GREEN + "\n✅ ValleRa готова!")
        print(Fore.BLUE + "💡 Скажи 'Валєра' для активації (або Ctrl+C для виходу)")
        print()
        
        conversation_active = False
        last_interaction_time = 0
        
        while True:
            try:
                current_time = time.time()
                time_passed = current_time - last_interaction_time
                is_conversation = time_passed < CONVERSATION_TIMEOUT
                time_left = max(0, int(CONVERSATION_TIMEOUT - time_passed))
                
                if is_conversation:
                    status = f"👂 Слухаю... ({time_left}с)"
                else:
                    status = "💤 Очікування 'Валєра'..."
                
                print(Fore.WHITE + status)
                
                if conversation_active:
                    # Listen for commands
                    with ignore_stderr():
                        user_input = listener.listen()
                    
                    if user_input:
                        text = user_input.lower()
                        triggers = config.TRIGGER_WORDS
                        
                        has_trigger = any(trigger in text for trigger in triggers)
                        
                        if has_trigger or is_conversation:
                            print(Fore.GREEN + f"\n🗣️ {user_input}")
                            
                            active_window = get_active_window()
                            if active_window:
                                print(Fore.CYAN + f"   📱 {active_window}")
                            
                            print(Fore.YELLOW + "⚡ Обробка...")
                            
                            brain.process(text)
                            
                            last_interaction_time = time.time()
                            print(Fore.MAGENTA + f"⏳ Діалог ще {EXTEND_TIMEOUT}с")
                            conversation_active = True
                else:
                    # Wait for hotword
                    if hotword.wait_for_wake():
                        print(Fore.GREEN + "\n🔔 ВАЛЕРА!")
                        voice.say("Слухаю!")
                        conversation_active = True
                        last_interaction_time = time.time()
                        hotword.clear_wake()
                        
            except KeyboardInterrupt:
                print(Fore.RED + "\n🛑 Вихід...")
                break
            except Exception as e:
                logger.error(f"Error: {e}")
                print(Fore.RED + f"⚠️ Помилка: {e}")
        
        hotword.stop()
        
    except Exception as e:
        print(Fore.RED + f"❌ Помилка ініціалізації: {e}")
        return

if __name__ == "__main__":
    main_hotword()
