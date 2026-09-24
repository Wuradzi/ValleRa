from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import load_settings  # noqa: E402 - project path bootstrap above
from services.audio.whisper import WhisperRecognizer  # noqa: E402


def main() -> int:
    settings = load_settings()
    recognizer = WhisperRecognizer(settings)
    ok, detail = recognizer.prepare()
    if not ok:
        print(f"[STT] Не вдалося підготувати Whisper: {detail}")
        return 1
    print(f"[STT] Whisper-модель готова: {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
