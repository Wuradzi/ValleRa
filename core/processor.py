# core/processor.py
import skills
from core.ai_brain import AIBrain
from thefuzz import fuzz
import re

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
        }

    def _execute_tag(self, tag, text):
        """Виконує тег і повертає статус для озвучки"""
        print(f"⚡ ВИКОНАННЯ ТЕГУ: [{tag}]")
        
        if tag == "browser": return skills.search_google(text)
        if tag == "steam": return skills.open_program("steam")
        if tag == "telegram": return skills.open_program("telegram")
        if tag == "weather": return skills.check_weather(text)
        if tag == "time": return skills.get_time()
        if tag == "youtube": return skills.search_youtube_clip(text)
        if tag == "shutdown": return skills.turn_off_pc()
        
        if tag == "vision":
            path = skills.look_at_screen()
            if not path: return "Помилка скріншоту."
            self.voice.say("Дивлюсь...")
            return self.brain.see(path, text)

        return None

    def process(self, text):
        if not text: return
        print(f"👤 Юзер: {text}")
        
        clean_text = text.lower().replace("валєра", "").replace("валера", "").strip()

        # 1. Жорсткі команди (Пріоритет)
        for triggers, func in self.hard_commands.items():
            for t in triggers:
                if fuzz.ratio(t, clean_text) > 85:
                    print("⚙️ Hard Command")
                    res = func(clean_text)
                    if res: self.voice.say(res)
                    return

        # 2. Програми
        if skills.is_app_name(clean_text):
            self.voice.say(f"Запускаю {clean_text}")
            skills.open_program(clean_text)
            return

        # 3. AI (Gemma 3)
        print("🧠 Gemma думає...")
        
        context = skills.get_custom_knowledge(clean_text)
        ai_reply = self.brain.think(clean_text, context_data=context)
        
        # Парсинг тегів
        match = re.search(r"\[CMD:\s*(\w+)\]", ai_reply)
        
        if match:
            tag = match.group(1)
            # ІГНОРУЄМО текст від AI, виконуємо команду
            result_voice = self._execute_tag(tag, clean_text)
            if result_voice:
                self.voice.say(result_voice)
        else:
            # Звичайна розмова
            self.voice.say(ai_reply)