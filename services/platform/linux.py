"""Linux execution only; authorization stays in Core. No shell or sudo."""
import logging
import os
from pathlib import Path
import shlex
import shutil
import subprocess

from services.platform.resolver import PlatformOperationError

logger = logging.getLogger(__name__)


def metadata():
    pi = None
    for name in ('/sys/firmware/devicetree/base/model', '/proc/device-tree/model'):
        try:
            with Path(name).open('rb') as stream:
                model = stream.read(256).decode('ascii', errors='replace').strip('\x00\n ')
            pi = model.startswith('Raspberry Pi ')
            break
        except OSError:
            continue
    return dict(raspberry_pi=pi, headless=not bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')),
                desktop_opener=bool(shutil.which('xdg-open')))


def _fail(capability, status, reason):
    # Neither arguments, environment, stderr nor hardware identifiers are logged.
    logger.warning('Linux operation capability=%s status=%s reason=%s', capability, status, reason)
    raise PlatformOperationError(capability, status)


def _executable(name, capability):
    executable = shutil.which(name)
    if not executable:
        status = 'permission_denied' if Path(name).is_file() else 'not_found'
        _fail(capability, status, 'executable_unavailable')
    return executable


def open_application(app):
    capability = 'application_launch'
    try:
        argv = shlex.split(app['command'])
    except (ValueError, TypeError, KeyError):
        _fail(capability, 'execution_failed', 'invalid_configured_command')
    if not argv:
        _fail(capability, 'not_found', 'empty_command')
    argv[0] = _executable(argv[0], capability)
    try:
        process = subprocess.Popen(argv, shell=False, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        if process.poll() not in (None, 0):
            _fail(capability, 'execution_failed', 'early_exit')
    except PermissionError:
        _fail(capability, 'permission_denied', 'os_permission')
    except FileNotFoundError:
        _fail(capability, 'not_found', 'executable_disappeared')
    except OSError as exc:
        _fail(capability, 'execution_failed', type(exc).__name__)
    return True  # Launch accepted, NOT a verified visible application.


def open_file(path):
    capability = 'file_open'
    if metadata()['headless']:
        _fail(capability, 'not_available', 'headless')
    executable = _executable('xdg-open', capability)
    try:
        result = subprocess.run([executable, str(path)], shell=False, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8, check=False)
    except PermissionError:
        _fail(capability, 'permission_denied', 'os_permission')
    except FileNotFoundError:
        _fail(capability, 'not_found', 'opener_disappeared')
    except (OSError, subprocess.TimeoutExpired) as exc:
        _fail(capability, 'execution_failed', type(exc).__name__)
    if result.returncode:
        _fail(capability, 'execution_failed', f'opener_exit_{result.returncode}')
    return True  # Opener accepted; no window verification on Linux yet.
