"""Offline replay of a private, hash-checked recorded corpus.

Actual recognition models; simulated input delivery, not a live microphone or
wall-clock end-to-end latency measurement. No network, playback or commands.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import statistics
import time
from types import SimpleNamespace
from unittest.mock import patch
import uuid

from testing.probes.record import PHRASES
from testing.probes.whisper import load_audio, score


def read_corpus(directory):
    directory = Path(directory).resolve()
    index = directory / 'corpus.json'
    if index.stat().st_size > 1_000_000:
        raise ValueError('Corpus index too large')
    data = json.loads(index.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or data.get('synthetic') is not False:
        raise ValueError('Expected recorded corpus')
    expected = {p[0]: p[1] for p in PHRASES}
    selected = {}
    rows = data.get('samples')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 240:
        raise ValueError('Invalid sample count')
    for row in rows:
        if row.get('id') not in expected or row.get('reference') != expected[row['id']] or row.get('reference_confirmed') is not True:
            raise ValueError('Unconfirmed reference')
        name = row['file']
        if not isinstance(name, str) or Path(name).name != name or '\\' in name or not name.endswith('.wav'):
            raise ValueError('Invalid audio path')
        path = (directory / name).resolve()
        if path.parent != directory or path.stat().st_size > 8_000_000:
            raise ValueError('Audio outside corpus or too large')
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['wav_sha256']:
            raise ValueError('Audio hash mismatch')
        pcm, rate, duration = load_audio(path)
        if not .5 <= duration <= 20 or len(pcm) != round(duration * rate) * 2 or rate != row['sample_rate']:
            raise ValueError('Invalid audio format/duration')
        selected[row['id']] = (row, pcm, rate, duration)  # latest explicitly saved take
    if set(selected) != set(expected):
        raise ValueError('Expected all 24 phrases')
    return selected, hashlib.sha256(index.read_bytes()).hexdigest()


def replay(listener, pcm, rate):
    """Exercise production endpoint/energy code, feeding one block per get().

Queue has no backlog; real capture overflow/CPU scheduling are not simulated.
Append at most 4 s of digital silence to allow an endpoint after manual Stop.
"""
    import core.listen as module
    block = max(800, int(rate * getattr(listener.settings, 'stt_audio_block_ms', 250) / 1000)) * 2
    padded = pcm + b'\0\0' * (rate * 4)
    clock = {'offset': 0, 'time': 0.0, 'callback': None}
    class ReplayQueue:
        def __init__(self):
            self.item = None
        def put(self, item):
            self.item = item
        def empty(self):
            return self.item is None
        def get(self, timeout=None):
            if clock['offset'] >= len(padded):
                clock['time'] = 15.01
                raise queue.Empty
            data = padded[clock['offset']:clock['offset'] + block]
            clock['offset'] += len(data)
            clock['time'] = clock['offset'] / (2 * rate)
            clock['callback'](data, len(data) // 2, None, False)
            result, self.item = self.item, None
            return result
    class Stream:
        def __init__(self, **kwargs):
            clock['callback'] = kwargs['callback']
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    listener._interrupt.clear()
    with patch.object(module, 'queue', SimpleNamespace(Queue=ReplayQueue, Empty=queue.Empty)), \
            patch.object(module, 'time', SimpleNamespace(monotonic=lambda: clock['time'], perf_counter=time.perf_counter)):
        result, captured = listener._listen_on_device(SimpleNamespace(RawInputStream=Stream), 0, rate, 15, None)
    return result, captured


def full_vosk(model, pcm, rate):
    from vosk import KaldiRecognizer
    decoder = KaldiRecognizer(model, rate)
    parts = []
    for offset in range(0, len(pcm), rate // 2):
        if decoder.AcceptWaveform(pcm[offset:offset + rate // 2]):
            parts.append(json.loads(decoder.Result()).get('text', ''))
    parts.append(json.loads(decoder.FinalResult()).get('text', ''))
    return ' '.join(p for p in parts if p)


def summary(rows, modes=('vosk_full', 'whisper_full', 'current_pipeline')):
    output = {}
    for mode in modes:
        values = [r[mode] for r in rows]
        output[mode] = {
            'samples': len(values), 'exact': sum(v['exact'] for v in values),
            'word_errors': sum(v['word_errors'] for v in values),
            'reference_words': sum(v['reference_words'] for v in values),
            'wer_percent': round(100 * sum(v['word_errors'] for v in values) / sum(v['reference_words'] for v in values), 2),
            'prefix_errors': sum(not v['command_prefix_correct'] for v in values),
            'median_processing_seconds': round(statistics.median(v['seconds'] for v in values), 3),
        }
    return output


def resume_results(path, report, samples):
    """Validate a checkpoint; never modify the original measurement file."""
    path = Path(path)
    if path.stat().st_size > 2_000_000:
        raise ValueError('Checkpoint too large')
    raw = path.read_bytes()
    previous = json.loads(raw)
    if previous.get('refinement_policy', 'legacy') != report.get('refinement_policy', 'legacy'):
        raise ValueError('Checkpoint configuration mismatch: refinement_policy')
    if previous.get('endpoint_adaptive', False) != report.get('endpoint_adaptive', False):
        raise ValueError('Checkpoint configuration mismatch: endpoint_adaptive')
    for key in ('kind', 'synthetic', 'live_microphone', 'network', 'corpus_sha256',
                'model', 'beam', 'threads', 'endpoint_ms', 'block_ms'):
        if previous.get(key) != report[key]:
            raise ValueError(f'Checkpoint configuration mismatch: {key}')
    rows = previous.get('results')
    if not isinstance(rows, list) or len(rows) > len(samples):
        raise ValueError('Invalid checkpoint results')
    seen = set()
    for row in rows:
        name = row.get('id')
        if name not in samples or name in seen:
            raise ValueError('Unknown or duplicate checkpoint sample')
        seen.add(name)
        source, _, _, duration = samples[name]
        if row.get('file') != source['file'] or row.get('reference') != source['reference'] or row.get('audio_seconds') != duration:
            raise ValueError('Checkpoint sample mismatch')
        for mode in ('vosk_full', 'whisper_full', 'current_pipeline'):
            value = row.get(mode, {})
            seconds = value.get('seconds')
            if not isinstance(value.get('text'), str) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
                raise ValueError('Invalid checkpoint measurement')
            if any(value.get(k) != v for k, v in score(source['reference'], value['text']).items()):
                raise ValueError('Checkpoint score mismatch')
    return rows, {'path': str(path.resolve()), 'sha256': hashlib.sha256(raw).hexdigest(),
                  'reused_samples': len(rows), 'original_status': previous.get('status')}


def run(args):
    if getattr(args, 'endpoint_comparison', False):
        return run_endpoint_comparison(args)
    if getattr(args, 'policy_comparison', False):
        return run_policy_comparison(args)
    from config import load_settings
    from core.security import scrub_sensitive_environment
    from core.listen import VoskListener
    from services.audio.whisper_process import ProcessWhisperRecognizer
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    samples, index_hash = read_corpus(args.corpus)
    settings = load_settings()
    scrub_sensitive_environment()
    listener = VoskListener(settings)
    whisper = ProcessWhisperRecognizer(settings)
    whisper.settings.benchmark_local_files_only = True
    whisper.settings.benchmark_word_timestamps = True
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = settings.paths.logs_dir / f'recorded-stt-{stamp}-{uuid.uuid4().hex[:8]}.json'
    report = {'kind': 'private_recorded_audio_replay', 'synthetic': False, 'live_microphone': False,
              'network': False, 'corpus_sha256': index_hash, 'corpus': str(args.corpus),
              'model': settings.stt_whisper_model, 'beam': settings.stt_whisper_beam_size,
              'refinement_policy': settings.stt_refinement_policy,
              'endpoint_adaptive': settings.stt_endpoint_adaptive,
              'threads': settings.stt_whisper_cpu_threads, 'endpoint_ms': settings.stt_endpoint_silence_ms,
              'block_ms': settings.stt_audio_block_ms, 'status': 'running', 'results': [],
              'limitations': ['Reference confirmed by user, not independently transcribed.',
                  'WER counts acronym spelling differences; not semantic task accuracy.',
                  'Replay excludes device scheduling, overflow and live endpoint wall latency.',
                  'Four seconds of digital silence appended only for endpoint replay.']}
    if getattr(args, 'resume', None):
        report['results'], report['resumed_from'] = resume_results(args.resume, report, samples)
        report['limitations'].append('Resumed measurements span separate runs; machine load may differ.')
    completed = {row['id'] for row in report['results']}
    def save():
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    save()
    print(f'[RECORDED] report: {path}', flush=True)
    try:
        model = listener._get_model()
        if not whisper.prepare()[0]:
            raise RuntimeError('Cached Whisper unavailable')
        # Separate warm-up, not included in per-clip timing.
        _, warm_pcm, warm_rate, _ = samples[PHRASES[0][0]]
        whisper.transcribe(warm_pcm, warm_rate)
        for name, _, _ in PHRASES:
            if name in completed:
                continue
            source, pcm, rate, duration = samples[name]
            reference = source['reference']
            row = {'id': name, 'file': source['file'], 'reference': reference, 'audio_seconds': duration}
            start = time.perf_counter()
            vosk_text = full_vosk(model, pcm, rate)
            row['vosk_full'] = {'text': vosk_text, 'seconds': time.perf_counter() - start, **score(reference, vosk_text)}
            start = time.perf_counter()
            whole = whisper.transcribe(pcm, rate)
            if not whisper.status()[0]:
                raise RuntimeError('Whisper inference failed')
            row['whisper_full'] = {'text': whole.text, 'confidence': whole.confidence,
                                  'seconds': time.perf_counter() - start, **score(reference, whole.text)}
            start = time.perf_counter()
            endpoint_result, captured = replay(listener, pcm, rate)
            replay_seconds = time.perf_counter() - start
            refine, reason = listener._should_refine(endpoint_result, captured, rate)
            selected = endpoint_result
            if refine:
                refined = whisper.transcribe(captured, rate)
                if not whisper.status()[0]:
                    raise RuntimeError('Whisper inference failed')
                selected = listener._select_result(endpoint_result, refined)
            row['current_pipeline'] = {'text': selected.text, 'engine': selected.engine,
                'confidence': selected.confidence, 'seconds': time.perf_counter() - start,
                'refinement': refine, 'skip_reason': reason, 'vosk_replay_seconds': replay_seconds,
                'captured_seconds': len(captured) / (2 * rate), **score(reference, selected.text)}
            report['results'].append(row)
            save()
            print(json.dumps({'sample': name, 'completed': len(report['results']),
                'full_errors': row['whisper_full']['word_errors'],
                'pipeline_errors': row['current_pipeline']['word_errors'],
                'captured_s': round(len(captured) / (2 * rate), 2)}, ensure_ascii=False), flush=True)
        report['summary'] = summary(report['results'])
        report['status'] = 'completed'
        print(json.dumps(report['summary']), flush=True)
    except BaseException:
        report['status'] = 'incomplete'
        raise
    finally:
        whisper.close()
        listener.close()
        save()
    return 0


def run_endpoint_comparison(args):
    """Paired production capture replay, with no Whisper inference or live I/O."""
    from config import load_settings
    from core.listen import VoskListener
    from core.security import scrub_sensitive_environment
    from vosk import KaldiRecognizer

    samples, digest = read_corpus(args.corpus)
    settings = load_settings()
    scrub_sensitive_environment()
    if not settings.stt_endpoint_silence_ms or not settings.stt_whisper_enabled:
        raise ValueError('Comparison requires configured quiet endpoint and Whisper enabled')
    listener = VoskListener(settings)
    path = settings.paths.logs_dir / ('recorded-endpoint-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                                     + '-' + uuid.uuid4().hex[:8] + '.json')
    report = dict(kind='private_recorded_endpoint_comparison', status='running', results=[],
                  corpus_sha256=digest, synthetic=False, live_microphone=False, network=False,
                  block_ms=settings.stt_audio_block_ms, endpoint_ms=settings.stt_endpoint_silence_ms,
                  adaptive_block_ms=args.endpoint_block_ms,
                  vosk_model=settings.stt_model_path,
                  limitations=['Production capture with simulated delivery and ready Whisper status; no inference.',
                               'End estimate is last Vosk word in full audio, not human-aligned truth.',
                               'One speaker; no microphone scheduling, background-load or physical latency test.',
                               'WER is endpoint Vosk only, not final Vosk/Whisper pipeline accuracy.'])
    print(f'[RECORDED ENDPOINT] report: {path}', flush=True)
    try:
        model = listener._get_model()
        for name, (source, pcm, rate, _) in samples.items():
            decoder = KaldiRecognizer(model, rate)
            decoder.SetWords(True)
            words = []
            for offset in range(0, len(pcm), rate // 2 * 2):
                if decoder.AcceptWaveform(pcm[offset:offset + rate // 2 * 2]):
                    words.extend(json.loads(decoder.Result()).get('result', []))
            words.extend(json.loads(decoder.FinalResult()).get('result', []))
            end = words[-1]['end'] if words else None
            row = dict(id=name, speech_end_estimate_seconds=end)
            for mode in ('fixed', 'adaptive'):
                settings.stt_endpoint_adaptive = mode == 'adaptive'
                settings.stt_audio_block_ms = report['block_ms'] if mode == 'fixed' else args.endpoint_block_ms
                started = time.perf_counter()
                with patch.object(listener.whisper, 'status', return_value=(True, 'simulated ready')):
                    result, captured = replay(listener, pcm, rate)
                seconds = len(captured) / (2 * rate)
                refine, _ = listener._should_refine(result, captured, rate)
                row[mode] = dict(captured_seconds=seconds, seconds=time.perf_counter() - started,
                                 confidence=result.confidence, needs_refinement=refine,
                                 text=result.text, **score(source['reference'], result.text),
                                 estimated_truncated=None if end is None else seconds < end,
                                 endpoint_delay_ms=None if end is None else round((seconds - end) * 1000))
            row['saved_ms'] = round((row['fixed']['captured_seconds'] - row['adaptive']['captured_seconds']) * 1000)
            report['results'].append(row)
            print(json.dumps({'sample': name, 'saved_ms': row['saved_ms'],
                              'fixed_errors': row['fixed']['word_errors'],
                              'adaptive_errors': row['adaptive']['word_errors']}), flush=True)
        rows = report['results']
        report['summary'] = dict(samples=len(rows), faster=sum(r['saved_ms'] > 0 for r in rows),
            mean_saved_ms=round(statistics.mean(r['saved_ms'] for r in rows)),
            median_saved_ms=statistics.median(r['saved_ms'] for r in rows),
            changed_texts=sum(r['fixed']['text'] != r['adaptive']['text'] for r in rows),
            worse_word_errors=sum(r['adaptive']['word_errors'] > r['fixed']['word_errors'] for r in rows),
            new_refinements=sum(r['adaptive']['needs_refinement'] and not r['fixed']['needs_refinement'] for r in rows),
            new_estimated_truncations=sum(r['adaptive']['estimated_truncated'] is True
                and r['fixed']['estimated_truncated'] is False for r in rows),
            fixed_word_errors=sum(r['fixed']['word_errors'] for r in rows),
            adaptive_word_errors=sum(r['adaptive']['word_errors'] for r in rows))
        report['status'] = 'completed'
        print(json.dumps(report['summary']), flush=True)
    except BaseException:
        report['status'] = 'incomplete'
        raise
    finally:
        listener.close()
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0


def run_policy_comparison(args):
    """Paired inference: same endpoint PCM, no production config mutation."""
    from config import load_settings
    from core.atomic_json import AtomicJSONFile
    from core.security import scrub_sensitive_environment
    from core.listen import VoskListener
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    samples, digest = read_corpus(args.corpus)
    settings = load_settings()
    scrub_sensitive_environment()
    settings.stt_refinement_policy = 'legacy'
    listener = VoskListener(settings)
    whisper = listener.whisper
    whisper.settings.benchmark_local_files_only = True
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = settings.paths.logs_dir / f'recorded-policy-{stamp}-{uuid.uuid4().hex[:8]}.json'
    report = dict(kind='private_recorded_policy_comparison', status='running', results=[],
                  corpus_sha256=digest, synthetic=False, live_microphone=False, network=False,
                  model=settings.stt_whisper_model, beam=settings.stt_whisper_beam_size,
                  threads=settings.stt_whisper_cpu_threads, block_ms=settings.stt_audio_block_ms,
                  endpoint_ms=settings.stt_endpoint_silence_ms, vosk_threshold=0.85,
                  limitations=['Same endpoint/Whisper inference shared by policies; costs are component sums.',
                               'Recorded audio, simulated delivery; not live latency or command execution.',
                               'One speaker, user-confirmed references; not independently transcribed.'])
    storage = AtomicJSONFile(path, {})
    print(f'[RECORDED POLICY] report: {path}', flush=True)
    storage.save(report)
    try:
        listener._get_model()
        if not whisper.prepare()[0]:
            raise RuntimeError('Cached Whisper unavailable')
        _, pcm, rate, _ = samples[PHRASES[0][0]]
        whisper.transcribe(pcm, rate)
        for name, _, _ in PHRASES:
            source, pcm, rate, duration = samples[name]
            settings.stt_refinement_policy = 'legacy'
            start = time.perf_counter()
            endpoint, captured = replay(listener, pcm, rate)
            capture_cost = time.perf_counter() - start
            row = dict(id=name, reference=source['reference'], file=source['file'],
                       audio_seconds=duration, captured_seconds=len(captured) / (2 * rate))
            def measurement(result, cost, refine=False, reason=''):
                return dict(text=result.text, confidence=result.confidence, engine=result.engine,
                            seconds=cost, refinement=refine, skip_reason=reason,
                            **score(source['reference'], result.text))
            row['vosk_endpoint'] = measurement(endpoint, capture_cost)
            decisions = {}
            for policy in ('legacy', 'vosk_first'):
                settings.stt_refinement_policy = policy
                decisions[policy] = listener._should_refine(endpoint, captured, rate)
            refined, refine_cost = None, 0.0
            if any(decision[0] for decision in decisions.values()):
                start = time.perf_counter()
                refined = whisper.transcribe(captured, rate)
                refine_cost = time.perf_counter() - start
                if not whisper.status()[0]:
                    raise RuntimeError('Whisper inference failed')
            for policy, (refine, reason) in decisions.items():
                settings.stt_refinement_policy = policy
                selected = listener._select_result(endpoint, refined) if refine else endpoint
                row[policy] = measurement(selected, capture_cost + (refine_cost if refine else 0), refine, reason)
            report['results'].append(row)
            storage.save(report)
            print(json.dumps(dict(sample=name, completed=len(report['results']),
                                  vosk_errors=row['vosk_endpoint']['word_errors'],
                                  legacy_errors=row['legacy']['word_errors'],
                                  proposed_errors=row['vosk_first']['word_errors'],
                                  proposed_refinement=row['vosk_first']['refinement'])), flush=True)
        report['summary'] = summary(report['results'], ('vosk_endpoint', 'legacy', 'vosk_first'))
        report['status'] = 'completed'
        print(json.dumps(report['summary']), flush=True)
    except BaseException:
        report['status'] = 'incomplete'
        raise
    finally:
        listener.close()
        storage.save(report)
    return 0
