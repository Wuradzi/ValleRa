"""Conservative device retry classification; no dependency installation."""
from importlib.metadata import PackageNotFoundError, version


def runtime_versions():
    values = {}
    for name in ('ctranslate2', 'faster-whisper'):
        try:
            values[name] = version(name)
        except PackageNotFoundError:
            values[name] = 'not-installed'
    return values


def device_retry_possible(exc):
    # Type/config errors and generic OOM are NOT evidence of a CUDA failure.
    if not isinstance(exc, (RuntimeError, OSError)):
        return False
    message = str(exc).casefold()
    return any(marker in message for marker in (
        'cuda failed', 'cuda error', 'cuda driver', 'cuda runtime',
        'cuda out of memory', 'cuda_error_', 'cublas', 'cudnn',
        'cudart', 'libcuda.so', 'nvcuda.dll',
    ))
