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
            ("запиши нотатку", "запиши нотатки", "додай нотатку", "додай нотатки"): skills.add_note,
            ("покажи нотатки", "нотатки"): skills.show_notes,
            ("очисти нотатки",): skills.clear_notes,
            ("погода", "прогноз"): skills.check_weather,
        }

    def _execute_tag(self, tag):
        commands = {"browser": lambda: skills.open_program("browser"), "shutdown": skills.turn_off_pc, "vision": lambda: "VISION_TRIGGER"}
        if tag in commands: return commands[tag]()
        path = skills.open_program(tag)
        return path if "Не знайшов" not in path else None

    def _execute_python(self, code):
        print(f"🐍 [PYTHON] Виконую:\n{code}")
        str_io = io.StringIO()
        try:
            with contextlib.redirect_stdout(str_io):
                local_scope = {"os": os, "sys": sys, "platform": platform, "math": math, "random": random, "skills": skills}
                exec(code, globals(), local_scope)
            output = str_io.getvalue()
            return output.strip() if output else "Код виконано (без виводу)."
        except Exception as e: return f"Помилка: {e}"

    def process(self, text):
        if not text: return
        clean_text = text.lower().replace("валєра", "").replace("валера", "").replace("бот", "").strip()

        for triggers, func in self.hard_commands.items():
            for t in triggers:
                if fuzz.ratio(t, clean_text) > 85:
                    res = func(clean_text, voice=self.voice, listener=self.listener)
                    if res: self.voice.say(res)
                    return

        if skills.is_app_name(clean_text):
            response = skills.open_program(clean_text)
            if response: self.voice.say(response)
            return

        web_context = ""
        if any(tr in clean_text for tr in ["розкажи про", "хто такий", "знайди інфу", "який курс"]):
            web_data = skills.search_internet(clean_text)
            if web_data: web_context = f"\n[ІНТЕРНЕТ]: {web_data}"
        
        ai_reply = self.brain.think(clean_text, context_data=web_context)
        
        match_cmd = re.search(r"\[CMD:\s*(\w+)\]", ai_reply)
        if match_cmd:
            tag = match_cmd.group(1)
            if tag == "vision":
                path = skills.look_at_screen()
                if path:
                    self.voice.say(self.brain.see(path, text))
                    os.remove(path)
                return
            res = self._execute_tag(tag)
            if res and res != "VISION_TRIGGER": self.voice.say(res)
            return

        match_py = re.search(r"\[PYTHON:\s*(.*?)\]", ai_reply, re.DOTALL)
        if match_py:
            self.voice.say("Обчислюю...")
            result = self._execute_python(match_py.group(1))
            self.voice.say(self.brain.think(f"SYSTEM: Результат коду:\n{result}\nОзвуч це."))
            return

        self.voice.say(ai_reply)