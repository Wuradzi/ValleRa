"""Content-free recognition diagnostics; never used to grant permissions."""


def recognition_reason(result):
    if result.engine == 'interrupted':
        return 'interrupted'
    if result.capture_truncated:
        return 'capture_truncated'
    if result.recognition_unreliable:
        return 'recognition_unreliable'
    if not result.text.strip():
        return 'empty_transcript'
    if result.utterance_incomplete or result.incomplete:
        return 'utterance_incomplete'
    return 'recognized_unscored' if not result.confidence_available else 'recognized'


def backend_failure_reason(outcome, backend):
    # A still-draining request can expose the previous job's last_error.
    if outcome != 'done':
        return outcome if outcome in {'timeout', 'cancelled', 'busy', 'error'} else 'not_completed'
    reason = getattr(backend, 'last_error', '')
    return reason if reason in {
        'non_ukrainian_transcript', 'empty_transcription', 'timeout', 'network',
        'authorization', 'rate_limit', 'server', 'http_error', 'malformed_response',
        'cancelled', 'invalid_audio', 'missing_key', 'client_error',
    } else 'empty_or_unreliable'
