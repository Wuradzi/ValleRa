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
            ("стоп", "скасуй"): skills.cancel_shutdown,
            ("гучніше",): skills.volume_up,
            ("тихіше",): skills.volume_down,
            ("пауза", "музика", "стоп"): skills.media_play_pause,
            ("наступний", "далі"): skills.media_next,
            ("попередній", "назад"): skills.media_prev,
            ("статус", "система", "навантаження"): skills.system_status,
            ("закрий", "вбий"): skills.close_app,
            ("блокування", "заблокуй", "лок"): skills.lock_screen,
            ("запам'ятай", "запиши"): skills.remember_data,
            ("нагадай", "що ти знаєш"): skills.recall_data,
            ("таймер", "через скільки"): skills.timer,
            ("порахуй", "скільки буде"): skills.calculator,
            ("процеси", "запущені програми"): skills.list_processes,
            ("запиши нотатку", "додай нотатку"): skills.add_note,
            ("покажи нотатки", "нотатки"): skills.show_notes,
            ("очисти нотатки",): skills.clear_notes,
            ("переклади", "переклад"): skills.translate_text,
            ("погода", "прогноз"): skills.check_weather,
        }

    def _execute_tag(self, tag):
        print(f"⚡ ВИКОНАННЯ ТЕГУ: [{tag}]")
        
        commands = {
            "browser": lambda: skills.open_program("browser"),
            "shutdown": skills.turn_off_pc,
            "vision": lambda: "VISION_TRIGGER",
        }

        if tag in commands:
            return commands[tag]()
        
        path = skills.open_program(tag)
        if "Не знайшов" not in path:
            return path
            
        return None

    def _execute_python(self, code):
        """Виконує Python код, який згенерував AI (Режим Бога)"""
        print(f"🐍 [PYTHON] Виконую:\n{code}")
        
        str_io = io.StringIO()
        
        try:
            with contextlib.redirect_stdout(str_io):
                # Повний доступ до модулів та навичок
                local_scope = {
                    "os": os, "sys": sys, "platform": platform,
                    "math": math, "random": random, "skills": skills
                }
                exec(code, globals(), local_scope)
            
            output = str_io.getvalue()
            if not output: output = "Код виконано (без виводу)."
            return output.strip()
            
        except Exception as e:
            return f"Помилка виконання коду: {e}"

    def process(self, text):
        if not text: return
        print(f"👤 Юзер: {text}")
        
        clean_text = text.lower().replace("валєра", "").replace("валера", "").replace("бот", "").strip()

        # 1. Жорсткі команди
        for triggers, func in self.hard_commands.items():
            for t in triggers:
                if fuzz.ratio(t, clean_text) > 85:
                    print("⚙️ Hard Command")
                    # Передаємо voice, щоб таймер міг дзвонити голосом
                    res = func(clean_text, voice=self.voice) 
                    if res: self.voice.say(res)
                    return

        # 2. Запуск програм напряму
        if skills.is_app_name(clean_text):
            print(f"🚀 Це програма: {clean_text}")
            response = skills.open_program(clean_text)
            if response: self.voice.say(response)
            return

        # 3. AI (Gemma)
        print("🧠 AI думає...")
        
        # Перевірка чи треба шукати в інтернеті
        search_triggers = ["розкажи про", "хто такий", "що таке", "знайди інфу", "який курс"]
        web_context = ""
        
        if any(tr in clean_text for tr in search_triggers):
            print("🕵️ Пошук даних в реальному часі...")
            web_data = skills.search_internet(clean_text)
            if web_data:
                web_context = f"\n[ЗНАЙДЕНО В ІНТЕРНЕТІ]: {web_data}"
        
        full_context = web_context
        
        ai_reply = self.brain.think(clean_text, context_data=full_context)
        
        # Парсинг CMD
        match_cmd = re.search(r"\[CMD:\s*(\w+)\]", ai_reply)
        if match_cmd:
            tag = match_cmd.group(1)
            if tag == "vision":
                path = skills.look_at_screen()
                if path:
                    vision_response = self.brain.see(path, text)
                    self.voice.say(vision_response)
                    os.remove(path)
                return

            result_voice = self._execute_tag(tag)
            if result_voice and result_voice != "VISION_TRIGGER":
                self.voice.say(result_voice)
            return

        # Парсинг PYTHON
        match_py = re.search(r"\[PYTHON:\s*(.*?)\]", ai_reply, re.DOTALL)
        if match_py:
            code = match_py.group(1)
            self.voice.say("Пишу код...")
            
            result = self._execute_python(code)
            print(f"📤 Результат коду: {result}")
            
            final_answer = self.brain.think(f"SYSTEM: Код виконано. Результат:\n{result}\nКоротко озвуч це користувачу.")
            self.voice.say(final_answer)
            return

        # Якщо просто текст
        self.voice.say(ai_reply)