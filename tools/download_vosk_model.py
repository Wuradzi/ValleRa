from __future__ import annotations

import shutil
import urllib.request
import zipfile
from pathlib import Path


MODEL_NAME = "vosk-model-small-uk-v3-small"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    models_dir = root / "models"
    target = models_dir / MODEL_NAME
    archive = models_dir / f"{MODEL_NAME}.zip"
    models_dir.mkdir(parents=True, exist_ok=True)

    if target.exists():
        print(f"Модель уже встановлена: {target}")
        return 0

    print(f"Завантаження {MODEL_URL}")
    with urllib.request.urlopen(MODEL_URL, timeout=60) as response, archive.open("wb") as file:
        shutil.copyfileobj(response, file)

    with zipfile.ZipFile(archive) as zip_file:
        zip_file.extractall(models_dir)
    archive.unlink(missing_ok=True)
    print(f"Модель встановлено: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
