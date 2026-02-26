#!/usr/bin/env python3
import os
import sys

# ГЛУШНИК ALSA
from ctypes import *
try:
    ERROR_HANDLER_FUNC = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)
    def py_error_handler(filename, line, function, err, fmt): pass
    c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)
    asound = cdll.LoadLibrary('libasound.so.2')
    asound.snd_lib_error_set_handler(c_error_handler)
except: pass
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"

import config
from core.listen import Listener
from core.speak import VoiceEngine
from core.processor import CommandProcessor
from hotword_detector import HotwordDetector
import colorama
from colorama import Fore
import time 
import platform
import subprocess
import logging
from contextlib import contextmanager
import signal
import re

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', handlers=[logging.FileHandler('valera.log')])
logger = logging.getLogger(__name__)
colorama.init(autoreset=True)

CONVERSATION_TIMEOUT = 60
EXTEND_TIMEOUT = 45

def cleanup_audio_cache():
    cache_dir = "audio_cache"
    if os.path.exists(cache_dir):
        try:
            deleted_count = 0
            for filename in os.listdir(cache_dir):
                if re.match(r'^[a-f0-9]{16}\.mp3$', filename):
                    os.remove(os.path.join(cache_dir, filename))
                    deleted_count += 1
            if deleted_count > 0:
                print(Fore.GREEN + f"✅ Очищено {deleted_count} тимчасових аудіофайлів")
        except: pass

def signal_handler(sig, frame):
    print(Fore.RED + "\n🛑 Вихід...")
    cleanup_audio_cache()
    sys.exit(0)

@contextmanager
def ignore_stderr():
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        old_stderr = os.dup(2)
        sys.stderr.flush()
        os.dup2(devnull, 2)
        os.close(devnull)
        try: yield
        finally:
            os.dup2(old_stderr, 2)
            os.close(old_stderr)
    except: yield

def get_active_window():
    try:
        res = subprocess.run(['xdotool', 'getactivewindow', 'getwindowname'], capture_output=True, text=True, timeout=1)
        if res.returncode == 0 and res.stdout.strip() and res.stdout.strip() != "N/A": return res.stdout.strip()[:37] + "..."
    except: pass
    return None

def main():
    signal.signal(signal.SIGINT, signal_handler)
    print(Fore.CYAN + "=" * 50)
    print(Fore.CYAN + f"🚀 {config.NAME} | OS: {platform.system()}")
    print(Fore.CYAN + "=" * 50)
    
    with ignore_stderr():
        hotword = HotwordDetector()
        listener = Listener()
        voice = VoiceEngine()
    
    brain = CommandProcessor(voice, listener)
    
    with ignore_stderr():
        voice.say(f"{config.NAME} на зв'язку!")
        hotword.start()
    
    print(Fore.GREEN + f"\n✅ Готово! Скажи '{config.TRIGGER_WORDS[0]}' для активації.")
    
    conversation_active = False
    last_interaction_time = 0
    
    while True:
        try:
            time_passed = time.time() - last_interaction_time
            is_conversation = time_passed < CONVERSATION_TIMEOUT
            
            if conversation_active:
                if not is_conversation:
                    conversation_active = False
                    print(Fore.BLUE + "\n💤 Перехід в режим очікування...")
                    continue
                
                with ignore_stderr():
                    user_input = listener.listen()
                
                if user_input:
                    text = user_input.lower()
                    if any(t in text for t in config.TRIGGER_WORDS) or is_conversation:
                        print(f"\n{Fore.WHITE}🗣️ Почув: {user_input}")
                        win = get_active_window()
                        if win: print(Fore.CYAN + f"   📱 {win}")
                        print(Fore.YELLOW + "⚡ Обробка...")
                        
                        with ignore_stderr():
                            brain.process(text)
                        
                        last_interaction_time = time.time()
            else:
                if hotword.wait_for_wake():
                    print(Fore.GREEN + "\n🔔 АКТИВАЦІЯ!")
                    with ignore_stderr(): voice.say("Слухаю!")
                    conversation_active = True
                    last_interaction_time = time.time()
                    hotword.clear_wake()
        except KeyboardInterrupt:
            signal_handler(signal.SIGINT, None)
        except Exception as e:
            print(Fore.RED + f"\n⚠️ Помилка: {e}")

if __name__ == "__main__":
    main()