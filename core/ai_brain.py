from google import genai
from google.genai import types
import config
from PIL import Image
from collections import deque

class AIBrain:
    def __init__(self):
        self.client = genai.Client(api_key=config.GOOGLE_API_KEY)
        
        # Основна модель для розмов (Gemma 3 12B)
        self.main_model = config.MAIN_MODEL
        # Модель для зору (Gemini 2.5 Flash)
        self.vision_model = config.VISION_MODEL
        
        # Історія діалогу (пам'ять)
        self.history = deque(maxlen=config.HISTORY_LIMIT)
        
        print(f"🧠 Cortex: {self.main_model} | 👀 Vision: {self.vision_model}")
        
        self.config = types.GenerateContentConfig(
            safety_settings=[
                types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
                types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
                types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
                types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
            ],
            temperature=0.6, 
        )
        
        self._init_context()

    def _init_context(self):
        """Завантажує особистість."""
        system_instruction = (
            f"SYSTEM OVERRIDE: Ти — {config.NAME}, голосовий інтерфейс керування Windows.\n"
            "Твоя задача — перетворювати запити користувача на системні теги або відповідати на питання.\n\n"
            
            "🔴 ВАЖЛИВО: Ти НЕ маєш фізичного тіла, але ти МАЄШ керувати системою через теги.\n"
            "Ніколи не кажи 'я не можу відкрити програму'. Замість цього просто видай тег.\n\n"
            
            "ПРАВИЛА ОБРОБКИ:\n"
            "1. Запит 'Відкрий Word' -> Твоя реакція: '[CMD: word]'\n"
            "2. Запит 'Запусти Firefox' -> Твоя реакція: '[CMD: firefox]'\n"
            "3. Запит 'Привіт' -> Твоя реакція: 'Привіт! Чим допомогти?'\n\n"
            
            "СПИСОК ТЕГІВ:\n"
            "- [CMD: browser] (пошук)\n"
            "- [CMD: steam] (ігри)\n"
            "- [CMD: telegram] (месенджер)\n"
            "- [CMD: youtube] (відео)\n"
            "- [CMD: weather] (погода)\n"
            "- [CMD: vision] (зір)\n"
            "- [CMD: shutdown] (вимкнення)\n"
            "- [CMD: назва_програми] (для будь-якої іншої програми, наприклад [CMD: word])" 
        )
        
        # (Далі код додавання в history без змін)
        self.history.append(types.Content(
            role="user", 
            parts=[types.Part(text="SYSTEM: " + system_instruction)]
        ))
        self.history.append(types.Content(
            role="model", 
            parts=[types.Part(text="Систему активовано. Очікую команд.")]
        ))

    def think(self, text, context_data=""):
        try:
            # Формуємо текст запиту
            current_prompt = text
            if context_data:
                current_prompt += f"\n[Інфо з файлів: {context_data}]"
            
            # 1. Створюємо об'єкт контенту для юзера
            user_content = types.Content(
                role="user", 
                parts=[types.Part(text=current_prompt)]
            )
            
            # Додаємо в локальну пам'ять
            self.history.append(user_content)
            
            # 2. Створюємо чат.
            # Важливо: ми передаємо в history ВСЕ, КРІМ останнього повідомлення (яке ми додамо через send_message)
            history_list = list(self.history)[:-1]
            
            chat = self.client.chats.create(
                model=self.main_model,
                config=self.config,
                history=history_list
            )
            
            # 3. Відправляємо нове повідомлення
            response = chat.send_message(current_prompt)
            answer = response.text.strip()
            
            # 4. Зберігаємо відповідь моделі у правильному форматі
            model_content = types.Content(
                role="model", 
                parts=[types.Part(text=answer)]
            )
            self.history.append(model_content)
            
            return answer
            
        except Exception as e:
            print(f"❌ Brain Error: {e}")
            return "Еррор. Мозок відпав."

    def see(self, image_path, user_question):
        print(f"👀 Vision ({self.vision_model}) аналізує...")
        try:
            image = Image.open(image_path)
            prompt = f"Користувач питає про цей скріншот: '{user_question}'. Відповідай коротко."
            
            # Для generate_content Pydantic не такий суворий, тут список [image, prompt] працює
            response = self.client.models.generate_content(
                model=self.vision_model,
                contents=[image, prompt],
                config=self.config
            )
            return response.text.strip()
        except Exception as e:
            print(f"❌ Vision Error: {e}")
            return "Не бачу картинку."