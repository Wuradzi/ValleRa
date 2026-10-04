"""Small configurable deployment records, not model==profile policy."""
from dataclasses import dataclass
from services.audio.capabilities import detect_capabilities


def default_profiles():
    # Candidates, NOT empirically validated winners. Budgets are reporting targets.
    return {
        'quality': dict(backend='faster-whisper', model='large-v3', device='auto', compute_type='auto',
                        target_ram_mb=8192, target_rtf=1.0),
        'balanced': dict(backend='faster-whisper', model='small', device='cpu', compute_type='int8',
                         target_ram_mb=3072, target_rtf=1.0),
        'edge': dict(backend='sherpa-onnx', model='sherpa-onnx-whisper-small', device='cpu', compute_type='int8',
                     target_ram_mb=2048, target_rtf=1.0),
    }


@dataclass(frozen=True)
class STTProfile:
    name: str
    backend: str
    model: str
    device: str
    compute_type: str
    target_ram_mb: int
    target_rtf: float
    hotwords: str = ''
    initial_prompt: str = ''
    language: str = 'uk'
    escalation_model: str = ''
    escalation_confidence: float = .8


def resolve_profile(settings, capabilities=None):
    caps = capabilities or detect_capabilities()
    legacy = getattr(settings, 'stt_quality_profile', None)
    name = legacy or settings.stt_profile
    if name == 'low_resource':
        name = 'edge'
    if name == 'auto':
        if caps.architecture.startswith(('arm', 'aarch')):
            name = 'edge'
        elif caps.cuda and (caps.ram_mb or 0) >= 16384 and (caps.vram_mb or 0) >= 8192:
            name = 'quality'
        else:
            name = 'balanced'
    values = {**default_profiles()[name], **settings.stt_profiles.get(name, {})}
    # Explicit old config is preserved, never inferred as an automatic fallback.
    if settings.stt_backend != 'auto':
        values['backend'] = settings.stt_backend
    if legacy == 'low_resource' and settings.stt_backend != 'vosk':
        values['backend'], values['model'] = 'faster-whisper', settings.stt_low_resource_model
    elif legacy == 'balanced' and not settings.stt_profiles:
        values['model'] = 'large-v3-turbo'
    if settings.stt_backend == 'vosk':
        values['model'] = settings.stt_model_path
    device = settings.stt_primary_device if settings.stt_primary_device != 'auto' else values['device']
    if name in {'balanced', 'edge'}:
        device = 'cpu'
    elif device == 'auto':
        device = 'cuda' if caps.cuda else 'cpu'
    compute = values['compute_type']
    if compute == 'auto':
        compute = 'float16' if device == 'cuda' else 'int8'
    if values['backend'] == 'sherpa-onnx' and device != 'cpu':
        raise ValueError('sherpa-onnx adapter supports CPU only')
    return STTProfile(name, values['backend'], values['model'], device, compute,
        values['target_ram_mb'], values['target_rtf'], values.get('hotwords', settings.stt_whisper_hotwords),
        values.get('initial_prompt', settings.stt_whisper_prompt),
        escalation_model=values.get('escalation_model', ''),
        escalation_confidence=values.get('escalation_confidence', .8)), caps
