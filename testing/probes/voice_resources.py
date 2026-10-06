"""Benchmark-only numeric sampling and crash checkpoints; no runtime policy."""
import hashlib
import json
import math
from pathlib import Path
import platform
import threading
import time

import psutil


def environment():
    return dict(platform=platform.system(), architecture=platform.machine(), python=platform.python_version())


def sensors():
    temperature = throttled = None
    if platform.system() == 'Linux':
        try:
            # Bounded numeric sysfs reads only, no process/network/device identity.
            with Path('/sys/class/thermal/thermal_zone0/temp').open() as source:
                value = float(source.read(32)) / 1000
            if math.isfinite(value) and -40 <= value <= 150:
                temperature = value
        except (OSError, ValueError):
            pass
        try:
            with Path('/sys/devices/platform/soc/soc:firmware/get_throttled').open() as source:
                value = int(source.read(32).strip(), 0)
            if 0 <= value <= 0xffffffff:
                throttled = value
        except (OSError, ValueError):
            pass
    return temperature, throttled


class VoiceResources:
    def __init__(self, checkpoint=None, interval=.05):
        self.checkpoint = Path(checkpoint) if checkpoint else None
        self.interval = interval
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.thread = None
        self.process = psutil.Process()
        self.started = time.monotonic()
        self.cpu_start = None
        self.swap_start = None
        self.phase = 'load'
        self.nvml = self.gpu = None
        self.data = dict(samples=0, rss_mb=None, peak_rss_mb=None,
            available_before_load_mb=None, available_ram_mb=None, min_available_inference_mb=None,
            cpu_seconds=None, cpu_one_core_percent=None, swap_used_mb=None, swap_used_delta_mb=None,
            swap_in_delta_bytes=None, swap_out_delta_bytes=None, temperature_c=None,
            peak_temperature_c=None, throttling_flags=None, throttling_flags_seen=None,
            peak_vram_mb=None, vram_note='NOT_TESTED',
            rss_note='sampled candidate process only; not exact OS peak or combined Core residency')

    def sample(self):
        with self.lock:
            try:
                available = psutil.virtual_memory().available / 1048576
                self.data['available_ram_mb'] = available
                if self.data['available_before_load_mb'] is None:
                    self.data['available_before_load_mb'] = available
                if self.phase == 'inference':
                    previous = self.data['min_available_inference_mb']
                    self.data['min_available_inference_mb'] = available if previous is None else min(previous, available)
                swap = psutil.swap_memory()
                if self.swap_start is None:
                    self.swap_start = swap
                self.data.update(swap_used_mb=swap.used / 1048576,
                    swap_used_delta_mb=(swap.used - self.swap_start.used) / 1048576,
                    swap_in_delta_bytes=max(0, swap.sin - self.swap_start.sin),
                    swap_out_delta_bytes=max(0, swap.sout - self.swap_start.sout))
            except (OSError, RuntimeError, NotImplementedError, psutil.Error):
                pass
            if self.nvml is not None:
                try:
                    used = self.nvml.nvmlDeviceGetMemoryInfo(self.gpu).used / 1048576
                    self.data['peak_vram_mb'] = max(self.data['peak_vram_mb'] or 0, used)
                except Exception:
                    pass
            try:
                rss = self.process.memory_info().rss / 1048576
                cpu = self.process.cpu_times()
                current = cpu.user + cpu.system
                if self.cpu_start is None:
                    self.cpu_start = current
                elapsed = max(.000001, time.monotonic() - self.started)
                self.data.update(rss_mb=rss, peak_rss_mb=max(self.data['peak_rss_mb'] or 0, rss),
                    cpu_seconds=max(0, current - self.cpu_start),
                    cpu_one_core_percent=100 * max(0, current - self.cpu_start) / elapsed)
            except (OSError, RuntimeError, NotImplementedError, psutil.Error):
                pass
            temperature, flags = sensors()
            self.data.update(temperature_c=temperature, throttling_flags=flags)
            if temperature is not None:
                self.data['peak_temperature_c'] = max(self.data['peak_temperature_c'] or temperature, temperature)
            if flags is not None:
                self.data['throttling_flags_seen'] = (self.data['throttling_flags_seen'] or 0) | flags
            self.data['samples'] += 1
            if self.data['samples'] == 1 or self.data['samples'] % 10 == 0:
                self._checkpoint()

    def _checkpoint(self):
        if self.checkpoint is not None:
            try:
                temporary = self.checkpoint.with_suffix('.tmp')
                temporary.write_text(json.dumps(self.data, allow_nan=False), encoding='utf-8')
                temporary.replace(self.checkpoint)
            except OSError:
                pass  # Last atomic checkpoint remains usable.

    def start(self):
        try:
            import pynvml
            pynvml.nvmlInit()
            self.nvml = pynvml
            self.gpu = pynvml.nvmlDeviceGetHandleByIndex(0)
            self.data['vram_note'] = 'total device 0 usage, including other processes'
        except Exception:
            pass
        self.sample()
        def monitor():
            while not self.stop_event.wait(self.interval):
                self.sample()
        self.thread = threading.Thread(target=monitor, daemon=True, name='voice-benchmark-resources')
        self.thread.start()
        return self

    def inference(self):
        with self.lock:
            self.phase = 'inference'
        self.sample()

    def before_load(self):
        self.sample()
        with self.lock:
            self.data['available_before_load_mb'] = self.data['available_ram_mb']
            self._checkpoint()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join()
        self.sample()
        self._checkpoint()
        if self.nvml is not None:
            try:
                self.nvml.nvmlShutdown()
            except Exception:
                pass
        return dict(self.data)


def read_checkpoint(path):
    try:
        # Written only by sampler; numeric allow-list prevents unexpected payloads.
        if Path(path).stat().st_size > 16384:
            return {}
        raw = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(raw, dict):
            return {}
        keys = VoiceResources().data
        return {k: v for k, v in raw.items() if k in keys and
                (v is None or type(v) in (int, float) and math.isfinite(v))}
    except (OSError, ValueError, psutil.Error):
        return {}


def public_report(value, key=''):
    """Preserve numeric schema, never export arbitrary paths/transcripts/errors."""
    if isinstance(value, dict):
        if key == 'entities_preserved':
            return {(k if len(k) == 71 and k.startswith('sha256:') and
                     all(c in '0123456789abcdef' for c in k[7:]) else
                     'sha256:' + hashlib.sha256(k.encode()).hexdigest()): v for k, v in value.items()}
        return {k: public_report(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [public_report(v, key) for v in value]
    if not isinstance(value, str):
        return value
    allowed = {'Windows', 'Linux', 'Darwin', 'AMD64', 'x86_64', 'aarch64', 'arm64',
        'cpu', 'cuda', 'auto', 'int8', 'float32', 'float16', 'int8_float16', 'unknown',
        'vosk', 'whisper', 'faster-whisper', 'sherpa-onnx', 'moonshine-onnx', 'moonshine',
        'tiny', 'base', 'small', 'medium', 'large-v3', 'large-v3-turbo', 'configured-vosk',
        'sherpa-onnx-whisper-small', 'quality', 'balanced', 'edge', 'uk', 'transcribe',
        'completed', 'failed', 'timeout', 'NOT_TESTED', 'NOT_SUPPORTED', 'cancelled',
        'fake', 'synthetic-fixture', 'pcm16', 'worker_exit', 'preparation_failed',
        'model_or_runtime_unavailable', 'cuda_unavailable', 'offline_stt_backend_comparison',
        'offline_tts_benchmark', 'not_requested', 'cooperative', 'cancel_timeout',
        'RuntimeError', 'ValueError', 'Error', 'EOFError', 'FileNotFoundError',
        'ModuleNotFoundError', 'MemoryError', 'OSError', 'cleanup_failed',
        'total device 0 usage, including other processes',
        'sampled candidate process only; not exact OS peak or combined Core residency'}
    if value in allowed:
        return value
    if key in {'python'} and all(c in '0123456789.' for c in value):
        return value
    if value.startswith('sha256:') and len(value) == 71 and all(c in '0123456789abcdef' for c in value[7:]):
        return value
    if key == 'corpus_sha256' and len(value) == 64 and all(c in '0123456789abcdef' for c in value):
        return value
    if key in {'id', 'model', 'requested_model', 'corpus_sha256'}:
        return 'sha256:' + hashlib.sha256(value.encode()).hexdigest()
    return '<omitted>'
