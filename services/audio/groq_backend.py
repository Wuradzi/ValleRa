"""Optional completed-utterance cloud STT. No dispatch or confidence invention."""
import io
import logging
import re
import threading
import time
import wave

import httpx

from core.models import RecognitionResult
from services.audio.backends import BackendMetadata

logger = logging.getLogger(__name__)
MODELS = ('whisper-large-v3-turbo', 'whisper-large-v3')
ENDPOINT = 'https://api.groq.com/openai/v1/audio/transcriptions'


def plausible_ukrainian(text):
    """Conservative script guard, not a language classifier or text repair.

    Preserve arbitrary Latin names in Ukrainian-script utterances.
    Latin-only scoped names are checked separately against a current catalogue.
    Shared Cyrillic vocabulary cannot reliably identify a language.
    """
    remainder = text
    letters = [c for c in remainder.casefold() if c.isalpha()]
    if not letters:
        return bool(text.strip())
    ukrainian = sum(c in 'абвгґдеєжзиіїйклмнопрстуфхцчшщьюя' for c in letters)
    if any(c in 'ыэёъ' for c in letters):
        return False
    if ukrainian / len(letters) >= .7:
        return True
    # A whole Ukrainian-script word anchors a mixed utterance, regardless of
    # the length of product/file/technical names. Mixed-script words do not count.
    words = re.findall(r"[^\W\d_]+", remainder.casefold())
    uk_words = sum(bool(re.fullmatch('[а-щьюяґєії]{2,}', word)) for word in words)
    return (uk_words >= 1
            and all(c in 'абвгґдеєжзиіїйклмнопрстуфхцчшщьюяabcdefghijklmnopqrstuvwxyz' for c in letters))


class GroqBackend:
    def __init__(self, settings, profile):
        self.metadata = BackendMetadata('groq', profile.model, device='cloud', compute_type='remote')
        # Match LLM providers: capture after vault unlock, before Core scrubs env.
        self._key = settings.api_keys.get('groq', '')
        self._timeout = settings.stt_groq_timeout_seconds
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self.last_error = ''
        self.last_metrics = {}

    def prepare(self):
        # Presence check only: no request or local model load.
        if self._closed.is_set():
            return False, 'closed'
        if self.metadata.model not in MODELS:
            return False, 'invalid_model'
        if not self._key:
            return False, 'MISSING_KEY'
        return True, 'CONFIGURED; LIVE_NOT_TESTED; confidence unavailable'

    def transcribe(self, pcm, sample_rate, *, name_reply_allowed=None):
        with self._lock:
            started = time.perf_counter()
            api_ms = None
            status = None
            text = ''
            error = ''
            duration = len(pcm) / 2 / sample_rate if sample_rate > 0 else 0
            try:
                ok, detail = self.prepare()
                if not ok:
                    error = 'missing_key' if detail == 'MISSING_KEY' else detail
                elif not pcm or len(pcm) % 2 or not 8000 <= sample_rate <= 192000 or len(pcm) > 24_000_000:
                    error = 'invalid_audio'
                else:
                    wav = io.BytesIO()
                    with wave.open(wav, 'wb') as output:
                        output.setnchannels(1)
                        output.setsampwidth(2)
                        output.setframerate(sample_rate)
                        output.writeframes(pcm)
                    logger.info('stt.groq.request_start provider=groq model=%s sample_rate=%s audio_seconds=%.3f',
                                self.metadata.model, sample_rate, duration)
                    api_started = time.perf_counter()
                    try:
                        # Request-owned client: close() cannot race an in-flight upload.
                        # No retries, redirects or SDK dependency. HTTP errors are never dumped.
                        with httpx.Client(timeout=self._timeout, follow_redirects=False) as client:
                            response = client.post(ENDPOINT,
                                headers={'Authorization': 'Bearer ' + self._key},
                                data={'model': self.metadata.model, 'language': 'uk',
                                      'temperature': '0', 'response_format': 'json'},
                                files={'file': ('utterance.wav', wav.getvalue(), 'audio/wav')})
                        status = response.status_code
                        if status != 200:
                            error = ('authorization' if status in (401, 403) else 'rate_limit' if status == 429
                                     else 'server' if status >= 500 else 'http_error')
                        else:
                            body = response.json()
                            if not isinstance(body, dict) or not isinstance(body.get('text'), str):
                                error = 'malformed_response'
                            else:
                                text = body['text'].strip()
                                if not text:
                                    error = 'empty_transcription'
                    finally:
                        api_ms = (time.perf_counter() - api_started) * 1000
            except httpx.TimeoutException:
                error = 'timeout'
            except httpx.RequestError:
                error = 'network'
            except (ValueError, TypeError):
                error = 'malformed_response'
            except Exception:
                error = 'client_error'
            if self._closed.is_set():
                error = 'cancelled'
            contextual_name = False
            if text and not error and not plausible_ukrainian(text) and callable(name_reply_allowed):
                # Only a request-local, exact catalogue/context check can exempt
                # a Latin-only name. No generic English phrase exception.
                if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._+()\-]{0,99}[.!?]?", text):
                    try:
                        contextual_name = name_reply_allowed(text) is True
                    except Exception:
                        logger.info('stt.language_guard scope_check=unavailable')
            if text and not plausible_ukrainian(text) and not contextual_name:
                error = 'non_ukrainian_transcript'
                logger.info('stt.language_guard decision=reject chars=%s words=%s foreign_cyrillic=%s',
                            len(text), len(text.split()), any(c in text.casefold() for c in 'ыэёъ'))
            elif contextual_name:
                logger.info('stt.language_guard decision=accept reason=scoped_catalogue_name')
            if error:
                text = ''
            self.last_error = error
            elapsed = (time.perf_counter() - started) * 1000
            self.last_metrics = dict(provider='groq', model=self.metadata.model, input_sample_rate=sample_rate,
                audio_seconds=duration, api_roundtrip_ms=api_ms, transcription_ms=elapsed,
                realtime_factor=elapsed / 1000 / duration if duration else None,
                success=not bool(error), error_class=error or None, http_status=status,
                confidence_available=False, resource_scope='client_only')
            logger.info('stt.groq.complete provider=groq model=%s api_roundtrip_ms=%s transcription_ms=%.1f '
                        'rtf=%s success=%s error_class=%s http_status=%s confidence_available=false',
                        self.metadata.model, api_ms, elapsed, self.last_metrics['realtime_factor'],
                        not bool(error), error or 'none', status)
            # Numeric zero is only a compatibility placeholder, not measured confidence.
            # Successful unscored text can be CHAT, never ACTION eligible.
            return RecognitionResult(text, 0.0, 'groq', fragmented=True, incomplete=bool(error),
                                     recognition_unreliable=bool(error), confidence_available=False)

    def close(self):
        # In-flight HTTP finishes under its timeout; late text is discarded.
        self._closed.set()
