"""Same confirmed WAV corpus, isolated backend processes, no LLM or downloads."""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

from testing.probes.whisper import load_audio, score, normalize, edit_distance


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


def aggregate(rows):
    return {'samples': len(rows),
            'wer_percent': 100 * sum(r['word_errors'] for r in rows) / sum(r['reference_words'] for r in rows),
            'cer_percent': 100 * sum(r['char_errors'] for r in rows) / sum(r['reference_chars'] for r in rows),
            'average_transcription_ms': sum(r['latency_ms'] for r in rows) / len(rows),
            'realtime_factor': sum(r['latency_ms'] for r in rows) / 1000 / sum(r['audio_seconds'] for r in rows)}


def _variant(connection, directory, model, device):
    backend = None
    stop = threading.Event()
    resources = {'peak_rss_mb': None, 'peak_vram_mb': None,
                 'vram_note': 'not sampled; CPU has no VRAM, CUDA requires an external NVML sampler',
                 'rss_note': 'sampled whole isolated process RSS every 50ms, including model load'}

    def sample_ram():
        try:
            import psutil
            process = psutil.Process(os.getpid())
            while not stop.is_set():
                resources['peak_rss_mb'] = max(resources['peak_rss_mb'] or 0, process.memory_info().rss / 1048576)
                stop.wait(.05)
        except (ImportError, OSError):
            pass

    monitor = threading.Thread(target=sample_ram, daemon=True)
    monitor.start()
    report = {'requested_model': model, 'rows': []}
    try:
        from config import load_settings
        from services.audio.backends import FasterWhisperBackend, VoskBackend, primary_settings
        settings = load_settings()
        settings.stt_primary_device = device
        settings.stt_primary_local_files_only = True
        settings.stt_quality_profile = 'low_resource'
        settings.stt_low_resource_model = model
        samples, digest, synthetic = read_samples(directory)
        report.update(corpus_sha256=digest, synthetic=synthetic)
        backend = (VoskBackend(settings.paths.project_root / settings.stt_model_path) if model == 'vosk'
                   else FasterWhisperBackend(primary_settings(settings), process=False))
        started = time.perf_counter()
        ok, _ = backend.prepare()
        report.update(load_ms=(time.perf_counter() - started) * 1000, metadata=asdict(backend.metadata))
        if not ok:
            report.update(status='unavailable', reason='model_not_cached_or_load_failed')
        else:
            if model != 'vosk':
                report['metadata']['device'] = backend.recognizer.actual_device
                report['metadata']['compute_type'] = backend.recognizer.actual_compute_type
            for row, pcm, rate, duration in samples:
                started = time.perf_counter()
                result = backend.transcribe(pcm, rate)
                report['rows'].append({'id': row['id'], 'reference': row['reference'], 'transcript': result.text,
                    'engine': result.engine, 'audio_seconds': duration, 'latency_ms': (time.perf_counter() - started) * 1000,
                    **scores(row['reference'], result.text)})
            report.update(status='completed', summary=aggregate(report['rows']),
                          empty_transcripts=sum(not r['transcript'].strip() for r in report['rows']))
    except Exception as exc:
        report.update(status='failed', reason=type(exc).__name__)
    finally:
        stop.set()
        monitor.join(1)
        report['resources'] = resources
        if backend is not None:
            backend.close()
        connection.send(report)
        connection.close()


def run(args):
    read_samples(args.corpus)  # Validate once before starting any model.
    root = Path(__file__).resolve().parents[2]
    folder = root / 'logs' / 'stt-benchmark' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8])
    folder.mkdir(parents=True)
    report = {'kind': 'offline_stt_backend_comparison', 'network': False, 'llm_correction': False,
              'normalization': 'NFKC/casefold/punctuation removed; CER includes normalized spaces', 'variants': []}
    context = multiprocessing.get_context('spawn')
    for model in args.models:
        parent, child = context.Pipe(duplex=False)
        worker = context.Process(target=_variant, args=(child, args.corpus, model, args.device))
        worker.start()
        child.close()
        try:
            if parent.poll(args.variant_timeout):
                entry = parent.recv()
            else:
                entry = {'requested_model': model, 'status': 'timeout', 'rows': []}
        except EOFError:
            entry = {'requested_model': model, 'status': 'failed', 'reason': 'worker_exit', 'rows': []}
        finally:
            if worker.is_alive():
                worker.join(1)
            if worker.is_alive():
                worker.terminate()
            worker.join()
            parent.close()
        report['variants'].append(entry)
        (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(model + ': ' + entry['status'] + ' ' + json.dumps(entry.get('summary', {})), flush=True)
    print('Private benchmark report: ' + str(folder / 'report.json'), flush=True)
    return 0 if any(v['status'] == 'completed' for v in report['variants']) else 1
