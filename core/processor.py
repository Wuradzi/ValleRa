# core/processor.py
import skills
from core.ai_brain import AIBrain
from thefuzz import fuzz
import re
import io
import contextlib
import sys
import os
import platform
import math
import random

class CommandProcessor:
    def __init__(self, voice_engine, listener):
        self.voice = voice_engine
        self.listener = listener 
        self.brain = AIBrain()
        
        # Швидкі команди (без інтернету)
        self.hard_commands = {
            ("час", "котра година"): skills.get_time,
            ("дата", "яке число"): skills.get_date,
            ("скрін", "фото екрану"): skills.take_screenshot,
            ("стоп", "скасуй", "відміна"): skills.cancel_shutdown,
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
        }

    def _execute_tag(self, tag):
        print(f"⚡ ВИКОНАННЯ ТЕГУ: [{tag}]")
        
        # Словник дій для AI
        commands = {
            "browser": lambda: skills.open_program("browser"),
            "weather": lambda: skills.check_weather(""), 
            "shutdown": skills.turn_off_pc,
            "vision": lambda: "VISION_TRIGGER",
            "youtube": lambda: skills.open_program("youtube"),
            "telegram": lambda: skills.open_program("telegram"),
            "steam": lambda: skills.open_program("steam"),
        }

        if tag in commands:
            return commands[tag]()
        
        path = skills.open_program(tag)
        if "Не знайшов" not in path:
            return path
            
        return None

    def _execute_python(self, code):
        """Виконує Python код, який згенерував AI"""
        print(f"🐍 [PYTHON] Виконую:\n{code}")
        
        # Створюємо буфер для перехоплення print()
        str_io = io.StringIO()
        
        try:
            # Перенаправляємо stdout (консоль) у наш буфер
            with contextlib.redirect_stdout(str_io):
                # ІЗОЛЬОВАНЕ середовище - НЕ даємо доступ до globals()
                safe_builtins = {
                    'print': print,
                    'len': len,
                    'str': str,
                    'int': int,
                    'float': float,
                    'list': list,
                    'dict': dict,
                    'range': range,
                    'sum': sum,
                    'min': min,
                    'max': max,
                    'abs': abs,
                    'round': round,
                    'sorted': sorted,
                    'reversed': reversed,
                    'enumerate': enumerate,
                    'zip': zip,
                    'map': map,
                    'filter': filter,
                }
                safe_scope = {
                    "os": {"getcwd": os.getcwd, "listdir": os.listdir},
                    "sys": {"argv": sys.argv},
                    "platform": {"system": platform.system, "machine": platform.machine},
                    "math": {"pi": math.pi, "sqrt": math.sqrt, "pow": math.pow, "sin": math.sin, "cos": math.cos},
                    "random": {"random": random.random, "randint": random.randint, "choice": random.choice},
                }
                exec(code, safe_builtins, safe_scope)
            
            output = str_io.getvalue()
            if not output: output = "Код виконано (без виводу)."
            return output.strip()
            
        except Exception as e:
            return f"Помилка виконання коду: {e}"

    def process(self, text):
        if not text: return
        print(f"👤 Юзер: {text}")
        
        clean_text = text.lower().replace("валєра", "").replace("валера", "").replace("бот", "").strip()

        # 1. Жорсткі команди (Пріоритет)
        for triggers, func in self.hard_commands.items():
            for t in triggers:
                if fuzz.ratio(t, clean_text) > 85:
                    print("⚙️ Hard Command")
                    res = func(clean_text)
                    if res: self.voice.say(res)
                    return

        if skills.is_app_name(clean_text):
            print(f"🚀 Це програма: {clean_text}")
            response = skills.open_program(clean_text)
            if response:
                self.voice.say(response)
            return

        # 3. AI (Gemma 3)
        print("🧠 AI думає...")
        
        # Якщо юзер просить інформацію (Пошук)
        search_triggers = ["розкажи про", "хто такий", "що таке", "знайди інфу", "який курс", "погода"]
        web_context = ""
        
        if any(tr in clean_text for tr in search_triggers):
            print("🕵️ Пошук даних в реальному часі...")
            web_data = skills.search_internet(clean_text)
            if web_data:
                web_context = f"\n[ЗНАЙДЕНО В ІНТЕРНЕТІ]: {web_data}"
        
        # Додаємо контекст вікна
        window_context = ""
        try:
            import subprocess
            result = subprocess.run(['xdotool', 'getactivewindow', 'getwindowname'], 
                                   capture_output=True, text=True, timeout=1)
            if result.returncode == 0 and result.stdout.strip():
                window_name = result.stdout.strip()[:50]
                if window_name and window_name != "N/A":
                    window_context = f"\n[АКТИВНЕ ВІКНО]: {window_name}"
        except:
            pass
        
        # Додаємо статус системи
        status_context = ""
        try:
            cpu = __import__('psutil').cpu_percent()
            memory = __import__('psutil').virtual_memory()
            status_context = f"\n[СТАН СИСТЕМИ]: CPU {cpu}% | RAM {memory.percent}%"
        except:
            pass
        
        # Додаємо це до існуючого контексту
        full_context = skills.get_custom_knowledge(clean_text) + web_context + window_context + status_context
        
        ai_reply = self.brain.think(clean_text, context_data=full_context)
        
        # === ОБРОБКА CMD (Запуск програм) ===
        match_cmd = re.search(r"\[CMD:\s*(\w+)\]", ai_reply)
        if match_cmd:
            tag = match_cmd.group(1)
            
            if tag == "vision":
                # Логіка зору
                path = skills.look_at_screen()
                if path:
                    vision_response = self.brain.see(path, text)
                    self.voice.say(vision_response)
                    os.remove(path)
                return

            result_voice = self._execute_tag(tag)
            
            # Якщо команда щось повернула (напр. статус) - озвучуємо
            if result_voice and result_voice != "VISION_TRIGGER":
                self.voice.say(result_voice)
            return

        # === ОБРОБКА PYTHON (Виконання коду) ===
        match_py = re.search(r"\[PYTHON:\s*(.*?)\]", ai_reply, re.DOTALL)
        if match_py:
            code = match_py.group(1)
            self.voice.say("Пишу код...")
            
            # 1. Виконуємо код
            result = self._execute_python(code)
            print(f"📤 Результат коду: {result}")
            
            # 2. Просимо AI прокоментувати результат
            final_answer = self.brain.think(f"SYSTEM: Код виконано. Результат:\n{result}\nКоротко озвуч це користувачу.")
            self.voice.say(final_answer)
            return

        # Якщо тегів немає — просто кажемо текст
        self.voice.say(ai_reply)