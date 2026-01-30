# core/ai_brain.py
from google import genai
from google.genai import types
import config
from PIL import Image
from collections import deque
import platform 

class AIBrain:
    def __init__(self):
        self.client = genai.Client(api_key=config.GOOGLE_API_KEY)
        
        self.main_model = config.MAIN_MODEL
        self.vision_model = config.VISION_MODEL
        
        self.history = deque(maxlen=config.HISTORY_LIMIT)
        
        # Визначаємо систему (Windows/Linux)
        self.os_type = platform.system()
        print(f"🧠 Cortex: {self.main_model} | 🖥️ OS: {self.os_type}")
        
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
        # Адаптуємо промпт під ОС
        os_context = "Linux Mint" if self.os_type == "Linux" else "Windows"
        
        system_instruction = (
            f"SYSTEM OVERRIDE: Ти — {config.NAME}, голосовий інтерфейс керування {os_context}.\n"
            "Твоя задача — перетворювати запити користувача на системні теги або відповідати на питання.\n\n"
            
            "🔴 ВАЖЛИВО: Ти керуєш комп'ютером. Не кажи 'я не можу', а видавай тег [CMD].\n"
            
            "ПРАВИЛА:\n"
            "1. Запит 'Відкрий Firefox' -> Твоя реакція: '[CMD: firefox]'\n"
            "2. Запит 'Що на екрані?' -> Твоя реакція: '[CMD: vision]'\n"
            "3. Запит 'Вимкни комп' -> Твоя реакція: '[CMD: shutdown]'\n\n"
            
            "СПИСОК ТЕГІВ:\n"
            "- [CMD: browser] (пошук)\n"
            "- [CMD: steam] (ігри)\n"
            "- [CMD: telegram] (месенджер)\n"
            "- [CMD: youtube] (відео)\n"
            "- [CMD: weather] (погода)\n"
            "- [CMD: vision] (ТІЛЬКИ коли просять глянути на екран)\n"
            "- [CMD: shutdown] (вимкнення)\n"
            "- [CMD: назва_програми] (запуск програм)" 
        )
        
        self.history.append(types.Content(
            role="user", 
            parts=[types.Part(text="SYSTEM: " + system_instruction)]
        ))
        self.history.append(types.Content(
            role="model", 
            parts=[types.Part(text=f"Систему {os_context} активовано. Готовий.")]
        ))

    def think(self, text, context_data=""):
        try:
            current_prompt = text
            if context_data:
                current_prompt += f"\n[Інфо з файлів: {context_data}]"
            
            user_content = types.Content(role="user", parts=[types.Part(text=current_prompt)])
            self.history.append(user_content)
            
            history_list = list(self.history)[:-1]
            
            chat = self.client.chats.create(
                model=self.main_model,
                config=self.config,
                history=history_list
            )
            
            response = chat.send_message(current_prompt)
            answer = response.text.strip()
            
            model_content = types.Content(role="model", parts=[types.Part(text=answer)])
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
            
            response = self.client.models.generate_content(
                model=self.vision_model,
                contents=[image, prompt],
                config=self.config
            )
            return response.text.strip()
        except Exception as e:
            print(f"❌ Vision Error: {e}")
            return "Не бачу картинку."