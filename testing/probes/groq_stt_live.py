"""Explicit ONE-WAV probe. Not used by --all or normal Doctor."""
from dataclasses import replace
import json

from config import load_settings
from core.security import scrub_sensitive_environment
from services.audio.backends import create_backend
from services.audio.profiles import resolve_profile
from testing.probes.whisper import load_audio


def run(args):
    path = args.audio
    if path.suffix.lower() != '.wav' or path.stat().st_size > 24_000_000:
        raise ValueError('Expected one WAV <=24MB')
    settings = replace(load_settings(), stt_backend='groq', stt_groq_model=args.model)
    backend = create_backend(settings, resolve_profile(settings)[0])
    scrub_sensitive_environment()
    try:
        ok, detail = backend.prepare()
        if not ok:
            print(detail)
            return 1
        pcm, rate, _ = load_audio(path)
        result = backend.transcribe(pcm, rate)
        print(json.dumps(backend.last_metrics, ensure_ascii=False))
        print('Transcript (private probe log): ' + result.text)
        return int(bool(backend.last_error))
    finally:
        backend.close()
