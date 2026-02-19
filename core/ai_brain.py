# core/ai_brain.py
from google import genai
from google.genai import types
import config
from PIL import Image
from collections import deque
import platform 
import os
import json
import hashlib

class AIBrain:
    def __init__(self):
        self.client = genai.Client(api_key=config.GOOGLE_API_KEY)
        
        self.main_model = config.MAIN_MODEL
        self.vision_model = config.VISION_MODEL
        
        self.history = deque(maxlen=config.HISTORY_LIMIT)
        
        # Cache for template responses
        self.cache_file = os.path.expanduser("~/.valera_response_cache.json")
        self.response_cache = self._load_cache()
        
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
        """Initialize AI context with system prompt."""
        os_context = "Linux Mint" if self.os_type == "Linux" else "Windows"
        
        system_instruction = (
            f"SYSTEM OVERRIDE: Ти — {config.NAME}, голосовий асистент для {os_context}.\n"
            "Твоя задача — допомагати користувачу.\n\n"
            
            "ПРАВИЛА:\n"
            "- На звичайні питання (як справи, що таке, хто такий) — відповідай просто текстом!\n"
            "- НЕ пиши код [PYTHON: ...] для простих питань!\n"
            "- [PYTHON: ...] — ТІЛЬКИ для обчислень, файлів, системної інформації.\n"
            "- [CMD: ...] — ТІЛЬКИ для запуску програм (firefox, telegram, тощо).\n\n"
            
            "ПРИКЛАДИ:\n"
            "Q: Як себе почуваєш?\n"
            "A: Все добре, дякую! Готовий допомагати.\n\n"
            "Q: Скільки буде 2+2?\n"
            "A: [PYTHON: print(2+2)]\n\n"
            "Q: Відкрий браузер\n"
            "A: [CMD: firefox]\n"
        )
        
        self.history.append(types.Content(
            role="user", 
            parts=[types.Part(text="SYSTEM: " + system_instruction)]
        ))
        self.history.append(types.Content(
            role="model", 
            parts=[types.Part(text=f"Зрозуміло. Я — {config.NAME}. Готовий допомагати на {os_context}.")]
        ))

    def _load_cache(self):
        """Load response cache from file."""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except:
            pass
        return {}

    def _save_cache(self):
        """Save response cache to file."""
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.response_cache, f, ensure_ascii=False, indent=2)
        except:
            pass

    def _get_cache_key(self, text):
        """Create a cache key from text (normalize)."""
        # Normalize text - lowercase, remove extra spaces
        normalized = " ".join(text.lower().split())
        return hashlib.md5(normalized.encode()).hexdigest()

    def _is_template_phrase(self, text):
        """Check if this is a template-like phrase (doesn't need dynamic info)."""
        templates = [
            "як себе почуваєш", "як справи", "що ти вмієш", "допомога",
            "хто ти", "як тебе звати", "що таке", "хто такий",
            "привіт", "доброго ранку", "добрий вечір", "добрий день",
            "дякую", "подякувати", "до побачення", "на все добре",
            "генрі", "хенрі", "henry",
        ]
        text_lower = text.lower()
        return any(tmpl in text_lower for tmpl in templates)

    def think(self, text, context_data=""):
        # Normalize text
        normalized_text = " ".join(text.lower().split())
        cache_key = self._get_cache_key(text)
        
        # Check cache for template phrases
        if self._is_template_phrase(text) and cache_key in self.response_cache:
            cached_response = self.response_cache[cache_key]
            print(f"💾 Кеш: '{normalized_text[:30]}...'")
            return cached_response

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
            
            # Cache template responses
            if self._is_template_phrase(text):
                self.response_cache[cache_key] = answer
                self._save_cache()
                print(f"💾 Збережено в кеш: '{normalized_text[:30]}...'")
            
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