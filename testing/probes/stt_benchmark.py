"""Same confirmed WAV corpus, isolated backend processes, no LLM or downloads."""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing
import math
import statistics
from pathlib import Path
import tempfile
import time
from uuid import uuid4

from testing.probes.whisper import load_audio, score, normalize, edit_distance
from testing.probes.voice_resources import VoiceResources, environment, public_report, read_checkpoint


def read_samples(directory):
    directory = Path(directory).resolve()
    index = directory / 'corpus.json'
    if index.stat().st_size > 1_000_000:
        raise ValueError('Corpus index too large')
    raw = index.read_bytes()
    manifest = json.loads(raw)
    rows = manifest.get('samples', [])
    if not isinstance(rows, list) or not 1 <= len(rows) <= 240:
        raise ValueError('Expected 1..240 samples')
    samples = []
    for row in rows:
        if row.get('reference_confirmed') is not True or not isinstance(row.get('reference'), str):
            raise ValueError('Unconfirmed reference')
        if not normalize(row['reference']):
            raise ValueError('Empty reference')
        semantic = semantic_scores(row.get('semantic'), row['reference'])
        if semantic['intent_preserved'] is False or not all(semantic['entities_preserved'].values()):
            raise ValueError('Semantic annotation must match reference')
        name = row['file']
        path = (directory / name).resolve()
        if path.parent != directory or path.suffix.lower() != '.wav' or path.stat().st_size > 16_000_000:
            raise ValueError('Invalid audio path/size')
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['wav_sha256']:
            raise ValueError('Audio hash mismatch')
        pcm, rate, duration = load_audio(path)
        if len(pcm) != round(duration * rate) * 2 or not 0 < duration <= 120:
            raise ValueError('Invalid PCM')
        samples.append((row, pcm, rate, duration))
    return samples, hashlib.sha256(raw).hexdigest(), manifest.get('synthetic')


def scores(reference, hypothesis):
    result = score(reference, hypothesis)
    expected, actual = normalize(reference), normalize(hypothesis)
    result.update(char_errors=edit_distance(expected, actual), reference_chars=len(expected))
    result['cer_percent'] = 100 * result['char_errors'] / len(expected)
    return result


def semantic_scores(annotation, transcript):
    """Manual lexical evidence, not a general intent classifier or runtime policy."""
    if not annotation:
        return {'intent_preserved': None, 'entities_preserved': {}}
    if not isinstance(annotation, dict) or set(annotation) - {'intent', 'intent_phrases', 'entities'}:
        raise ValueError('Invalid semantic annotation')
    text = ' ' + normalize(transcript) + ' '

    def matches(values):
        values = [values] if isinstance(values, str) else values
        if not isinstance(values, list) or not values or any(not isinstance(v, str) or not normalize(v) for v in values):
            raise ValueError('Expected nonempty phrase alternatives')
        return any(' ' + normalize(v) + ' ' in text for v in values)

    entities = annotation.get('entities', {})
    if not isinstance(entities, dict):
        raise ValueError('entities must map names to phrase alternatives')
    return {'intent_preserved': matches(annotation['intent_phrases']) if annotation.get('intent_phrases') else None,
            'entities_preserved': {name: matches(values) for name, values in entities.items()}}


def aggregate(rows):
    latencies = sorted(r['latency_ms'] for r in rows)
    intents = [r['intent_preserved'] for r in rows if r.get('intent_preserved') is not None]
    entities = [value for r in rows for value in r.get('entities_preserved', {}).values()]
    return {'samples': len(rows),
            'wer_percent': 100 * sum(r['word_errors'] for r in rows) / sum(r['reference_words'] for r in rows),
            'cer_percent': 100 * sum(r['char_errors'] for r in rows) / sum(r['reference_chars'] for r in rows),
            'average_transcription_ms': sum(r['latency_ms'] for r in rows) / len(rows),
            'median_transcription_ms': statistics.median(latencies),
            'p95_transcription_ms': latencies[math.ceil(.95 * len(latencies)) - 1],
            'intent_annotated': len(intents), 'entities_annotated': len(entities),
            'intent_preservation_accuracy': sum(intents) / len(intents) if intents else None,
            'critical_entity_preservation_accuracy': sum(entities) / len(entities) if entities else None,
            'realtime_factor': sum(r['latency_ms'] for r in rows) / 1000 / sum(r['audio_seconds'] for r in rows)}


def _variant(connection, directory, candidate, device, checkpoint=None):
    backend = None
    monitor = VoiceResources(checkpoint).start()
    report = {'candidate': candidate, 'requested_model': candidate['model'], 'rows': [], **environment()}
    try:
        from config import load_settings
        from services.audio.backends import create_backend
        from services.audio.profiles import resolve_profile
        settings = load_settings()
        settings.stt_primary_device = device or 'auto'
        settings.stt_primary_local_files_only = True
        settings.stt_quality_profile = None
        settings.stt_profile = candidate.get('profile', 'balanced')
        settings.stt_backend = 'auto'
        settings.stt_profiles = {settings.stt_profile: {k: v for k, v in candidate.items() if k not in {'id', 'profile'}}}
        # Benchmark variants are reproducible, independent of personal hint tuning.
        settings.stt_whisper_prompt = settings.stt_whisper_hotwords = ''
        profile, capabilities = resolve_profile(settings)
        report.update(profile=asdict(profile), hardware=asdict(capabilities))
        from services.audio.cuda_diagnostics import runtime_versions
        report['versions'] = runtime_versions()
        if profile.device == 'cuda' and not capabilities.cuda:
            report.update(status='NOT_TESTED', reason='cuda_unavailable')
            return
        samples, digest, synthetic = read_samples(directory)
        report.update(corpus_sha256=digest, synthetic=synthetic,
                      sample_rates=sorted({s[2] for s in samples}), corpus_duration_seconds=sum(s[3] for s in samples))
        if profile.backend == 'moonshine-onnx':
            from testing.probes.moonshine_candidate import MoonshineCandidate
            backend = MoonshineCandidate(settings.paths.project_root / profile.model, settings.stt_whisper_cpu_threads)
        else:
            backend = create_backend(settings, profile, process=False)
        if profile.backend == 'faster-whisper':
            backend.recognizer.settings.benchmark_word_timestamps = candidate.get('word_timestamps', True)
            backend.recognizer.settings.benchmark_decode_options = (
                {'temperature': 0.0, 'best_of': 1} if candidate.get('single_pass', False) else {})
        monitor.before_load()
        started = time.perf_counter()
        report['metadata'] = asdict(backend.metadata)
        try:
            ok, detail = backend.prepare()
        finally:
            report['load_ms'] = (time.perf_counter() - started) * 1000
        report['metadata'] = asdict(backend.metadata)
        if not ok:
            unavailable = (backend.recognizer.preparation_unavailable if profile.backend == 'faster-whisper'
                           else any(token in detail for token in ('missing', 'FileNotFoundError', 'ModuleNotFoundError')))
            report.update(status='NOT_TESTED' if unavailable else 'failed',
                          reason='model_or_runtime_unavailable' if unavailable else 'preparation_failed', preparation_detail=detail)
        else:
            if profile.backend == 'faster-whisper':
                report['metadata']['device'] = backend.recognizer.actual_device
                report['metadata']['compute_type'] = backend.recognizer.actual_compute_type
                if profile.device == 'cuda' and backend.recognizer.actual_device != 'cuda':
                    raise RuntimeError('CUDA load fell back to CPU; not a GPU benchmark')
            # A real first inference, excluded from the warm distribution, catches
            # CUDA errors that weight loading alone cannot reveal.
            monitor.inference()
            _, pcm, rate, _ = samples[0]
            started = time.perf_counter()
            backend.transcribe(pcm, rate)
            report['first_inference_ms'] = (time.perf_counter() - started) * 1000
            if profile.backend == 'faster-whisper':
                if backend.recognizer.last_error:
                    raise RuntimeError('warmup failed: ' + backend.recognizer.last_error)
                if profile.device == 'cuda' and backend.recognizer.actual_device != 'cuda':
                    raise RuntimeError('CUDA candidate fell back to CPU; not a GPU benchmark')
            for row, pcm, rate, duration in samples:
                started = time.perf_counter()
                result = backend.transcribe(pcm, rate)
                if profile.backend == 'faster-whisper':
                    if backend.recognizer.last_error:
                        raise RuntimeError(backend.recognizer.last_error)
                    if profile.device == 'cuda' and backend.recognizer.actual_device != 'cuda':
                        raise RuntimeError('CUDA candidate fell back to CPU; not a GPU benchmark')
                report['rows'].append({'id': row['id'], 'reference': row['reference'], 'transcript': result.text,
                    'engine': result.engine, 'confidence': result.confidence,
                    'audio_seconds': duration, 'latency_ms': (time.perf_counter() - started) * 1000,
                    **scores(row['reference'], result.text), **semantic_scores(row.get('semantic'), result.text)})
            report.update(status='completed', summary=aggregate(report['rows']),
                          empty_transcripts=sum(not r['transcript'].strip() for r in report['rows']))
    except Exception as exc:
        report.update(status='failed', reason=type(exc).__name__)
    finally:
        report['resources'] = monitor.stop()
        try:
            if backend is not None:
                backend.close()
        except Exception:
            report.update(status='failed', reason='cleanup_failed')
        connection.send(public_report(report))
        connection.close()


def read_matrix(args):
    if getattr(args, 'matrix', None):
        path = Path(args.matrix)
        if path.stat().st_size > 100000:
            raise ValueError('Matrix too large')
        candidates = json.loads(path.read_text(encoding='utf-8'))['candidates']
    else:
        candidates = [dict(id=model, model='configured-vosk' if model == 'vosk' else model,
                           backend='vosk' if model == 'vosk' else 'faster-whisper',
                           profile='quality' if model.startswith('large') else 'balanced') for model in args.models]
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 24:
        raise ValueError('Expected 1..24 candidates')
    seen = set()
    for entry in candidates:
        if not isinstance(entry, dict) or set(entry) - {'id', 'backend', 'model', 'profile', 'device', 'compute_type', 'word_timestamps', 'single_pass'}:
            raise ValueError('Invalid candidate')
        if any(not isinstance(entry.get(k), str) or not entry[k] for k in ('id', 'backend', 'model')):
            raise ValueError('Candidate id/backend/model required')
        if entry['id'] in seen or entry['backend'] not in {'vosk', 'faster-whisper', 'sherpa-onnx', 'moonshine-onnx'}:
            raise ValueError('Duplicate/invalid candidate')
        seen.add(entry['id'])
        for option in ('word_timestamps', 'single_pass'):
            if option in entry and (type(entry[option]) is not bool or entry['backend'] != 'faster-whisper'):
                raise ValueError('Invalid Whisper benchmark option')
        if entry.get('profile', 'balanced') not in {'quality', 'balanced', 'edge'}:
            raise ValueError('Invalid candidate profile')
        if entry.get('device', 'auto') not in {'auto', 'cpu', 'cuda'}:
            raise ValueError('Invalid candidate device')
        if entry.get('compute_type', 'auto') not in {'auto', 'int8', 'float32', 'float16', 'int8_float16'}:
            raise ValueError('Invalid candidate compute')
    only = getattr(args, 'candidates', None)
    if only and not set(only) <= seen:
        raise ValueError('Unknown candidate id')
    return [entry for entry in candidates if not only or entry['id'] in only]


def run(args):
    if getattr(args, 'endpoint_only', False):
        from testing.probes.pcm_endpoint import run as replay
        return replay(args)
    samples, _, _ = read_samples(args.corpus)  # Validate before starting any model.
    corpus_metadata = dict(sample_rates=sorted({r[2] for r in samples}),
                           corpus_duration_seconds=sum(r[3] for r in samples))
    del samples  # Do not retain a second PCM corpus in the parent on small Pi RAM.
    candidates = read_matrix(args)
    root = Path(__file__).resolve().parents[2]
    folder = root / 'logs' / 'stt-benchmark' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8])
    folder.mkdir(parents=True)
    report = {'kind': 'offline_stt_backend_comparison', 'network': False, 'llm_correction': False,
              'normalization': 'NFKC/casefold/punctuation removed; CER includes normalized spaces', 'variants': []}
    context = multiprocessing.get_context('spawn')
    for candidate in candidates:
        checkpoint_dir = tempfile.TemporaryDirectory(prefix='valera-benchmark-')
        checkpoint = Path(checkpoint_dir.name) / 'resources.json'
        parent, child = context.Pipe(duplex=False)
        worker = context.Process(target=_variant, args=(child, args.corpus, candidate, args.device, checkpoint))
        worker.start()
        child.close()
        try:
            if parent.poll(args.variant_timeout):
                entry = parent.recv()
            else:
                entry = {'candidate': candidate, 'status': 'timeout', 'rows': []}
        except EOFError:
            entry = {'candidate': candidate, 'status': 'failed', 'reason': 'worker_exit', 'rows': []}
        finally:
            if worker.is_alive():
                worker.join(1)
            if worker.is_alive():
                worker.terminate()
            worker.join()
            parent.close()
            if 'resources' not in entry:
                entry['resources'] = read_checkpoint(checkpoint)
                entry['resources_partial'] = True
            checkpoint_dir.cleanup()
        entry = {**environment(), **corpus_metadata, **entry}
        entry = public_report(entry)
        report['variants'].append(entry)
        (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(public_report(candidate['id'], 'id') + ': ' + entry['status'] + ' ' + json.dumps(entry.get('summary', {})), flush=True)
    print('Benchmark report: logs/stt-benchmark/' + folder.name + '/report.json', flush=True)
    return 0 if any(v['status'] == 'completed' for v in report['variants']) else 1
