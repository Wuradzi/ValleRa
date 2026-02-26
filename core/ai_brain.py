from google import genai
from google.genai import types
import config
from collections import deque
import platform 
from PIL import Image

class AIBrain:
    def __init__(self):
        if not config.GOOGLE_API_KEY:
            print("⚠️ Google API Key не знайдено.")
            self.client = None
            return
            
        self.client = genai.Client(api_key=config.GOOGLE_API_KEY)
        self.main_model = config.MAIN_MODEL
        self.vision_model = config.VISION_MODEL
        self.history = deque(maxlen=config.HISTORY_LIMIT)
        self.os_type = platform.system()
        
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
        os_context = "Linux" if self.os_type == "Linux" else "Windows"
        system_instruction = (
            f"Ти — {config.NAME}, голосовий помічник для {os_context}.\n"
            "🔴 СУПЕР-СИЛА (PYTHON):\n"
            "Ти маєш доступ до Python! Якщо треба порахувати, створити файл, дізнатися IP чи процеси — пиши код у тезі [PYTHON: код].\n"
            "ФОРМАТ ВІДПОВІДІ:\n"
            "1. Запуск програм: '[CMD: firefox]'\n"
            "2. Виконання коду: '[PYTHON: import os; print(os.getcwd())]'\n"
            "3. Перегляд екрану: '[CMD: vision]'\n"
            "4. Звичайна розмова: просто текст.\n"
            "Код має виводити результат через print()."
        )
        self.history.append(types.Content(role="user", parts=[types.Part(text="SYSTEM: " + system_instruction)]))
        self.history.append(types.Content(role="model", parts=[types.Part(text="Прийнято.")]))

    def think(self, text, context_data=""):
        if not self.client: return "Я працюю в офлайн режимі без AI."
        try:
            current_prompt = text
            if context_data: current_prompt += f"\n[Інфо: {context_data}]"
            self.history.append(types.Content(role="user", parts=[types.Part(text=current_prompt)]))
            
            chat = self.client.chats.create(model=self.main_model, config=self.config, history=list(self.history)[:-1])
            response = chat.send_message(current_prompt)
            answer = response.text.strip()
            
            self.history.append(types.Content(role="model", parts=[types.Part(text=answer)]))
            return answer
        except Exception as e: return f"Помилка мозку: {e}"

    def see(self, image_path, user_question):
        try:
            image = Image.open(image_path)
            response = self.client.models.generate_content(
                model=self.vision_model, 
                contents=[image, f"Що на скріншоті: {user_question}"], 
                config=self.config
            )
            return response.text.strip()
        except Exception as e: return "Не можу подивитись."