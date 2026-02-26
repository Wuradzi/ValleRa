"""
Note Skills - Управление заметками
"""

import os
import datetime


NOTES_FILE = os.path.expanduser("~/.valera_notes.txt")


def add_note(text, voice=None, listener=None):
    """Додає нотатку: 'запиши нотатку купити хліб'"""
    note = text.lower()
    ignore = ["запиши", "нотатку", "нотатка", "замітка", "додай", "запам'ятай", "додай нотатку"]
    for w in ignore:
        note = note.replace(w, " ").strip()
    
    note = " ".join(note.split())  # Видаляємо доданкові пробіли
    
    if not note:
        return "Що записати?"
    
    try:
        with open(NOTES_FILE, "a", encoding="utf-8") as f:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            f.write(f"[{timestamp}] {note}\n")
        return f"Записано: '{note}'"
    except Exception as e:
        return f"Помилка запису: {e}"


def show_notes(text=None, voice=None, listener=None):
    """Показує всі нотатки (останні 5)."""
    try:
        if not os.path.exists(NOTES_FILE):
            return "Немає збережених нотаток."
        
        with open(NOTES_FILE, "r", encoding="utf-8") as f:
            notes = f.read()
        
        if not notes.strip():
            return "Немає нотаток."
        
        lines = notes.strip().split("\n")[-5:]
        result = ["Останні нотатки:"]
        result.extend(lines)
        return "\n".join(result)
    except Exception as e:
        return f"Помилка: {e}"


def clear_notes(text=None, voice=None, listener=None):
    """Очищує всі нотатки."""
    try:
        if os.path.exists(NOTES_FILE):
            os.remove(NOTES_FILE)
        return "Нотатки очищено."
    except Exception as e:
        return f"Помилка: {e}"
