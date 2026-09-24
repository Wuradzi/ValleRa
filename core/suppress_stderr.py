from __future__ import annotations

import contextlib
import os
import sys


@contextlib.contextmanager
def suppress_native_stderr():
    """Приглушує C-рівень stderr, зокрема повідомлення ALSA/JACK."""
    stderr_fd = sys.stderr.fileno()
    saved_fd = os.dup(stderr_fd)
    try:
        with open(os.devnull, "w", encoding="utf-8") as devnull:
            os.dup2(devnull.fileno(), stderr_fd)
            yield
    finally:
        os.dup2(saved_fd, stderr_fd)
        os.close(saved_fd)
