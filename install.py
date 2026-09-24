from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Інсталятор ValleRa")
    parser.add_argument(
        "--profile",
        choices=("desktop", "raspberry_pi"),
        default="desktop",
    )
    parser.add_argument("--with-extra-llm", action="store_true")
    parser.add_argument("--with-vision", action="store_true")
    return parser.parse_args()


def _install(root: Path, requirement_file: str) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            str(root / requirement_file),
        ],
        check=True,
    )


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parent
    subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], check=True)
    _install(root, "requirements.txt")

    if args.profile == "desktop":
        _install(root, "requirements-desktop.txt")
        _install(root, "requirements-whisper.txt")
    if args.with_extra_llm:
        _install(root, "requirements-llm-extra.txt")
    if args.with_vision:
        _install(root, "requirements-optional.txt")

    if platform.system() == "Windows":
        print("Встановіть RHVoice та український голос Volodymyr.")
    else:
        print(
            "Debian/Ubuntu: sudo apt install portaudio19-dev python3-tk "
            "xdotool wmctrl playerctl pulseaudio-utils tesseract-ocr "
            "tesseract-ocr-ukr rhvoice"
        )

    print("Далі: python tools/download_vosk_model.py")
    if args.profile == "desktop":
        print("Для точнішого STT: python tools/download_whisper_model.py")
    else:
        print("Запуск на Pi: python main.py --profile raspberry_pi")
    print("Потім: python main.py --setup")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
