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
            ("пауза", "продовжити", "музика", "стоп"): skills.media_play_pause,
            ("наступний", "наступна", "далі", "перемкни"): skills.media_next,
            ("попередній", "назад", "верни"): skills.media_prev,
            ("натисни", "клік"): skills.click_play,
            ("прочитай", "що в буфері", "озвуч"): skills.read_clipboard,
            ("статус", "система", "навантаження", "як ти"): skills.system_status,
            ("закрий", "вбий"): skills.close_app,
            ("блокування", "заблокуй", "лок"): skills.lock_screen,
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

        if skills.is_app_name(tag): 
            return skills.open_program(tag)
            
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

        if skills.is_app_name(clean_text):
            print(f"🚀 Це програма: {clean_text}")

            response = skills.open_program(clean_text)
            
            if response:
                self.voice.say(response)
                
            return

        # 3. AI (Gemma 3)
        print("🧠 Gemma думає...")
        
# Якщо юзер просить інформацію
        search_triggers = ["розкажи про", "хто такий", "що таке", "знайди інфу", "який курс", "погода"]
        web_context = ""
        
        if any(tr in clean_text for tr in search_triggers):
            print("🕵️ Пошук даних в реальному часі...")
            web_data = skills.search_internet(clean_text)
            if web_data:
                web_context = f"\n[ЗНАЙДЕНО В ІНТЕРНЕТІ]: {web_data}"
        
        # Додаємо це до існуючого контексту
        full_context = skills.get_custom_knowledge(clean_text) + web_context
        
        ai_reply = self.brain.think(clean_text, context_data=full_context)
        
        # Парсинг тегів
        match = re.search(r"\[CMD:\s*(\w+)\]", ai_reply)
        
        if match:
            tag = match.group(1)
            result_voice = self._execute_tag(tag, clean_text)
            if result_voice:
                self.voice.say(result_voice)
        else:
            # Звичайна розмова
            self.voice.say(ai_reply)