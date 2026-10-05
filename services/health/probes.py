"""Explicit local probes. PCM is memory-only; neutral TTS WAV is temporary."""
from pathlib import Path
import tempfile
import time
import wave

from services.platform import resolve_platform
from services.health.checks import check


def audio_inventory(settings):
    try:
        import sounddevice as sd
        device = sd.query_devices(settings.input_device, 'input')
        rows = [check('Microphone', 'WARN', 'present; opening/signal NOT_TESTED (use --audio-test)',
                      device_name=device['name'], sample_rate=device['default_samplerate'])]
    except Exception as exc:
        linux = resolve_platform().os == 'Linux'
        rows = [check('Microphone', 'WARN' if linux else 'FAIL',
                      type(exc).__name__ + ('; check sounddevice, libportaudio2 and audio devices; text Core unaffected' if linux else ''))]
    try:
        import sounddevice as sd
        device = sd.query_devices(settings.output_device, 'output')
        rows.append(check('Audio output', 'PASS', 'device listed; playback NOT_TESTED'))
    except Exception:
        rows.append(check('Audio output', 'WARN', 'playback_unavailable'))
    return rows


def audio_probe(settings, seconds=3, sd=None):
    import numpy as np
    from services.audio.pcm_capture import AcousticEndpoint
    if sd is None:
        import sounddevice as sd
    try:
        device = sd.query_devices(settings.input_device, 'input')
        rate = round(device['default_samplerate'])
        data = sd.rec(round(seconds * rate), samplerate=rate, channels=1, dtype='float32', device=settings.input_device)
        sd.wait()
        values = np.asarray(data).reshape(-1)
        peak = float(np.max(np.abs(values))) if values.size else 0
        rms = float(np.sqrt(np.mean(values ** 2))) if values.size else 0
        threshold = max(.0025, min(.08, settings.noise_threshold * .75))
        endpoint = AcousticEndpoint(rate, threshold, settings.stt_endpoint_silence_ms,
                                    short_ms=settings.stt_pcm_short_silence_ms, long_ms=settings.stt_pcm_long_silence_ms)
        endpoint.feed((np.clip(values, -1, 1) * 32767).astype(np.int16).tobytes())
        return [check('Microphone', 'PASS' if endpoint.speech and peak < .99 else 'WARN',
                      'memory-only probe; no config changes', device=device['name'], index=settings.input_device,
                      sample_rate=rate, duration=seconds, peak=peak, rms=rms,
                      noise_floor_estimate=float(np.percentile(np.abs(values), 20)) if values.size else 0,
                      active_percent=100 * endpoint.active / max(1, endpoint.processed),
                      clipping=peak >= .99, speech_detected=endpoint.speech, threshold=threshold,
                      endpoint_ready=endpoint.ready, endpoint_threshold_ms=endpoint.threshold_ms)]
    except Exception as exc:
        return [check('Microphone', 'FAIL', type(exc).__name__)]


def tts_self_test(settings, synthesize=None):
    platform = resolve_platform()
    if not platform.supports('tts') and synthesize is None:
        return check('TTS', 'NOT_AVAILABLE', 'TTS NOT_IMPLEMENTED on this platform; text responses supported')
    started = time.perf_counter()
    data = dict(backend='Windows Speech', voice_requested=settings.tts_voice_hint,
                voice_resolved=None, culture=None, wav_bytes=0, temp_wav_exists=False,
                stage='temp_file_creation', error_type=None, exception_message=None, exit_code=None)
    status = 'synthesis_failed'
    try:
        with tempfile.TemporaryDirectory(prefix='valera-tts-check-') as folder:
            path = Path(folder) / 'probe.wav'
            data['stage'] = 'backend_initialization'
            result = (synthesize or platform.synthesize_probe)(path, settings.tts_voice_hint)
            data.update(voice_resolved=result.get('voice'), culture=result.get('culture'), fallback=result.get('fallback', False))
            data.update(stage=result.get('stage', 'backend_initialization'), error_type=result.get('error_type'),
                        exception_message=result.get('exception_message'), exit_code=result.get('exit_code'))
            status = result['status']
            data['temp_wav_exists'] = path.is_file()
            data['wav_bytes'] = path.stat().st_size if path.is_file() else 0
            if status == 'ok':
                data['stage'] = 'wav_validation'
                if not path.exists():
                    status = 'wav_not_created'
                else:
                    data['wav_bytes'] = path.stat().st_size
                    status = 'wav_empty'
                    if data['wav_bytes']:
                        with wave.open(str(path), 'rb') as wav:
                            if wav.getnframes() > 0 and wav.readframes(1):
                                status = 'ok'
    except Exception as exc:
        data.update(error_type=type(exc).__name__, exception_message=str(exc)[:2048])
        status = 'synthesis_failed' if status != 'wav_empty' else 'wav_empty'
    data.update(self_test_duration_ms=round((time.perf_counter() - started) * 1000, 1), self_test_status=status)
    return check('TTS', ('WARN' if data.get('fallback') else 'PASS') if status == 'ok' else 'FAIL', status, **data)
