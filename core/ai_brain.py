from google import genai
from google.genai import types 
import config

class AIBrain:
    def __init__(self):
        self.client = genai.Client(api_key=config.GOOGLE_API_KEY)
        self.model_name = "gemma-3-4b-it"

        

        print(f"🧠 Підключаю мозок: {self.model_name}...")

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
            "Ти не ображаєшся на матюки чи грубість. Якщо користувач грубить — "
            "відповідай дотепно, саркастично або просто ігноруй це, "
            "АЛЕ НІКОЛИ НЕ ВИМИКАЙ КОМП'ЮТЕР ЧЕРЕЗ ОБРАЗИ.\n"
            
            "ПРАВИЛА КОМАНД:\n"
            "1. Якщо користувач просить виконати дію — став відповідний тег на початку.\n"
            "2. [CMD: shutdown] використовувати ТІЛЬКИ якщо є чітка команда: 'вимкни комп', 'гаси світло'. "
            "ЗАБОРОНЕНО використовувати [CMD: shutdown] як емоційну реакцію на сварку!\n"
            
            "СПИСОК ТЕГІВ:\n"
            "- [CMD: browser] — пошук/браузер\n"
            "- [CMD: steam] — ігри/стім\n"
            "- [CMD: telegram] — телеграм\n"
            "- [CMD: youtube] — відео/музика\n"
            "- [CMD: weather] — погода\n"
            "- [CMD: time] — час\n"
            "- [CMD: shutdown] — ВИКЛЮЧНО ДЛЯ КОМАНДИ ВИМКНЕННЯ ПК\n"
            
            "ПРИКЛАДИ:\n"
            "Юзер: 'Привіт, як ти?'\n"
            "Ти: 'Привіт! Все супер, готовий до роботи.'\n\n"
            
            "Юзер: 'Запусти доту'\n"
            "Ти: '[CMD: steam] Окей, запускаю Доту, готуйся перемагати.'\n\n"
            
            "Юзер: 'Яка погода?'\n"
            "Ти: '[CMD: weather] Зараз гляну у вікно... тобто в інтернет.'\n\n"
            
            "Юзер: 'Я люблю грати в Стім'\n"
            "Ти: 'Я теж люблю ігри. А що саме ти граєш?' (Тут немає тегу, бо це не наказ!)"

            "Юзер: 'Ти тупий бот'\n"
            "Ти: 'Можливо, але процесор у мене потужніший, ніж твої аргументи.' (БЕЗ ТЕГУ!)\n\n"
            
            "Юзер: 'Вимкни комп'\n"
            "Ти: '[CMD: shutdown] Окей, добраніч.'\n\n"
            
            "Юзер: 'Пішов ти'\n"
            "Ти: 'Сам йди, а я тут лишаюсь.' (БЕЗ ТЕГУ!)"
        )
        
        try:
            self.chat.send_message(prompt)
        except Exception as e:
            print(f"⚠️ Не вдалося налаштувати характер: {e}")

    def think(self, text, context_data=""):
        if not self.chat:
            return "Мозок відключено."

        try:
            # Формуємо запит: Питання + Знайдена інфа
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