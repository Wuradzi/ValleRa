"""Single dispatch for explicitly requested local maintenance commands."""
from pathlib import Path
import time
import json
from datetime import datetime, timezone
from uuid import uuid4

from config import load_settings
from core.console import console_print as _console_print
from services.health.checks import check, doctor, exit_code, models
from services.health.probes import audio_probe, tts_self_test


def console_print(value):
    from core.logging_setup import _redact
    _console_print(_redact(str(value)))


def print_rows(rows):
    for row in rows:
        console_print(f"[{row['status']}] {row['name']}: {row['detail']}")
        if row['data']:
            console_print(str(row['data']))
    console_print(' | '.join(f'{state}: {sum(r["status"] == state for r in rows)}' for state in ('PASS', 'WARN', 'FAIL')))


def contextual_probe():
    from core.action_proposal import ProposalState, ProposalReply, ReplyKind, acceptance_matches
    from core.command_intent import CommandIntent
    state = ProposalState()
    proposal = state.offer(CommandIntent('open_app', {'name': 'браузер'}), 'smoke', 'smoke', {'apps'}, state.generation)
    lease = state.begin_turn('smoke')
    reply = ProposalReply(proposal.proposal_id, ReplyKind.ACCEPT)
    valid = (state.current(lease) and reply.proposal_id == lease.proposal.proposal_id
             and acceptance_matches('так', proposal.tool) and state.consume(lease) is not None)
    return check('Context followup', 'PASS' if valid else 'FAIL', 'local proposal lease/acceptance dry-run; no LLM or executor')


def smoke(settings, *, listener_factory=None, prompt=input, audio=audio_probe, tts=tts_self_test):
    from core.confirmation import ConfirmationService
    from core.stt_listener import SpeechListener
    prompt('Мікрофон: Enter, потім говоріть 3 секунди (локально, без збереження). ')
    rows = audio(settings)
    listener = (listener_factory or SpeechListener)(settings)
    try:
        ok, _ = listener.prepare()
        rows.append(check('STT preparation', 'PASS' if ok else 'FAIL', 'local cached model only'))
        if ok:
            for name, phrase, grammar, expected in (
                ('Natural STT', 'Як твої справи?', None, None),
                ('Command transcript', 'Відкрий калькулятор', None, None),
                ('Confirmation YES', 'так', ConfirmationService.GRAMMAR, True),
                ('Confirmation NO', 'ні', ConfirmationService.GRAMMAR, False),
            ):
                prompt(f'Enter, потім скажіть: «{phrase}». Жодна дія не виконується. ')
                started = time.perf_counter()
                result = listener.listen_once(10, grammar)
                valid = bool(result.text.strip()) and not result.recognition_unreliable
                if expected is not None:
                    valid = ConfirmationService._decision(result.text, result.confidence) is expected
                rows.append(check(name, 'PASS' if valid else 'FAIL', 'transcript content not stored; no tool execution',
                                  engine=result.engine, capture_and_decode_ms=round((time.perf_counter() - started) * 1000)))
            rows.append(check('STT latency', 'NOT_TESTED', 'capture aggregate is not speech-end latency; use session PERF'))
        rows.append(tts(settings))
        rows.append(contextual_probe())
    finally:
        listener.close()
    return rows


def run(args, root):
    # Reuse central log redaction, without loading credentials into providers.
    import os
    from dotenv import dotenv_values
    from core.logging_setup import register_secret
    from core.security import is_sensitive_env_name
    local = Path(root) / '.env'
    values = {**(dotenv_values(local) if local.exists() else {}), **os.environ}
    for name, value in values.items():
        if value and (is_sensitive_env_name(name) or 'AUTHORIZATION' in name.upper() or 'CREDENTIAL' in name.upper()):
            register_secret(str(value))
    if args.doctor:
        rows, info = doctor(root, probe_audio=args.probe_audio)
        console_print(str(info.get('system', {})))
        console_print(str(info.get('stt_status', {})))
    elif args.diagnostics:
        from services.health.bundle import collect
        folder = collect(root)
        console_print(f'Локальний пакет: {folder}. Нічого не завантажено в мережу.')
        return 0
    else:
        settings = load_settings(read_only=True, root=Path(root))
        if args.models:
            for item in models(settings):
                console_print(str(item))
            return 0
        if args.audio_test:
            console_print('Говоріть протягом 3 секунд. PCM не зберігається і не надсилається в мережу.')
            rows = audio_probe(settings)
        else:
            rows = smoke(settings)
            folder = settings.paths.logs_dir / 'smoke-live' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8])
            folder.mkdir(parents=True)
            state = 'FAIL' if exit_code(rows) else 'WARN' if any(r['status'] != 'PASS' for r in rows) else 'PASS'
            (folder / 'report.json').write_text(json.dumps({'status': state, 'checks': rows}, ensure_ascii=False, indent=2), encoding='utf-8')
            console_print(f'SMOKE RESULT: {state}; not a production readiness certificate. Report: {folder / "report.json"}')
    print_rows(rows)
    return exit_code(rows)
