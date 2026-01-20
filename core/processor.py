import skills
from core.ai_brain import AIBrain
from thefuzz import fuzz
import re

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
        Шукає найбільш схожу команду (Fuzzy matching).
        """
        best_ratio = 0
        best_func = None
        THRESHOLD = 80 

        for triggers, func in self.commands.items():
            for trigger in triggers:
                ratio = fuzz.partial_ratio(trigger, user_text)
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_func = func
        
        if best_ratio >= THRESHOLD:
            return best_func
        return None

    def _execute_ai_command(self, tag, user_text):
        """
        Виконує команду на основі тегу.
        Тепер ми озвучуємо результат виконання (return) функції,
        замість балаканини Gemma.
        """
        print(f"🔧 AI виконує команду: {tag}")
        try:
            response = None

            if tag == "browser":
                # search_google повертає рядок "Шукаю..."
                response = skills.search_google(user_text)
            
            elif tag == "steam":
                # open_program повертає "Запускаю steam..."
                response = skills.open_program("steam")
            
            elif tag == "telegram":
                response = skills.open_program("telegram")
            
            elif tag == "weather":
                # Тут повертається повний прогноз
                response = skills.check_weather(user_text)
            
            elif tag == "time":
                response = skills.get_time()
            
            elif tag == "shutdown":
                response = skills.turn_off_pc()

            elif tag == "youtube":
                response = skills.search_youtube_clip(user_text)
            
            elif tag == "vision":
                image_path = skills.look_at_screen()
                if image_path:
                    self.voice.say("Секунду, дивлюсь...")
                    # Vision повертає опис, тому тут ми його кажемо
                    vision_response = self.brain.see(image_path, user_text)
                    self.voice.say(vision_response)
                    return # Виходимо, бо ми вже сказали все, що треба
                else:
                    response = "Не можу зробити скріншот."

            else:
                print(f"⚠️ Невідомий AI тег: {tag}")
            
            # Якщо функція повернула текстову відповідь (статус) — озвучуємо її
            if response:
                self.voice.say(response)

        except Exception as e:
            print(f"❌ Помилка виконання AI команди: {e}")

    def process(self, text):
        if not text:
            return

        print(f"💬 Отримав: {text}")

        clean_text = text.lower().replace("валера", "").replace("валєра", "").strip()
        
        # 1. Швидкий пошук "Розкажи про..."
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

        # 2. Fuzzy Match (Старі добрі жорсткі команди)
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

        # 3. Запуск програм за назвою
        if skills.is_app_name(clean_text):
            print(f"🚀 Це програма! Запускаю: {clean_text}")
            self.voice.say(f"Запускаю {clean_text}.")
            skills.open_program(clean_text) 
            return

        # 4. NEURO-STYLE: АНАЛІЗ ЧЕРЕЗ GEMMA
        print("🧠 Аналізую наміри через Gemma...")
        
        # Перевірка бази знань (RAG)
        custom_info = skills.get_custom_knowledge(clean_text)
        if custom_info:
            print(f"📚 Знайшов додаткову інфу в базі!")

        try:
            # Думаємо...
            if custom_info:
                ai_response = self.brain.think(clean_text, context_data=custom_info)
            else:
                ai_response = self.brain.think(clean_text)
            
            # Шукаємо тег [CMD: ...]
            match = re.search(r"\[CMD:\s*(\w+)\]", ai_response)
            
            if match:
                command_tag = match.group(1) # "steam", "weather", "vision"
                
                # 🔥 ГОЛОВНА ЗМІНА:
                # Якщо ми знайшли команду — ми ІГНОРУЄМО все, що там набазікала Gemma.
                # Ми не кажемо spoken_text. Ми просто виконуємо дію.
                
                # Виконуємо дію (і вона сама озвучить свій статус, якщо треба)
                self._execute_ai_command(command_tag, clean_text)
                
            else:
                # Тегу немає — значить це просто розмова, кажемо все як є
                if ai_response:
                    self.voice.say(ai_response)
                else:
                    self.voice.say("Хм, не знаю що відповісти.")

        except Exception as e:
            print(f"Brain Error: {e}")
            self.voice.say("Щось у мене думки плутаються.")