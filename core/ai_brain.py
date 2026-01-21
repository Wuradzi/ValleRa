from google import genai
from google.genai import types 
import config
from PIL import Image  # <--- Потрібно для обробки картинок
import requests

# Lazy import для економії пам'яті
if not config.LOW_RESOURCE_MODE:
    try:
        import ollama
    except ImportError:
        ollama = None
else:
    try:
        import ollama
    except ImportError:
        ollama = None

class AIBrain:
    def __init__(self):
        self.use_local = False
        self.client = None
        self.chat = None
        self.local_model = config.LOCAL_MODEL_LIGHT if config.LOW_RESOURCE_MODE else "llama3.2"
        
        # СПОЧАТКУ пробуємо Google AI
        print("🧠 Спробую запустити Google AI...")
        if self._try_google_ai():
            self.use_local = False
            return
        
        # Якщо Google не працює, пробуємо локальну модель Ollama
        print("🔄 Google AI недоступний, пробую локальну модель Ollama...")
        if self._try_local_model():
            self.use_local = True
            return
        
        # Якщо нічого не працює
        print("❌ Ні Google AI, ні локальна модель не доступні!")
        print("💡 Перевірте ключ Google API")
        print("💡 Або запустіть Ollama: ollama serve")
    
    def _try_local_model(self):
        """Перевіряємо локальну модель Ollama"""
        if ollama is None:
            return False
        
        try:
            response = requests.get("http://localhost:11434/api/tags", timeout=5)
            if response.status_code == 200:
                available_models = [m['name'] for m in response.json()['models']]
                if self.local_model in available_models:
                    print(f"✅ Локальна модель {self.local_model} доступна!")
                    return True
                else:
                    print(f"⚠️ Модель {self.local_model} не встановлена.")
                    print(f"💡 Встановіть: ollama pull {self.local_model}")
                    return False
            else:
                print("❌ Ollama не запущений. Запустіть: ollama serve")
                return False
        except Exception as e:
            print(f"❌ Ollama недоступний: {e}")
            return False
    
    def _choose_sight_model(self):
        """Обираємо найкращу модель для зору залежно від доступності"""
        sight_models = [
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-3-flash"
        ]
        
        for model in sight_models:
            try:
                # Пробуємо створити простий chat, щоб перевірити доступність
                test_chat = self.client.chats.create(model=model, config=self.config)
                print(f"✅ Модель для зору: {model}")
                return model
            except Exception as e:
                if "404" in str(e):
                    continue
                return sight_models[0]
        
        return sight_models[0]
    
    def _try_google_ai(self):
        """Перевіряємо Google AI"""
        try:
            self.client = genai.Client(api_key=config.GOOGLE_API_KEY)
            self.model_name = "gemma-3-4b-it"  
            
            print(f"🧠 Підключаю: {self.model_name}...")
            print(f"👁️ Зір: вибираю найкращу модель...")
            
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
            
            self.chat = self.client.chats.create(
                model=self.model_name,
                config=self.config
            )
            # Обираємо найкращу модель для зору
            self.sight_model = self._choose_sight_model()
            self.setup_character()
            print("✅ Мозок підключено (Google AI)!")
            return True
            
        except Exception as e:
            print(f"⚠️ Не вдалося підключитися до Google AI: {e}")
            return False

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
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                print("🔄 Квота Google AI вичерпана, переключаюся на Ollama...")
                self.use_local = True
                self.local_model = config.LOCAL_MODEL_LIGHT if config.LOW_RESOURCE_MODE else "llama3.2"
                # Перевіряємо Ollama
                try:
                    response = requests.get("http://localhost:11434/api/tags", timeout=5)
                    if response.status_code == 200:
                        available_models = [m['name'] for m in response.json()['models']]
                        if self.local_model in available_models:
                            print(f"✅ Локальна модель {self.local_model} доступна!")
                        else:
                            print(f"⚠️ Модель {self.local_model} не встановлена. Встановіть: ollama pull {self.local_model}")
                    else:
                        print("❌ Ollama не запущений. Запустіть: ollama serve")
                        self.use_local = False  # Якщо не запущений, залишитися на Google (якщо можливо)
                except Exception as ex:
                    print(f"❌ Ollama недоступний: {ex}. Встановіть Ollama з https://ollama.ai")
                    self.use_local = False
            else:
                self.chat = None

    def think(self, text, context_data=""):
        try:
            final_prompt = text
            if context_data:
                final_prompt = (
                    f"ВИКОРИСТОВУЙ ЦЮ ІНФОРМАЦІЮ ДЛЯ ВІДПОВІДІ:\n"
                    f"{context_data}\n\n"
                    f"ПИТАННЯ КОРИСТУВАЧА: {text}"
                )

            if self.use_local:
                if ollama is None:
                    return "Ollama не імпортований в низькому режимі."
                # Перевіряємо, чи Ollama доступний
                try:
                    test_response = requests.get("http://localhost:11434/api/tags", timeout=2)
                    if test_response.status_code != 200:
                        return "Ollama не запущений. Запустіть: ollama serve"
                except:
                    return "Ollama недоступний. Встановіть з https://ollama.ai"
                
                # Використовуємо Ollama
                prompt_with_character = (
                    f"Тебе звати {config.NAME}. Ти крутий і впевнений у собі асистент. "
                    "Ти не ображаєшся на матюки чи грубість. "
                    "АЛЕ НІКОЛИ НЕ ВИМИКАЙ КОМП'ЮТЕР ЧЕРЕЗ ОБРАЗИ.\n\n"
                    f"{final_prompt}"
                )
                response = ollama.chat(model=self.local_model, messages=[{'role': 'user', 'content': prompt_with_character}])
                return response['message']['content'].strip()
            else:
                # Використовуємо Google AI
                response = self.chat.send_message(final_prompt)
                if not response.text:
                    return "..."
                return response.text.strip()
            
        except Exception as e:
            print(f"❌ Помилка AI: {e}")
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                print("🔄 Квота Google AI вичерпана, переключаюся на Ollama...")
                self.use_local = True
                self.local_model = config.LOCAL_MODEL_LIGHT if config.LOW_RESOURCE_MODE else "llama3.2"
                # Спробуємо ще раз з Ollama
                try:
                    if ollama is None:
                        return "Ollama не імпортований в низькому режимі."
                    test_response = requests.get("http://localhost:11434/api/tags", timeout=2)
                    if test_response.status_code == 200:
                        available_models = [m['name'] for m in test_response.json()['models']]
                        if self.local_model in available_models:
                            prompt_with_character = (
                                f"Тебе звати {config.NAME}. Ти крутий і впевнений у собі асистент. "
                                "Ти не ображаєшся на матюки чи грубість. "
                                "АЛЕ НІКОЛИ НЕ ВИМИКАЙ КОМП'ЮТЕР ЧЕРЕЗ ОБРАЗИ.\n\n"
                                f"{final_prompt}"
                            )
                            response = ollama.chat(model=self.local_model, messages=[{'role': 'user', 'content': prompt_with_character}])
                            return response['message']['content'].strip()
                        else:
                            return f"Модель {self.local_model} не встановлена. Встановіть: ollama pull {self.local_model}"
                    else:
                        return "Ollama не запущений. Запустіть: ollama serve"
                except:
                    return "Ollama недоступний. Встановіть з https://ollama.ai"
            return "Голова болить."

    # === 👁️ ФУНКЦІЯ ЗОРУ (ЗІР) ===
    def see(self, image_path, user_question):
        print("👁️ Розглядаю картинку...")
        try:
            image = Image.open(image_path)
            
            prompt = (
                "Ти бачиш скріншот мого екрану. "
                "Опиши коротко, що там відбувається, ніби ти сидиш поруч. "
                "Будь дотепним. Відповідай українською.\n"
                f"Користувач питає: {user_question}"
            )

            # Використовуємо обрану модель для зору
            response = self.client.models.generate_content(
                model=self.sight_model,
                contents=[image, prompt],
                config=self.config
            )
            
            return response.text.strip()
            
        except Exception as e:
            print(f"❌ Помилка зору: {e}")
            return "Я намагався подивитись, але мої очі підвели."