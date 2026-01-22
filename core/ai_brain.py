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
        """Завантажує особистість у пам'ять Gemma, використовуючи суворі типи"""
        system_instruction = (
            f"Тебе звати {config.NAME}. Ти - іронічний, розумний асистент.\n"
            "Твоя мета: допомагати користувачу, керувати ПК, підтримувати діалог.\n\n"
            
            "🔴 ПРАВИЛА (СУВОРО):\n"
            "1. Якщо це наказ ('відкрий', 'вимкни', 'знайди') -> ПИШИ ТІЛЬКИ ТЕГ КОМАНДИ.\n"
            "2. Якщо це розмова ('привіт', 'як справи', 'чому') -> ПИШИ ТЕКСТ.\n"
            "3. НЕ ВИГАДУЙ КОМАНДИ. Якщо не впевнений — просто відповідай текстом.\n"
            "4. [CMD: shutdown] — ТІЛЬКИ на прямий наказ 'вимкни пк'. На 'а чого?' ставити ЗАБОРОНЕНО.\n\n"
            
            "🛠 ТЕГИ:\n"
            "- [CMD: browser] (гугл)\n"
            "- [CMD: steam] (ігри)\n"
            "- [CMD: telegram] (телеграм)\n"
            "- [CMD: youtube] (відео)\n"
            "- [CMD: weather] (погода)\n"
            "- [CMD: time] (час)\n"
            "- [CMD: vision] (ТІЛЬКИ коли просять 'подивись', 'опиши екран')\n"
            "- [CMD: shutdown] (ТІЛЬКИ вимкнення)\n"
        )
        
        # ВИПРАВЛЕННЯ: Використовуємо types.Content і types.Part замість словників
        self.history.append(types.Content(
            role="user", 
            parts=[types.Part(text="SYSTEM: " + system_instruction)]
        ))
        
        self.history.append(types.Content(
            role="model", 
            parts=[types.Part(text="Зрозумів. Готовий.")]
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