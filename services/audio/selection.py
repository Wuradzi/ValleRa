"""Persist only STT selection; running listeners are never replaced here."""
from dataclasses import replace
import json

from config import merge_config, validate_config
from core.atomic_json import AtomicJSONFile
from services.audio.profiles import resolve_profile


def save_selection(settings, selection):
    if selection not in ('local', 'groq'):
        raise ValueError('Unknown STT selection')
    path = settings.paths.config_file
    # Strict read: never replace a damaged config with defaults or stale backup.
    raw = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not isinstance(raw, dict) or not isinstance(raw.get('stt', {}), dict):
        raise ValueError('Invalid config')
    stt = raw.setdefault('stt', {})
    if selection == 'local':
        profile, _ = resolve_profile(replace(settings, stt_backend='auto',
                                            stt_profiles=stt.get('profiles', settings.stt_profiles)))
        if profile.backend == 'groq':
            raise ValueError('Selected local profile is configured as cloud')
    stt['backend'] = 'groq' if selection == 'groq' else 'auto'
    validate_config(merge_config(raw))
    AtomicJSONFile(path, {}).save(raw)
