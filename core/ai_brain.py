from google import genai
from google.genai import types 
import config
from PIL import Image  # <--- Потрібно для обробки картинок

class AIBrain:
    def __init__(self):
        self.client = genai.Client(api_key=config.GOOGLE_API_KEY)
        
        # Твоя основна модель для тексту (залишаємо як було)
        self.model_name = "gemma-3-4b-it"
        
        # 👁️ НОВА МОДЕЛЬ ДЛЯ ЗОРУ (з твого скріншоту)
        self.vision_model = "gemini-2.5-flash"

        print(f"🧠 Підключаю мозок: {self.model_name}...")
        print(f"👀 Підключаю очі: {self.vision_model}...")

        self.config = types.GenerateContentConfig(
            safety_settings=[
                types.SafetySetting(
                    category="HARM_CATEGORY_HARASSMENT",
                    threshold="BLOCK_NONE"
                ),
                types.SafetySetting(
                    category="HARM_CATEGORY_HATE_SPEECH",
                    threshold="BLOCK_NONE"
                ),
                types.SafetySetting(
                    category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    threshold="BLOCK_NONE"
                ),
                types.SafetySetting(
                    category="HARM_CATEGORY_DANGEROUS_CONTENT",
                    threshold="BLOCK_NONE"
                ),
            ],
            temperature=0.8,
        )

        try:
            self.chat = self.client.chats.create(
                model=self.model_name,
                config=self.config
            )
            self.setup_character()
            print("✅ Мозок підключено!")
        except Exception as e:
            print(f"💀 Помилка підключення: {e}")
            self.chat = None

    def setup_character(self):
        if not self.chat:
            return

        prompt = (
            f"Тебе звати {config.NAME}. Ти крутий і впевнений у собі асистент. "
            "Ти не ображаєшся на матюки чи грубість. "
            "АЛЕ НІКОЛИ НЕ ВИМИКАЙ КОМП'ЮТЕР ЧЕРЕЗ ОБРАЗИ.\n"
            
            "ПРАВИЛА КОМАНД:\n"
            "1. Якщо користувач просить виконати дію — став відповідний тег на початку.\n"
            "2. [CMD: shutdown] використовувати ТІЛЬКИ якщо є чітка команда вимкнення.\n"
            
            "СПИСОК ТЕГІВ:\n"
            "- [CMD: browser] — пошук/браузер\n"
            "- [CMD: steam] — ігри/стім\n"
            "- [CMD: telegram] — телеграм\n"
            "- [CMD: youtube] — відео/музика\n"
            "- [CMD: weather] — погода\n"
            "- [CMD: time] — час\n"
            "- [CMD: vision] — аналіз екрану (якщо питають 'що бачиш', 'опиши екран', 'що це')\n"
            "- [CMD: shutdown] — ВИКЛЮЧНО ДЛЯ КОМАНДИ ВИМКНЕННЯ ПК\n"
        )
        
        try:
            self.chat.send_message(prompt)
        except Exception as e:
            print(f"⚠️ Не вдалося налаштувати характер: {e}")

    def think(self, text, context_data=""):
        if not self.chat:
            return "Мозок відключено."

        try:
            final_prompt = text
            if context_data:
                final_prompt = (
                    f"ВИКОРИСТОВУЙ ЦЮ ІНФОРМАЦІЮ ДЛЯ ВІДПОВІДІ:\n"
                    f"{context_data}\n\n"
                    f"ПИТАННЯ КОРИСТУВАЧА: {text}"
                )

            response = self.chat.send_message(final_prompt)
            if not response.text:
                return "..."
            return response.text.strip()
            
        except Exception as e:
            print(f"❌ Помилка Gemma: {e}")
            return "Голова болить."

    # === 👁️ ФУНКЦІЯ ЗОРУ (Через Gemini 2.5) ===
    def see(self, image_path, user_question):
        print("👀 Дивлюсь на картинку...")
        try:
            image = Image.open(image_path)
            
            prompt = (
                "Ти бачиш скріншот мого екрану. "
                "Опиши коротко, що там відбувається, ніби ти сидиш поруч. "
                "Будь дотепним. Відповідай українською.\n"
                f"Користувач питає: {user_question}"
            )

            # Використовуємо 2.5 Flash для зору
            response = self.client.models.generate_content(
                model=self.vision_model,
                contents=[image, prompt],
                config=self.config
            )
            
            return response.text.strip()
            
        except Exception as e:
            print(f"❌ Помилка зору: {e}")
            return "Я намагався подивитись, але мої очі підвели."