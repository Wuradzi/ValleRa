"""
Translation Skills - Управление переводами
"""

import os
import json


TRANSLATIONS_FILE = os.path.expanduser("~/.valera_translations.json")


def _load_translations():
    """Завантажує переклади."""
    try:
        if os.path.exists(TRANSLATIONS_FILE):
            with open(TRANSLATIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except:
        pass
    return {}


def _save_translations(data):
    """Зберігає переклади."""
    try:
        with open(TRANSLATIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except:
        pass


def translate_text(text, voice=None, listener=None):
    """Переклад: 'переклади hello на українську' або 'перекладіть привіт на англійську'"""
    target_lang = "uk"
    source_text = text.lower()
    
    if "на англійську" in text or "на english" in text or "на англійський" in text:
        target_lang = "en"
        source_text = source_text.replace("на англійську", "").replace("на english", "").replace("на англійський", "")
    elif "на українську" in text:
        target_lang = "uk"
        source_text = source_text.replace("на українську", "")
    elif "російську" in text or "російською" in text:
        target_lang = "ru"
        source_text = source_text.replace("російську", "").replace("російською", "")
    
    ignore = ["переклади", "перекладіть", "переклад", "переведи"]
    for w in ignore:
        source_text = source_text.replace(w, "").strip()
    
    if not source_text:
        return "Що перекласти?"
    
    try:
        from googletrans import Translator
        translator = Translator()
        
        if target_lang == "en":
            result = translator.translate(source_text, src="uk", dest="en")
        elif target_lang == "ru":
            result = translator.translate(source_text, src="uk", dest="ru")
        else:
            result = translator.translate(source_text, src="auto", dest="uk")
        
        history = _load_translations()
        key = f"{source_text[:20]}..."
        history[key] = {"original": source_text, "translated": result.text, "lang": target_lang}
        _save_translations(history)
        
        return f"{source_text} → {result.text}"
    except ImportError:
        simple_dict = {
            "hello": "привіт",
            "goodbye": "до побачення",
            "thanks": "дякую",
            "please": "будь ласка",
            "yes": "так",
            "no": "ні",
            "привіт": "hello",
            "дякую": "thanks",
        }
        if source_text in simple_dict:
            return f"{source_text} → {simple_dict[source_text]}"
        return "Немає перекладача. Встанови: pip install googletrans"


def show_translations(text=None, voice=None, listener=None):
    """Показує історію перекладів (останні 5)."""
    history = _load_translations()
    if not history:
        return "Немає збережених перекладів."
    
    items = list(history.items())[-5:]
    lines = ["Останні переклади:"]
    for key, data in items:
        lines.append(f"{data['original']} → {data['translated']}")
    
    return "\n".join(lines)
