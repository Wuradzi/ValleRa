from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from core.security import sanitized_environment
from core.console import console_print as print


@dataclass(slots=True)
class CodeExecutionResult:
    return_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class CodeExecutor:
    """Фаза 2. Повний доступ, але модуль вимкнено за замовчуванням."""

    def __init__(self, enabled: bool, timeout_seconds: int = 30, output_limit: int = 20000):
        self.enabled = enabled
        self.timeout_seconds = timeout_seconds
        self.output_limit = output_limit

    async def execute_python(self, code: str, description: str, confirm) -> CodeExecutionResult:
        if not self.enabled:
            raise PermissionError("Динамічне виконання коду вимкнено у config.json.")

        print("\n=== ОПИС ===")
        print(description)
        print("\n=== КОД ===")
        print(code)
        print("=== КІНЕЦЬ КОДУ ===\n")

        if not await confirm("виконання показаного Python-коду"):
            return CodeExecutionResult(1, "", "Операцію скасовано.")

        return await asyncio.to_thread(self._run, code)

    def _run(self, code: str) -> CodeExecutionResult:
        with tempfile.TemporaryDirectory(prefix="valera_code_") as temp_dir:
            path = Path(temp_dir) / "generated.py"
            path.write_text(code, encoding="utf-8")
            try:
                completed = subprocess.run(
                    [sys.executable, str(path)],
                    cwd=temp_dir,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                    env=sanitized_environment(),
                )
                return CodeExecutionResult(
                    completed.returncode,
                    completed.stdout[:self.output_limit],
                    completed.stderr[:self.output_limit],
                )
            except subprocess.TimeoutExpired as exc:
                return CodeExecutionResult(
                    -1,
                    (exc.stdout or "")[:self.output_limit],
                    (exc.stderr or "")[:self.output_limit],
                    True,
                )
