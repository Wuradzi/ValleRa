"""Bound waiting, not inference. One drain-only job owns the worker pipe.

Late results live only in their job's mailbox, never in a dispatch callback.
No second job may start until the previous transcribe call has returned.
"""
import logging
import threading
import time

from core.models import RecognitionResult

logger = logging.getLogger(__name__)


class RefinementWait:
    def __init__(self):
        self._lock = threading.Lock()
        self._active = None

    def run(self, recognizer, pcm, rate, soft_ms, hard_ms, cancelled):
        started = time.monotonic()
        if cancelled():
            return RecognitionResult('', 0, 'interrupted'), 'cancelled', 0
        with self._lock:
            if self._active is not None and not self._active.is_set():
                return RecognitionResult('', 0, 'whisper'), 'busy', 0
            done = threading.Event()
            self._active = done
            mailbox = []

            def drain():
                try:
                    mailbox.append(recognizer.transcribe(pcm, rate))
                except Exception:
                    logger.exception('Whisper refinement failed')
                finally:
                    done.set()

            threading.Thread(target=drain, name='whisper-refinement-drain', daemon=True).start()
        warned = False
        while True:
            elapsed = (time.monotonic() - started) * 1000
            if cancelled():
                return RecognitionResult('', 0, 'interrupted'), 'cancelled', elapsed
            if elapsed >= soft_ms and not warned:
                logger.warning('Whisper soft budget exceeded: budget_ms=%s', soft_ms)
                warned = True
            if elapsed >= hard_ms:
                return RecognitionResult('', 0, 'whisper'), 'timeout', elapsed
            if done.is_set():
                return (mailbox[0] if mailbox else RecognitionResult('', 0, 'whisper')), 'done', elapsed
            done.wait(min(.025, (hard_ms - elapsed) / 1000))
