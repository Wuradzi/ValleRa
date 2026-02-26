"""
Search Skills - Поиск в интернете, погода, веб-навигация
"""

import webbrowser
import requests
from duckduckgo_search import DDGS


def search_internet(text):
    """Шукає інформацію в DuckDuckGo і повертає текст для читання."""
    query = text.replace("знайди інфу", "").replace("розкажи про", "").strip()
    print(f"🌎 Сканую інтернет: {query}")
    try:
        results = DDGS().text(query, max_results=3)
        if not results:
            return ""
        
        summary = []
        for r in results:
            summary.append(f"- {r['title']}: {r['body']}")
        
        return "\n".join(summary)
    except Exception as e:
        print(f"Search error: {e}")
        return ""


def check_weather(text):
    """Перевіряє поточну погоду."""
    ignore_words = ["погода", "weather", "скажи", "яка", "зараз", "у", "в"]
    city = text.lower()
    for word in ignore_words:
        city = city.replace(f" {word} ", " ").replace(word, "")
    
    city = city.strip()
    
    print(f"🌍 Дивлюсь погоду для: '{city}'")

    try:
        if city:
            url = f"https://wttr.in/{city}?format=3&lang=uk"
        else:
            url = "https://wttr.in/?format=3&lang=uk"
            
        r = requests.get(url, timeout=5)
        
        if r.status_code == 200:
            return r.text.strip()
        else:
            return "Сайт погоди не відповідає."
            
    except Exception as e:
        print(f"Weather Error: {e}")
        return "Не можу з'єднатися з сервером погоди."


def weather_forecast(text=None, voice=None, listener=None):
    """Прогноз погоди на кілька днів."""
    city = text.lower() if text else ""
    ignore = ["погода", "прогноз", "яка", "на", "тиждень", "днів", "дні"]
    for w in ignore:
        city = city.replace(w, "").strip()
    
    try:
        if city:
            url = f"https://wttr.in/{city}?format=%l+%c+%t+%h+%w+%m"
        else:
            url = "https://wttr.in/?format=%l+%c+%t+%h+%w+%m"
        
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            return f"Погода: {r.text.strip()}"
        else:
            return "Сайт погоди не відповідає."
    except Exception as e:
        return f"Помилка: {e}"


def search_google(t):
    """Відкриває Google з пошуком."""
    query = t.replace('гугл', '').strip()
    webbrowser.open(f"https://google.com/search?q={query}")
    return "Шукаю."


def search_youtube_clip(t):
    """Відкриває YouTube з пошуком."""
    query = t.replace('ютуб', '').strip()
    webbrowser.open(f"https://www.youtube.com/results?search_query={query}")
    return "Ютуб."
