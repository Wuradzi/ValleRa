#!/usr/bin/env python3
"""
Простий тест для перевірки Ollama та моделі
"""
import requests
import config

def test_ollama():
    """Перевіряємо, чи Ollama запущений"""
    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=5)
        if response.status_code == 200:
            models = response.json()['models']
            print("✅ Ollama запущений!")
            print(f"📦 Встановлено моделей: {len(models)}")
            for model in models:
                print(f"  - {model['name']}")
            return True
        else:
            print("❌ Ollama не відповідає.")
            return False
    except Exception as e:
        print(f"❌ Ollama недоступний: {e}")
        print("💡 Запустіть: ollama serve")
        return False

def test_model():
    """Перевіряємо модель на простому запиті"""
    try:
        import ollama
        
        model_name = config.LOCAL_MODEL_LIGHT
        print(f"\n🧪 Тестую модель {model_name}...")
        
        response = ollama.chat(
            model=model_name,
            messages=[{'role': 'user', 'content': 'Привіт! Як твоє ім\'я?'}],
            stream=False
        )
        
        answer = response['message']['content']
        print(f"✅ Модель відповідає!")
        print(f"🤖 Модель каже: {answer}\n")
        return True
        
    except Exception as e:
        print(f"❌ Помилка моделі: {e}")
        return False

if __name__ == "__main__":
    print("=" * 50)
    print("🔍 Тест Ollama та моделі ValleRa")
    print("=" * 50)
    
    if test_ollama():
        test_model()
    
    print("=" * 50)
    print("✅ Тест завершено!")
