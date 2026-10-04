"""Offline acoustic replay, not a live microphone latency measurement."""
import json

from services.audio.pcm_capture import AcousticEndpoint


def compare(samples):
    rows = []
    for row, pcm, rate, duration in samples:
        for short in (700, 1200):
            endpoint = AcousticEndpoint(rate, .01125, 1200, short_ms=short)
            # The complete file provides an acoustic reference, not manual speech labels.
            reference = AcousticEndpoint(rate, .01125)
            reference.feed(pcm)
            padded = pcm + bytes(rate * 6)
            block = round(rate * .1) * 2
            detected = None
            for offset in range(0, len(padded), block):
                endpoint.feed(padded[offset:offset + block])
                if endpoint.ready:
                    detected = endpoint.processed / rate
                    break
            rows.append({'id': row['id'], 'short_ms': short, 'audio_seconds': duration,
                         'reference_acoustic_end_s': reference.last_active / rate,
                         'endpoint_s': detected,
                         'possible_early_cut': detected is not None and detected < reference.last_active / rate,
                         'end_to_endpoint_ms': (detected - reference.last_active / rate) * 1000
                         if detected is not None else None})
    return rows


def run(args):
    from testing.probes.stt_benchmark import read_samples
    samples, digest, synthetic = read_samples(args.corpus)
    report = {'kind': 'offline_pcm_endpoint_replay', 'corpus_sha256': digest, 'synthetic': synthetic,
              'block_ms': 100, 'energy_threshold': .01125, 'rows': compare(samples),
              'limitation': 'Energy-estimated end, no microphone/CPU wall timing; not proof against clipping.'}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0
