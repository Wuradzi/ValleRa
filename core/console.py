"""User-facing terminal output, separate from background diagnostics."""
from __future__ import annotations

import getpass
import logging
import sys
import os
import time


def console_stream(*, error=False):
    stream = sys.stderr if error else sys.stdout
    return getattr(stream, "console", stream)


def console_print(*values, sep=" ", end="\n", flush=False):
    print(*values, sep=sep, end=end, flush=flush, file=console_stream())
    if values:
        logging.getLogger("session.dialogue").info(sep.join(str(value) for value in values))


def console_input(prompt="", *, log_response=True):
    console_print(prompt, end="", flush=True)
    value = input()
    if log_response:
        logging.getLogger("session.dialogue").info("[TEXT] %s", value)
    return value


def correlated_console_input(prompt, snapshot):
    """Bind a Windows console line when typing starts, not when Enter arrives.

    Non-console stdin cannot expose keystrokes: bind before its blocking read,
    failing closed if confirmation changes while that read is in progress.
    """
    if os.name != "nt" or not sys.stdin.isatty():
        request_id = snapshot()
        return console_input(prompt), request_id
    import msvcrt
    console_print(prompt, end="", flush=True)
    # Peek only: input() still owns Windows line editing, echo and paste.
    while not msvcrt.kbhit():
        time.sleep(.01)
    request_id = snapshot()
    return console_input(""), request_id


def console_getpass(prompt):
    from core.logging_setup import register_secret

    value = getpass.getpass(prompt, stream=console_stream(error=True))
    register_secret(value)
    return value
