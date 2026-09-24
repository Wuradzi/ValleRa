"""Resolve complete local Whisper files before attempting a Hub request."""

from pathlib import Path


def require_model_files(source):
    path = Path(source)
    # Missing tokenizer.json triggers a separate Hub lookup inside WhisperModel.
    required = [path / name for name in ("model.bin", "config.json", "tokenizer.json")]
    if not all(file.is_file() and file.stat().st_size > 0 for file in required):
        raise FileNotFoundError("Whisper: неповні локальні файли моделі")
    if not any(file.is_file() and file.stat().st_size > 0 for file in path.glob("vocabulary.*")):
        raise FileNotFoundError("Whisper: відсутній локальний словник моделі")
    return str(path)


def resolve_model(source, cache_dir, download_model, *, local_files_only=False):
    if Path(source).is_dir():
        return require_model_files(source)
    try:
        cached = download_model(source, cache_dir=str(cache_dir), local_files_only=True)
        return require_model_files(cached)
    except FileNotFoundError:
        # Includes Hub LocalEntryNotFoundError/IncompleteSnapshotError. Invalid
        # names, permissions and native load errors must not trigger a download.
        if local_files_only:
            raise
    downloaded = download_model(source, cache_dir=str(cache_dir), local_files_only=False)
    return require_model_files(downloaded)
