"""
Utility Skills - Утилиты (время, таймер, калькулятор, память)
"""

import os
import json
import datetime
import re
import time


TIMERS = {}
MEMORY_FILE = os.path.expanduser("~/.valera_memory.json")


def get_time(text=None):
    """Повертає поточний час."""
    return datetime.datetime.now().strftime("%H:%M")


def get_date(text=None):
    """Повертає поточну дату."""
    return str(datetime.date.today())


def timer(text, voice=None, listener=None):
    """Таймер: 'таймер 5 хвилин' або 'нагадай через 10 секунд'"""
    match = re.search(r'(\d+)', text)
    if not match:
        return "Скажи, скільки хвилин або секунд."
    
    value = int(match.group(1))
    duration = value
    
    if "секунд" in text.lower() or "сек" in text.lower():
        duration = value
        unit = "секунд"
    elif "хвилин" in text.lower() or "хв" in text.lower():
        duration = value * 60
        unit = "хвилин"
    elif "годин" in text.lower() or "год" in text.lower():
        duration = value * 3600
        unit = "годин"
    else:
        duration = value * 60
        unit = "хвилин"
    
    end_time = time.time() + duration
    TIMERS["active"] = end_time
    
    return f"Таймер на {value} {unit} запущено. Сповіщу через {value} {unit}."


def check_timers():
    """Перевіряє таймери (для виклику з циклу)."""
    if "active" in TIMERS:
        if time.time() >= TIMERS["active"]:
            del TIMERS["active"]
            return True
    return False


def calculator(text, voice=None, listener=None):
    """Простий калькулятор: 'порахуй 2+2' або 'скільки буде 10*5'"""
    expr = text.lower()
    ignore_words = ["порахуй", "скільки", "буде", "дорівнює", "равно"]
    for w in ignore_words:
        expr = expr.replace(w, "").strip()
    
    expr = expr.replace("×", "*").replace("х", "*")
    expr = expr.replace("÷", "/").replace(":", "/")
    expr = expr.replace("плюс", "+").replace("мінус", "-").replace("помножити", "*").replace("поділити", "/")
    
    allowed = "0123456789+-*/(). "
    expr = ''.join(c for c in expr if c in allowed)
    
    try:
        result = eval(expr)
        if result == int(result):
            result = int(result)
        return f"{expr} = {result}"
    except:
        return "Не розумію вираз. Скажи: 'порахуй 2+2'"


def _load_memory():
    """Завантажує пам'ять з файлу."""
    try:
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except:
        pass
    return {}


def _save_memory(data):
    """Зберігає пам'ять у файл."""
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except:
        pass


def remember_data(text, voice=None, listener=None):
    """Запам'ятовує дані. Формат: 'ключ: значення' або 'ключ = значення'"""
    try:
        # Видаляємо команду
        clean = text.lower()
        for cmd in ["запам'ятай", "запам'ятати", "запиши", "запиши"]:
            clean = clean.replace(cmd, "", 1).strip()
        
        # Шукаємо розділювач
        if ":" in clean:
            key, value = clean.split(":", 1)
        elif "=" in clean:
            key, value = clean.split("=", 1)
        else:
            return "Використай формат: 'запам'ятай ключ: значення'"
        
        key = key.strip().lower()
        value = value.strip()
        
        if not key or not value:
            return "Ключ і значення не можуть бути пусті."
        
        memory = _load_memory()
        memory[key] = value
        _save_memory(memory)
        
        return f"Запам'ятав: {key} = {value}"
    except Exception as e:
        return f"Помилка: {e}"


def recall_data(text, voice=None, listener=None):
    """Згадує запам'ятані дані."""
    query = text.lower().strip()
    memory = _load_memory()
    
    if not memory:
        return "Пам'ять порожня."
    
    if query in memory:
        return f"{query}: {memory[query]}"
    
    for key, value in memory.items():
        if query in key or key in query:
            return f"{key}: {value}"
    
    if "що ти знаєш" in query or "все" in query:
        if len(memory) <= 5:
            items = "\n".join([f"- {k}: {v}" for k, v in memory.items()])
            return f"Пам'ятаю:\n{items}"
        else:
            count = len(memory)
            keys = ", ".join(list(memory.keys())[:5])
            return f"Пам'ятаю {count} записів: {keys}..."
    
    return f"Не знайшов '{query}' в пам'яті."


def get_help(text=None, voice=None, listener=None):
    """Показує всі доступні команди."""
    help_text = """
🤖 ВАЛЕРА - Допомога

🎤 ГОЛОСОВІ КОМАНДИ:
• "Валєра, який час?" - поточний час
• "Валєра, яка дата?" - поточна дата
• "Валєра, таймер 5 хвилин" - встановлює таймер
• "Валєра, порахуй 10*5" - калькулятор

💻 ПРОГРАМИ:
• "Валєра, відкрий Firefox" - запускає програму
• "Валєра, які процеси?" - список процесів

🌐 ПОШУК:
• "Валєра, знайди інформацію про Python" - веб-пошук
• "Валєра, переклади hello на англійську" - переклад

📝 НОТАТКИ:
• "Валєра, запиши нотатку купити хліб" - додає нотатку
• "Валєра, нотатки" - показує нотатки

📸 ІНШЕ:
• "Валєра, скріншот" - знімок екрану
• "Валєра, заблокуй" - блокує екран
• "Валєра, буди" - активує комп'ютер

📖 ПАМ'ЯТЬ:
• "Валєра, запам'ятай ключ: значення" - зберігає дані
• "Валєра, що ти знаєш?" - показує пам'ять
    """.strip()
    return help_text
