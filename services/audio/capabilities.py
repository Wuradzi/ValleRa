"""The only STT hardware detection boundary; no device product-name checks."""
from dataclasses import dataclass
from importlib.util import find_spec
import platform


@dataclass(frozen=True)
class Capabilities:
    architecture: str
    platform: str
    cuda: bool = False
    ram_mb: int | None = None
    vram_mb: int | None = None
    runtimes: tuple[str, ...] = ()


def cuda_available():
    try:
        from ctranslate2 import get_cuda_device_count
        return get_cuda_device_count() > 0
    except (ImportError, OSError, RuntimeError):
        return False


def detect_capabilities():
    machine = platform.machine().lower()
    architecture = {'amd64': 'x86_64', 'aarch64': 'arm64'}.get(machine, machine)
    ram = vram = None
    try:
        import psutil
        ram = psutil.virtual_memory().total // 1048576
    except (ImportError, OSError):
        pass
    # Optional NVML. Unknown VRAM never authorizes auto selection of quality.
    try:
        import pynvml
        pynvml.nvmlInit()
        try:
            vram = pynvml.nvmlDeviceGetMemoryInfo(pynvml.nvmlDeviceGetHandleByIndex(0)).total // 1048576
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        pass
    runtimes = tuple(name for name, module in [('faster-whisper', 'faster_whisper'),
                     ('sherpa-onnx', 'sherpa_onnx'), ('vosk', 'vosk')] if find_spec(module) is not None)
    return Capabilities(architecture, platform.system().lower(), cuda_available(), ram, vram, runtimes)
