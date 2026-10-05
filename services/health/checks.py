from dataclasses import asdict, replace
from importlib.util import find_spec
import json
import os
from pathlib import Path
import platform
import sys

from config import load_settings, merge_config, validate_config
from services.audio.capabilities import detect_capabilities
from services.audio.profiles import resolve_profile
from services.audio.cuda_diagnostics import runtime_versions


def check(name, status, detail='', **data):
    return dict(name=name, status=status, detail=detail, data=data)


def exit_code(rows):
    return int(any(row['status'] == 'FAIL' for row in rows))


def configuration(root):
    path = Path(root) / 'config.json'
    if path.exists() and path.stat().st_size > 1_000_000:
        raise ValueError('Config exceeds diagnostic size limit')
    raw = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    value = merge_config(raw)
    validate_config(value)
    return value


def cached_model(settings, backend, model):
    """Inspect files only, never ask a Hub client to resolve/download."""
    from services.audio.model_cache import require_model_files
    if backend == 'faster-whisper':
        direct = settings.paths.project_root / model
        cache = settings.paths.models_dir / 'faster-whisper'
        paths = [direct] if direct.is_dir() else list(cache.glob(f'models--*--faster-whisper-{model}/snapshots/*'))
        for path in paths:
            try:
                require_model_files(path)
                return str(path), True
            except FileNotFoundError:
                pass
        return str(cache / model), False
    if backend == 'vosk':
        path = settings.paths.project_root / model
        return str(path), all((path / name).exists() for name in ('am/final.mdl', 'conf/model.conf'))
    path = Path(model)
    path = path if path.is_absolute() else settings.paths.models_dir / model
    return str(path), (any(path.glob('*encoder*.onnx')) and any(path.glob('*decoder*.onnx'))
                       and any(path.glob('*tokens.txt')))


def models(settings):
    active, _ = resolve_profile(settings)
    rows = []
    for name in ('quality', 'balanced', 'edge'):
        profile, _ = resolve_profile(replace(settings, stt_profile=name, stt_quality_profile=None))
        for role, model in (('primary', profile.model), ('escalation', profile.escalation_model)):
            if not model:
                continue
            path, present = cached_model(settings, profile.backend, model)
            rows.append(dict(profile=name, role=role, backend=profile.backend, model=model,
                             path=path, present=present, active=name == active.name,
                             device=profile.device, compute_type=profile.compute_type))
    path, present = cached_model(settings, 'vosk', settings.stt_model_path)
    rows.append(dict(profile='confirmation', role='confirmation', backend='vosk', model='configured-vosk',
                     path=path, present=present, active=True, device='cpu', compute_type='n/a'))
    return rows


def doctor(root, *, probe_audio=False, test_tts=True):
    from services.platform import resolve_platform
    target = resolve_platform().report()
    rows = []
    rows.append(check('Platform', 'PASS', f"{target['os']} / {target['architecture']}",
                      capabilities=target['capabilities'], hardware_validation='NOT_TESTED'))
    system = dict(python=sys.version.split()[0], os=platform.system(), architecture=platform.machine(),
                  project_root=str(Path(root).resolve()))
    try:
        cfg = configuration(root)
        settings = load_settings(read_only=True, root=root)
        rows.append(check('Config', 'PASS' if (Path(root) / 'config.json').exists() else 'WARN',
                          'validated; migration in memory only', config_version=cfg['config_version']))
    except Exception as exc:
        return [check('Config', 'FAIL', type(exc).__name__)], {'system': system}
    caps = detect_capabilities()
    system.update(ram_mb=caps.ram_mb)
    if target['os'] == 'Linux':
        import psutil
        system.update(cpu_count=os.cpu_count(), available_ram_mb=psutil.virtual_memory().available // 1048576,
                      cuda=caps.cuda)
        rows.append(check('Raspberry Pi', 'PASS' if target['raspberry_pi'] is not None else 'WARN',
                          'detected' if target['raspberry_pi'] else 'not detected or unknown'))
        rows.append(check('Resources', 'PASS', 'local numeric snapshot', ram_mb=caps.ram_mb,
                          available_ram_mb=system['available_ram_mb'], cpu_count=system['cpu_count']))
        rows.append(check('Desktop', 'NOT_AVAILABLE' if target['headless'] else 'PASS',
                          'headless; text Core supported' if target['headless'] else 'session hint; GUI NOT_TESTED'))
    for name in ('data_dir', 'cache_dir', 'logs_dir', 'models_dir'):
        path = getattr(settings.paths, name)
        parent = path
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        rows.append(check(name, 'PASS' if os.access(parent, os.W_OK) else 'FAIL',
                          'access hint only; no write probe', exists=path.exists()))
    inventory = models(settings)
    for model in inventory:
        required = model['active'] and model['role'] != 'escalation'
        rows.append(check(f"Model {model['profile']} {model['role']}", 'PASS' if model['present'] else ('FAIL' if required else 'WARN'),
                          'cached' if model['present'] else 'not cached; no download'))
    profile, _ = resolve_profile(settings, caps)
    rows.append(check('STT runtime', 'PASS' if find_spec(profile.backend.replace('-', '_')) else 'FAIL', profile.backend))
    rows.append(check('CUDA', 'PASS' if caps.cuda else 'WARN', 'available' if caps.cuda else 'unavailable; CPU profiles supported'))
    versions = runtime_versions()
    stt = dict(profile={k: v for k, v in asdict(profile).items() if k not in {'hotwords', 'initial_prompt'}}, versions=versions, cuda=caps.cuda,
               requested_device=settings.stt_primary_device, requested_compute_type=settings.stt_profiles.get(profile.name, {}).get('compute_type', 'auto'),
               cuda_libraries='NOT_TESTED: check driver/cuBLAS/cuDNN with live inference')
    from services.health.probes import audio_probe, audio_inventory, tts_self_test
    audio = audio_probe(settings) if probe_audio else audio_inventory(settings)
    rows.extend(audio)
    tts = tts_self_test(settings) if test_tts else check('TTS', 'NOT_TESTED', 'synthesis not requested')
    rows.append(tts)
    # Presence only, never unlock the vault or issue healthcheck requests.
    from dotenv import dotenv_values
    keys = dotenv_values(settings.paths.env_file) if settings.paths.env_file.exists() else {}
    for provider in settings.llm_order:
        key = f'{provider.upper()}_API_KEY'
        present = bool(os.environ.get(key) or keys.get(key)) if provider != 'ollama' else bool(settings.llm_models.get(provider))
        rows.append(check(f'LLM {provider}', 'PASS' if present else 'WARN',
                          'configuration present; health NOT_TESTED' if present else 'key/model absent or locked vault'))
    return rows, dict(system=system, platform=target, stt_status=stt, tts_status=tts, models=inventory)
