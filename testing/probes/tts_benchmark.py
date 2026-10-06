"""Offline, no-playback TTS measurement. Only synthetic fixture registered yet."""
from datetime import datetime, timezone
import json
import multiprocessing
from pathlib import Path
import tempfile
import threading
import time
from uuid import uuid4
import wave

from testing.probes.voice_resources import VoiceResources, environment, public_report, read_checkpoint

PHRASES = ('Перевірка українського голосу.', 'Сьогодні гарна погода. Продовжимо нашу розмову.')


class FakeTTSBackend:
    """NOT a voice engine: silent deterministic PCM for harness tests only."""
    metadata = dict(backend='fake', model='synthetic-fixture', device='cpu', compute_type='pcm16')
    supports_cancellation = True

    def __init__(self, mode='success'):
        self.mode = mode

    def prepare(self):
        return True, 'fixture'

    def synthesize(self, text, path, cancelled):
        if self.mode == 'failure':
            raise RuntimeError('fixture failure')
        if self.mode == 'malformed':
            path.write_bytes(b'not a wave')
            return path
        if self.mode == 'cancel':
            cancelled.wait(.05)
            if cancelled.is_set():
                return None
        with wave.open(str(path), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(bytes(3200))
        return path

    def close(self):
        pass


def wav_info(path):
    with wave.open(str(path), 'rb') as audio:
        rate, frames, channels, width = (audio.getframerate(), audio.getnframes(),
                                        audio.getnchannels(), audio.getsampwidth())
        if rate <= 0 or frames <= 0 or channels not in (1, 2) or width not in (1, 2, 3, 4):
            raise ValueError('invalid PCM WAV')
        expected = frames * channels * width
        if expected > 32_000_000 or len(audio.readframes(frames)) != expected:
            raise ValueError('truncated or oversized WAV')
        return dict(sample_rate=rate, audio_seconds=frames / rate)


def benchmark(backend, checkpoint=None, cancellation=False):
    """Adapter: metadata, prepare()->(ok,detail), synthesize(text,path,event), close()."""
    monitor = VoiceResources(checkpoint).start()
    report = dict(kind='offline_tts_benchmark', **environment(), metadata=backend.metadata,
                  rows=[], human_quality_evaluation='NOT_TESTED', cancellation='not_requested',
                  status='failed', corpus_duration_seconds=0, sample_rates=[])
    try:
        monitor.before_load()
        started = time.perf_counter()
        try:
            ok, _ = backend.prepare()
        finally:
            report['load_ms'] = (time.perf_counter() - started) * 1000
        if not ok:
            report['reason'] = 'preparation_failed'
            raise RuntimeError('preparation_failed')
        monitor.inference()
        with tempfile.TemporaryDirectory(prefix='valera-tts-benchmark-') as directory:
            for index, text in enumerate(PHRASES):
                path = Path(directory) / f'{index}.wav'
                started = time.perf_counter()
                try:
                    backend.synthesize(text, path, threading.Event())
                finally:
                    elapsed = (time.perf_counter() - started) * 1000
                    report['last_synthesis_attempt_ms'] = elapsed
                info = wav_info(path)
                report['rows'].append(dict(id=index, synthesis_ms=elapsed,
                    synthesis_rtf=elapsed / 1000 / info['audio_seconds'], first_playable_audio_ms=None, **info))
                report['corpus_duration_seconds'] = sum(r['audio_seconds'] for r in report['rows'])
                report['sample_rates'] = sorted({r['sample_rate'] for r in report['rows']})
            if cancellation:
                if not getattr(backend, 'supports_cancellation', False):
                    report['cancellation'] = 'NOT_SUPPORTED'
                else:
                    event = threading.Event()
                    timer = threading.Timer(.02, event.set)
                    timer.start()
                    began = time.perf_counter()
                    try:
                        result = backend.synthesize(PHRASES[0], Path(directory) / 'cancel.wav', event)
                        report['cancellation'] = ('cancelled' if event.is_set() and result is None
                                                  else 'completed')
                        report['cancellation_ms'] = (time.perf_counter() - began) * 1000
                    finally:
                        timer.cancel()
                        timer.join()
        report['status'] = 'completed'
    except Exception as exc:
        report.update(status='failed', failure_type=type(exc).__name__)
    finally:
        report['resources'] = monitor.stop()
        try:
            backend.close()
        except Exception:
            report.update(status='failed', reason='cleanup_failed')
    return public_report(report)


def _worker(connection, checkpoint, mode, cancellation):
    try:
        connection.send(benchmark(FakeTTSBackend(mode), checkpoint, cancellation))
    finally:
        connection.close()


def run(args):
    context = multiprocessing.get_context('spawn')
    with tempfile.TemporaryDirectory(prefix='valera-tts-resources-') as directory:
        checkpoint = Path(directory) / 'resources.json'
        parent, child = context.Pipe(duplex=False)
        worker = context.Process(target=_worker, args=(child, checkpoint, args.fake_mode, args.cancellation))
        worker.start()
        child.close()
        try:
            report = parent.recv() if parent.poll(args.variant_timeout) else dict(status='timeout')
        except EOFError:
            report = dict(status='failed', reason='worker_exit')
        finally:
            worker.join(.2)
            if worker.is_alive():
                worker.terminate()
                worker.join(2)
            if worker.is_alive():
                worker.kill()
                worker.join(2)
            parent.close()
        if 'resources' not in report:
            report.update(resources=read_checkpoint(checkpoint), resources_partial=True)
        base = dict(kind='offline_tts_benchmark', metadata=FakeTTSBackend.metadata,
                    human_quality_evaluation='NOT_TESTED', **environment())
        base.update(report)
        report = public_report(base)
    folder = Path(__file__).resolve().parents[2] / 'logs' / 'tts-benchmark' / (
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8])
    folder.mkdir(parents=True)
    (folder / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('TTS fixture benchmark: ' + report['status'] + '; report in logs/tts-benchmark/' + folder.name)
    return int(report['status'] != 'completed')
