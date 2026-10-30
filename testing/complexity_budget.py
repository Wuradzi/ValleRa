"""Complexity ratchet over ruff C901 (mccabe), audit 2026-10-29 #3.

A function above the threshold in pyproject.toml ([tool.ruff.lint.mccabe])
must be listed in testing/complexity_baseline.json, and its complexity may not
grow above the recorded value. New functions must stay within the threshold.

    python -m testing.complexity_budget            # report
    python -m testing.complexity_budget --update   # rewrite baseline (review the diff!)

--update never hides growth silently: the diff of the baseline file is the review.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path(__file__).with_name("complexity_baseline.json")
CODE_DIRS = ("core", "services", "skills", "native_ui", "testing", "tools", "tests")
_COMPLEXITY = re.compile(r"\((\d+) > \d+\)")


class RuffUnavailable(RuntimeError):
    pass


def threshold(root: Path = ROOT) -> int:
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return int(config["tool"]["ruff"]["lint"]["mccabe"]["max-complexity"])


def code_paths(root: Path = ROOT) -> list[str]:
    """Application code only; private research scripts under docs/ are excluded."""
    paths = [name for name in CODE_DIRS if (root / name).is_dir()]
    paths += sorted(path.name for path in root.glob("*.py"))
    return paths


def _qualnames(path: Path) -> dict[int, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: dict[int, str] = {}

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualname = f"{prefix}{child.name}"
                if not isinstance(child, ast.ClassDef):
                    names[child.lineno] = qualname
                visit(child, qualname + ".")
            else:
                visit(child, prefix)

    visit(tree, "")
    return names


def measure(root: Path = ROOT) -> dict[str, int]:
    command = [sys.executable, "-m", "ruff", "check", "--select", "C901", "--output-format",
               "json", "--exit-zero", "--no-cache", *code_paths(root)]
    try:
        completed = subprocess.run(command, cwd=root, capture_output=True, text=True,
                                   encoding="utf-8", check=False)
    except OSError as exc:
        raise RuffUnavailable(str(exc)) from exc
    if completed.returncode != 0 or not completed.stdout.strip().startswith("["):
        raise RuffUnavailable((completed.stderr or completed.stdout).strip()[:300])
    measured: dict[str, int] = {}
    qualnames: dict[Path, dict[int, str]] = {}
    for item in json.loads(completed.stdout):
        path = Path(item["filename"])
        relative = path.resolve().relative_to(root.resolve()).as_posix()
        names = qualnames.setdefault(path, _qualnames(path))
        row = item["location"]["row"]
        qualname = names.get(row, f"<line {row}>")
        key = f"{relative}::{qualname}"
        suffix = 2
        while key in measured:  # same qualname twice in one file (e.g. redefinition)
            key = f"{relative}::{qualname}#{suffix}"
            suffix += 1
        match = _COMPLEXITY.search(item["message"])
        if not match:
            raise RuffUnavailable(f"Unexpected C901 message: {item['message']}")
        measured[key] = int(match.group(1))
    return dict(sorted(measured.items()))


def load_baseline(path: Path = BASELINE) -> dict[str, int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(key): int(value) for key, value in data["functions"].items()}


def violations(measured: dict[str, int], baseline: dict[str, int], limit: int) -> list[str]:
    problems = []
    for key, value in measured.items():
        if key not in baseline:
            problems.append(f"{key}: нова функція зі складністю {value} > {limit}")
        elif value > baseline[key]:
            problems.append(f"{key}: складність зросла {baseline[key]} → {value}")
    return problems


def improvements(measured: dict[str, int], baseline: dict[str, int]) -> list[str]:
    """Entries that can be tightened in the baseline (informational)."""
    return [f"{key}: {value} → {measured.get(key, 'в межах порогу')}"
            for key, value in baseline.items() if measured.get(key, 0) < value]


def write_baseline(measured: dict[str, int], limit: int, path: Path = BASELINE) -> None:
    payload = {
        "description": "C901 ratchet: listed functions may not grow; others must stay "
                       f"<= {limit}. Regenerate with python -m testing.complexity_budget --update.",
        "threshold": limit,
        "functions": measured,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m testing.complexity_budget")
    parser.add_argument("--update", action="store_true", help="rewrite the baseline file")
    args = parser.parse_args(argv)
    limit = threshold()
    measured = measure()
    if args.update:
        write_baseline(measured, limit)
        print(f"Базову лінію записано: {len(measured)} функцій > {limit}. Перегляньте diff.")
        return 0
    problems = violations(measured, load_baseline(), limit)
    for line in problems:
        print("ПОРУШЕННЯ:", line)
    for line in improvements(measured, load_baseline()):
        print("Можна посилити:", line)
    print(f"Поріг {limit}; понад поріг {len(measured)} функцій; порушень {len(problems)}.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
