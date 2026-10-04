from __future__ import annotations

import sys
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import load_settings  # noqa: E402 - project path bootstrap above
from services.audio.backends import create_backend  # noqa: E402
from services.audio.profiles import resolve_profile  # noqa: E402


def main() -> int:
    settings = load_settings()
    parser = argparse.ArgumentParser(description='Explicit STT model preparation (legacy command name)')
    parser.add_argument('--profile', choices=['auto', 'quality', 'balanced', 'edge'])
    args = parser.parse_args()
    if args.profile:
        settings.stt_profile, settings.stt_quality_profile = args.profile, None
    profile, _ = resolve_profile(settings)
    recognizer = create_backend(settings, profile, process=False, allow_download=True)
    print(f'[STT] profile={profile.name} backend={profile.backend} model={profile.model}')
    try:
        ok, detail = recognizer.prepare()
    finally:
        recognizer.close()
    if not ok:
        print(f"[STT] Не вдалося підготувати Whisper: {detail}")
        return 1
    print(f"[STT] Whisper-модель готова: {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
