"""
Skills Module - Модульна система навичок для Валери

Кожна категорія навичок знаходиться у окремому файлі для кращої організації та підтримки.
"""

from .system_skills import *
from .program_skills import *
from .search_skills import *
from .note_skills import *
from .media_skills import *
from .utility_skills import *
from .translation_skills import *

__all__ = [
    # System
    'turn_off_pc',
    'cancel_shutdown',
    'lock_screen',
    'wake_up_pc',
    'system_status',
    
    # Programs
    'open_program',
    'close_app',
    'is_app_name',
    'list_processes',
    
    # Search
    'search_internet',
    'check_weather',
    'weather_forecast',
    'search_google',
    'search_youtube_clip',
    
    # Notes
    'add_note',
    'show_notes',
    'clear_notes',
    
    # Media
    'volume_up',
    'volume_down',
    'media_play_pause',
    'media_next',
    'media_prev',
    'click_play',
    'take_screenshot',
    'look_at_screen',
    
    # Utilities
    'get_time',
    'get_date',
    'timer',
    'check_timers',
    'calculator',
    'read_clipboard',
    'remember_data',
    'recall_data',
    'get_help',
    
    # Translation
    'translate_text',
    'show_translations',
]
