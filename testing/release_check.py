"""One orchestration entry; existing tester remains the test execution owner."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from core.security import sanitized_environment


def execute(command, root, logfile, interactive=False):
    try:
        if interactive:
            # A redirected prompt would make the live flow impossible to follow.
            return subprocess.run(command, cwd=root, timeout=1800, env=sanitized_environment()).returncode
        with logfile.open('w', encoding='utf-8') as output:
            result = subprocess.run(command, cwd=root, stdout=output, stderr=output,
                stdin=None if interactive else subprocess.DEVNULL, timeout=1800 if interactive else 900,
                env=sanitized_environment({'PYTHONIOENCODING': 'utf-8'}))
        return result.returncode
    except (OSError, subprocess.TimeoutExpired):
        return 1


def run(args, root=None, runner=execute):
    root = Path(root or Path(__file__).resolve().parents[1])
    folder = root / 'logs' / 'release-check' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8])
    folder.mkdir(parents=True)
    python = [sys.executable, '-X', 'utf8']
    tests = python + ['tester.py']
    stages = [
        ('Config', python + ['-c', 'from config import load_settings; load_settings(read_only=True)']),
        ('Imports', python + ['-c', 'import main, core.app, core.stt_listener']),
        ('Critical contracts', tests + ['--module', 'voice', 'runtime', '--verbose']),
        ('Confirmation', tests + ['--all', '--filter', 'confirmation', '--verbose']),
        ('Contextual actions', tests + ['--all', '--filter', 'context', '--verbose']),
        ('STT tests', tests + ['--module', 'stt', 'performance', 'other', '--verbose']),
        ('Full suite', tests + ['--all', '--verbose']),
        ('Ruff', python + ['-m', 'ruff', 'check', '.']),
        ('git diff --check', ['git', '-c', 'core.autocrlf=false', 'diff', '--check']),
    ]
    rows = []
    for index, (name, command) in enumerate(stages):
        code = runner(command, root, folder / f'{index:02d}.log')
        row = dict(name=name, status='PASS' if code == 0 else 'FAIL', exit_code=code, required=True)
        rows.append(row)
        print(f'[{row["status"]}] {name}', flush=True)
    for name, requested in [('Offline STT benchmark', args.with_benchmark), ('GPU validation', args.with_gpu), ('Live smoke', args.with_live)]:
        row = dict(name=name, status='NOT_RUN', required=False)
        if requested:
            if name == 'GPU validation':
                from services.audio.capabilities import cuda_available
                if not cuda_available():
                    row.update(status='NOT_TESTED', reason='CUDA unavailable')
                    rows.append(row)
                    continue
            if name != 'Live smoke' and not args.corpus:
                row.update(status='FAIL', reason='--corpus required')
            else:
                before = set((root / 'logs' / 'stt-benchmark').glob('*/report.json'))
                before_smoke = set((root / 'logs' / 'smoke-live').glob('*/report.json'))
                command = python + ['main.py', '--smoke-live'] if name == 'Live smoke' else tests + [
                    '--probe', 'stt_benchmark', '--allow-live', '--timeout', '800', '--', '--corpus', args.corpus,
                    '--matrix', 'testing/stt_matrix.json']
                if name == 'GPU validation':
                    command += ['--candidates', 'turbo-cuda-fp16', 'turbo-cuda-int8', 'large-cuda-fp16']
                code = runner(command, root, folder / f'optional-{len(rows)}.log', name == 'Live smoke')
                row['status'] = 'PASS' if code == 0 else 'FAIL'
                if name == 'Live smoke':
                    fresh_smoke = set((root / 'logs' / 'smoke-live').glob('*/report.json')) - before_smoke
                    row['status'] = json.loads(fresh_smoke.pop().read_text(encoding='utf-8'))['status'] if len(fresh_smoke) == 1 else 'FAIL'
                if name != 'Live smoke':
                    fresh = set((root / 'logs' / 'stt-benchmark').glob('*/report.json')) - before
                    if len(fresh) == 1:
                        variants = json.loads(fresh.pop().read_text(encoding='utf-8'))['variants']
                        states = {v['status'] for v in variants}
                        row['variants'] = [{'id': v['candidate']['id'], 'status': v['status']} for v in variants]
                        row['status'] = ('FAIL' if states & {'failed', 'timeout'} else
                                         'NOT_TESTED' if states == {'NOT_TESTED'} else
                                         'WARN' if 'NOT_TESTED' in states else 'PASS')
                    else:
                        row.update(status='FAIL', reason='No unique new benchmark report')
        rows.append(row)
    result = dict(status='FAIL' if any(r['status'] == 'FAIL' for r in rows) else 'PASS', stages=rows,
                  warnings=['Automated PASS is not production readiness.', 'Unrequested live/GPU stages are not validated.'])
    (folder / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('ValleRa Release Check')
    for row in rows:
        print(f"{row['name']}: {row['status']}")
    print(f"FINAL: {result['status']}\nReport: {folder / 'report.json'}")
    return int(result['status'] == 'FAIL')
