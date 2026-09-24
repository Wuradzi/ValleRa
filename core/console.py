"""User-facing terminal output, separate from background diagnostics."""
from __future__ import annotations

import getpass
import logging
import sys


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


def console_getpass(prompt):
    from core.logging_setup import register_secret

    value = getpass.getpass(prompt, stream=console_stream(error=True))
    register_secret(value)
    return value
