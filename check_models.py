# check_models.py
from google import genai
import config

def check_available_models():
    print("🔑 Використовую ключ:", config.GOOGLE_API_KEY[:5] + "..." + config.GOOGLE_API_KEY[-5:])
    
    try:
        # У новій версії ми створюємо клієнта
        client = genai.Client(api_key=config.GOOGLE_API_KEY)
        
        print("📡 З'єднуюсь з Google серверами...")
        
        # Отримуємо список моделей
        # У новій версії це client.models.list()
        count = 0
        for model in client.models.list():
            print(f"✅ {model.name} | {model.display_name}")
            count += 1
                
            
    except Exception as e:
        print(f"❌ Помилка: {e}")

if __name__ == "__main__":
    check_available_models()