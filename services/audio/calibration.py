from __future__ import annotations

from core.console import console_print as print

import numpy as np


def calibrate_microphone(device: int | None, sample_rate: int = 16000, seconds: float = 5.0) -> float:
    import sounddevice as sd

    device_info = sd.query_devices(device, "input")
    native_sample_rate = int(round(float(device_info.get("default_samplerate", 0))))
    if native_sample_rate > 0:
        sample_rate = native_sample_rate
    print(f"[AUDIO] Запис фонового шуму протягом {seconds:.0f} секунд. Не говоріть.")
    recording = sd.rec(
        int(seconds * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        device=device,
    )
    sd.wait()
    rms = float(np.sqrt(np.mean(np.square(recording))))
    threshold = max(0.005, min(0.2, rms * 2.5))
    print(
        f"[AUDIO] Пристрій: {device_info.get('name', device)}; "
        f"{sample_rate} Гц; RMS шуму: {rms:.5f}; поріг: {threshold:.5f}"
    )
    return threshold
