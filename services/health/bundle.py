"""Allowlisted local artifacts; never copy logs, stores, env or recordings."""
from datetime import datetime, timezone
from importlib.metadata import version, PackageNotFoundError
import json
import math
from pathlib import Path
import re
import platform
import sys
from uuid import uuid4
import zipfile

from core.security import is_sensitive_env_name
from services.health.checks import configuration, doctor
from config import default_config

SECRET = re.compile(r'api.?key|token|secret|passw|authorization|credential|private.?key', re.I)


def schema_keys(value):
    if not isinstance(value, dict):
        return set()
    return set(value) | set().union(*(schema_keys(v) for v in value.values()))


SAFE_KEYS = schema_keys(default_config()) | {'api_key', 'token', 'password', 'secret', 'credentials', 'authorization',
    'system', 'stt_status', 'tts_status', 'os', 'python', 'architecture', 'ram_mb', 'cuda', 'versions', 'name',
    'status', 'detail', 'data', 'voice_requested', 'voice_resolved', 'culture', 'wav_bytes', 'fallback',
    'self_test_status', 'self_test_duration_ms', 'requested_device', 'requested_compute_type', 'cuda_libraries'}


def sanitize(value):
    # Diagnostics need configuration shape, not arbitrary string user data.
    # Redacting all strings also catches credentials stored under innocent keys.
    if isinstance(value, dict):
        return {str(k) if k in SAFE_KEYS else f'<field_{index}>':
                '<redacted>' if SECRET.search(str(k)) or is_sensitive_env_name(str(k)) else sanitize(v)
                for index, (k, v) in enumerate(value.items())}
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return '<redacted>'
    if value is None or type(value) is bool:
        return value
    # Numeric values under unknown keys can also be passwords/phone numbers.
    return '<redacted>'


STAGES = frozenset({'turn.endpoint_wait_estimate', 'turn.endpoint_to_recognition_ready',
    'turn.endpoint_to_tts_submit', 'turn.speech_end_estimate_to_tts_submit',
    'whisper.inference', 'llm.first_text', 'llm.gemini.first_text', 'tts.request_to_speak_call'})

BUNDLE_FILES = ('system.json', 'config.sanitized.json', 'dependencies.json', 'stt_status.json',
                'tts_status.json', 'performance_summary.json', 'summary.txt', 'recent_session_tail.log', 'manifest.json')


def performance_tail(root):
    path = Path(root) / 'data' / 'metrics.jsonl'
    if not path.is_file():
        return []
    with path.open('rb') as source:
        source.seek(max(0, path.stat().st_size - 65536))
        lines = source.read(65536).decode('utf-8', errors='replace').splitlines()
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
            stage, ms = row.get('stage'), row.get('duration_ms')
            if row.get('event') == 'performance' and stage in STAGES and type(ms) in (int, float) and math.isfinite(ms) and 0 <= ms <= 3600000:
                rows.append(dict(stage=stage, duration_ms=round(ms, 2)))
        except (ValueError, AttributeError, TypeError, RecursionError):
            continue
    return rows[-100:]


def collect(root, health=None):
    from services.platform import resolve_platform
    root = Path(root)
    rows, health = doctor(root, test_tts=False) if health is None else ([], health)
    folder = root / 'logs' / 'diagnostics' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8])
    folder.mkdir(parents=True)
    try:
        config = sanitize(configuration(root))
    except Exception:
        config = {'status': 'unreadable; values omitted'}
    deps = {}
    for name in ('faster-whisper', 'ctranslate2', 'vosk', 'sounddevice', 'pyttsx3', 'google-genai'):
        try:
            v = version(name)
            deps[name] = v if re.fullmatch(r'[0-9a-zA-Z.+-]{1,40}', v) else '<redacted>'
        except PackageNotFoundError:
            deps[name] = 'NOT_AVAILABLE'
    performance = performance_tail(root)
    public_profile = {}
    profile = health.get('stt_status', {}).get('profile', {})
    allowed = {'name': {'quality', 'balanced', 'edge'}, 'backend': {'faster-whisper', 'vosk', 'sherpa-onnx'},
               'model': {'tiny', 'base', 'small', 'medium', 'large-v3', 'large-v3-turbo', 'sherpa-onnx-whisper-small'},
               'device': {'cpu', 'cuda', 'auto'}, 'compute_type': {'int8', 'float16', 'float32', 'int8_float16', 'auto'},
               'escalation_model': {'', 'large-v3'}}
    for key, values in allowed.items():
        value = profile.get(key)
        public_profile[key] = value if isinstance(value, str) and value in values else '<redacted>'
    files = {
        'system.json': {'python': sys.version.split()[0], 'os': platform.system(), 'architecture': platform.machine(),
                        'platform': resolve_platform().report()},
        'config.sanitized.json': config,
        'dependencies.json': deps,
        'stt_status.json': {'profile': public_profile, 'cuda': health.get('stt_status', {}).get('cuda') is True},
        'tts_status.json': {'status': 'NOT_TESTED', 'reason': 'bundle collection does not synthesize audio'},
        'performance_summary.json': performance,
    }
    for name, content in files.items():
        (folder / name).write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding='utf-8')
    (folder / 'summary.txt').write_text(
        'LOCAL ONLY. No upload. Arbitrary strings/numbers redacted. No conversations, raw audio, env or stores.\n'
        + '\n'.join(f"{state}: {sum(r['status'] == state for r in rows)}" for state in ('PASS', 'WARN', 'FAIL')),
        encoding='utf-8')
    (folder / 'recent_session_tail.log').write_text(
        '# Reconstructed numeric performance only; raw session logs deliberately excluded.\n'
        + '\n'.join(f"{r['stage']}={r['duration_ms']}ms" for r in performance), encoding='utf-8')
    manifest = dict(bundle_version=1, created_at=datetime.now(timezone.utc).isoformat(),
                    files_included=list(BUNDLE_FILES),
                    files_excluded_by_policy=['.env', 'raw_session_logs', 'chat_history', 'clipboard',
                        'audio', 'memory', 'secret_stores_including_encrypted', 'private_notes', 'models', 'caches'],
                    redaction_applied=True, raw_audio_included=False, conversation_content_included=False)
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    with zipfile.ZipFile(folder / 'diagnostics.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in BUNDLE_FILES:
            archive.write(folder / name, name)
    return folder
