import os
import edge_tts
from pathlib import Path
import hashlib

AUDIO_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audio_cache")
VOICE = "uk-UA-OstapNeural"

PRE_RECORDED = {
    "слухаю": "listening.mp3", "я слухаю": "listening.mp3",
    "запускаю": "launching.mp3", "не знайшов": "not_found.mp3",
    "помилка": "error.mp3", "ок": "ok.mp3", "обчислюю": "calc.mp3"
}

def ensure_cache_dir(): Path(AUDIO_CACHE_DIR).mkdir(parents=True, exist_ok=True)

def get_audio_path(keyword):
    ensure_cache_dir()
    norm = " ".join(keyword.lower().split())
    if norm in PRE_RECORDED and os.path.exists(os.path.join(AUDIO_CACHE_DIR, PRE_RECORDED[norm])):
        return os.path.join(AUDIO_CACHE_DIR, PRE_RECORDED[norm])
    for key, fn in PRE_RECORDED.items():
        if key in norm and os.path.exists(os.path.join(AUDIO_CACHE_DIR, fn)):
            return os.path.join(AUDIO_CACHE_DIR, fn)
    return None

async def text_to_audio(text):
    ensure_cache_dir()
    norm = " ".join(text.lower().split())
    filename = hashlib.md5(norm.encode()).hexdigest()[:16] + ".mp3"
    filepath = os.path.join(AUDIO_CACHE_DIR, filename)
    if not os.path.exists(filepath):
        communicate = edge_tts.Communicate(text, VOICE)
        await communicate.save(filepath)
    return filepath