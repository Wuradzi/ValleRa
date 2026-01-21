import skills
from core.ai_brain import AIBrain
from thefuzz import fuzz
import re
import config

class CommandProcessor:
    def __init__(self, voice_engine, listener):
        self.voice = voice_engine
        self.listener = listener 
        self.brain = AIBrain()
        
        self.commands = {
            ("час", "котра година", "скільки часу"): skills.get_time,
            ("дата", "яке число", "сьогодні"): skills.get_date,
            ("гугл", "google", "загугли", "пошук"): skills.search_google,
            ("блокнот", "записки"): skills.open_notepad,
            ("калькулятор", "порахуй"): skills.open_calculator,
            ("гучніше", "звук плюс"): skills.volume_up,
            ("тихіше", "звук мінус"): skills.volume_down,
            ("скрін", "знімок екрану", "фото екрану"): skills.take_screenshot,
            ("бувай", "вихід", "пока"): skills.stop_program,
            ("погода", "прогноз"): skills.check_weather,
            ("режим навчання", "вчитися"): skills.mode_study,
            ("ігровий режим", "грати"): skills.mode_gaming,

            ("статус", "як ти", "стан"): skills.system_status,
            ("батарея", "заряд"): skills.get_battery,
            ("відкрий", "запусти"): skills.open_program,
            ("закрий", "вбий"): skills.close_app,     
            ("спати", "блокування"): skills.lock_screen,
            ("вимкни комп'ютер", "гаси світло"): skills.turn_off_pc, 
            ("перезавантаж", "ребут"): skills.restart_pc,
            ("скасуй", "відміна"): skills.cancel_shutdown,

            ("натисни", "клік"): skills.click_play,       
            ("плей", "грай", "стоп"): skills.click_play,
            ("знайди відео", "ютуб"): skills.search_youtube_clip,
            ("включи перше", "включи друге"): skills.click_video_by_number,
            ("налаштуй мікрофон", "калібрування", "тихо"): skills.recalibrate_mic,

            ("запам'ятай", "запиши"): skills.remember_data,
            ("нагадай", "що ти знаєш"): skills.recall_data,
            ("запам'ятай що", "вивчи"): skills.teach_alias,
            
            ("якщо я скажу", ): skills.teach_response,
        }

    def _find_best_match(self, user_text):
        """
        Шукає найбільш схожу команду (точне входження слів).
        Перевіряє, чи слова тригера є окремими словами в тексті.
        """
        words_in_text = set(user_text.lower().split())
        
        for triggers, func in self.commands.items():
            for trigger in triggers:
                trigger_words = set(trigger.lower().split())
                # Перевіряємо, чи всі слова тригера є в тексті як окремі слова
                if trigger_words.issubset(words_in_text):
                    return func
        
        return None

    def _execute_ai_command(self, tag, user_text):
        """
        Виконує команду на основі тегу, який повернула Gemma.
        """
        print(f"🔧 AI виконує команду: {tag}")
        try:
            if tag == "browser":
                skills.search_google(user_text)
            elif tag == "steam":
                skills.open_program("steam")
            elif tag == "telegram":
                skills.open_program("telegram")
            elif tag == "weather":
                res = skills.check_weather(user_text)
                self.voice.say(res)
            elif tag == "time":
                self.voice.say(skills.get_time()) 
            elif tag == "shutdown":
                skills.turn_off_pc()
            elif tag == "youtube":
                skills.search_youtube_clip(user_text)
            elif tag == "vision":
                if config.LOW_RESOURCE_MODE and config.DISABLE_VISION_LOW_MODE:
                    self.voice.say("Візія відключена в низькому режимі для економії ресурсів.")
                else:
                    image_path = skills.look_at_screen()
                    if image_path:
                        self.voice.say("Зараз гляну...")
                        # Передаємо скріншот у мозок (Gemini 2.5)
                        vision_response = self.brain.see(image_path, user_text)
                        self.voice.say(vision_response)
                    else:
                        self.voice.say("Не можу зробити скріншот.")
            else:
                print(f"⚠️ Невідомий AI тег: {tag}")
        except Exception as e:
            print(f"❌ Помилка виконання AI команди: {e}")

    def process(self, text):
        if not text:
            return

        print(f"💬 Отримав: {text}")

        clean_text = text.lower().replace("валера", "").replace("валєра", "").strip()
        
        search_triggers = ["розкажи про", "хто такий", "що таке", "знайди інфу"]
        if any(clean_text.startswith(tr) for tr in search_triggers):
            print("🕵️ Пошук в інтернеті...")
            try:
                res = skills.search_internet(clean_text)
                if res:
                    self.voice.say(self.brain.think(f"Ось інфа: {res}"))
                    return
                else:
                    self.voice.say(self.brain.think(clean_text))
                    return
            except: pass

        command_func = self._find_best_match(clean_text)
        
        if command_func:
            print("⚡ Виконую команду (Fuzzy)...")
            try:
                response = command_func(text, voice=self.voice, listener=self.listener)
            except TypeError:
                response = command_func(text)
            
            if response == "goodbye":
                self.voice.say("Бувай, чувак.")
                exit()
            
            if response:
                self.voice.say(response)
            return

        # 4. ПЕРЕВІРКА НА НАЗВУ ПРОГРАМИ ("Валєра, Телеграм")
        if skills.is_app_name(clean_text):
            print(f"🚀 Це програма! Запускаю: {clean_text}")
            self.voice.say(f"Запускаю {clean_text}.")
            skills.open_program(clean_text) 
            return

        # 5. NEURO-STYLE: АНАЛІЗ НАМІРІВ ЧЕРЕЗ GEMMA
        print("🧠 Аналізую наміри через Gemma...")
        custom_info = skills.get_custom_knowledge(clean_text)
        if custom_info:
            print(f"📚 Знайшов додаткову інфу в базі!")
        try:
            ai_response = self.brain.think(clean_text)
            
            # Шукаємо тег [CMD: ...]
            match = re.search(r"\[CMD:\s*(\w+)\]", ai_response)
            
            if match:
                command_tag = match.group(1) # "steam", "weather" і т.д.
                
                # Прибираємо тег з тексту, щоб він його не читав
                spoken_text = ai_response.replace(match.group(0), "").strip()
                
                # Список команд, які виконуються тихо (без попереднього тексту)
                silent_commands = ["vision", "time", "weather", "shutdown", "steam", "telegram", "youtube"]
                
                # Спочатку кажемо текст (реакцію), якщо не тиха команда
                if spoken_text and command_tag not in silent_commands:
                    self.voice.say(spoken_text)
                
                # Потім виконуємо дію
                self._execute_ai_command(command_tag, clean_text)
                
            else:
                if ai_response:
                    self.voice.say(ai_response)
                else:
                    self.voice.say("Хм, не знаю що відповісти.")

        except Exception as e:
            print(f"Brain Error: {e}")
            self.voice.say("Щось у мене думки плутаються.")