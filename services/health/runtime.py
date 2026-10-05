"""Read-only snapshots and formatting of existing timings, not a policy owner."""
def latency_hud(values):
    def ms(key):
        value = values.get(key)
        return f'{value:.0f}ms' if isinstance(value, (int, float)) else 'n/a'
    return ('[TURN] endpoint=' + ms('endpoint') + ' | recognition_ready=' + ms('recognition_ready')
            + ' | stt=' + ms('stt_inference')
            + ' | llm_first_text=' + ms('llm_first_text')
            + ' | first_phrase=' + ms('first_phrase') + ' | tts_submit=' + ms('tts_submit')
            + ' | total=' + ms('total') + f' | model={values.get("model", "n/a")} | device={values.get("device", "n/a")}'
            + f' | escalation={values.get("escalated") if values.get("escalated") is not None else "n/a"}')


def runtime_status(app):
    from services.platform import resolve_platform
    target = resolve_platform()
    listener = getattr(app, 'listener', None)
    profile = getattr(listener, 'profile', None)
    metadata = getattr(getattr(listener, 'whisper', None), 'metadata', {})
    stt = (f'{profile.name} / {profile.backend} / {metadata.get("model", profile.model)} / '
           f'{metadata.get("device", profile.device)} {metadata.get("compute_type", profile.compute_type)}') if profile else 'n/a'
    proposals = getattr(getattr(getattr(app, 'processor', None), 'dialogue_state', None), 'proposals', None)
    last = getattr(getattr(app, 'performance', None), 'last_turn', {})
    result = (f'STT: {stt}\nEscalation: {getattr(profile, "escalation_model", "") or "none"}\n'
            f'TTS: requested voice {app.settings.tts_voice_hint}; resolved: n/a (use --doctor)\n'
            f'LLM: {app.llm.active_name or "none"}; active candidate, health not rechecked\n'
            f'Pending: confirmation={app.confirmation.awaiting}; proposal={bool(proposals and proposals.pending)}\n'
            f'Fallback: {metadata.get("fallback", "n/a")}\n{latency_hud(last)}')
    from core.logging_setup import _redact
    for secret in getattr(app.settings, 'api_keys', {}).values():
        if secret:
            result = result.replace(secret, '<redacted>')
    suffix = ' / Raspberry Pi' if target.report().get('raspberry_pi') is True else ''
    if not target.supports('tts'):
        result = result.replace(f'TTS: requested voice {app.settings.tts_voice_hint}; resolved: n/a (use --doctor)',
                                'TTS: NOT_IMPLEMENTED; text only')
    return f'Platform: {target.os} / {target.architecture}{suffix}\n' + _redact(result)
