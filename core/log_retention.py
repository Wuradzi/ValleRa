"""Retention for logs/sessions and logs/tests that never removes registered evidence.

Session journals and tester runs are also thesis evidence: any path mentioned in
docs/evidence/manifest.json, docs/EVIDENCE_REGISTER.md or docs/DEVELOPMENT_LOG.md
is kept. An unreadable manifest disables pruning (fail closed). Recent entries
are kept by age as well, so a parallel run in the same checkout is never removed.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

SESSION_KEEP = 30
TEST_RUN_KEEP = 50
MIN_AGE_SECONDS = 7 * 24 * 3600
EVIDENCE_TEXTS = ("docs/EVIDENCE_REGISTER.md", "docs/DEVELOPMENT_LOG.md")
# Entry names are unique (timestamp + random suffix); documents sometimes cite
# them without the logs/... prefix, so evidence is matched by name.
_ENTRY_NAME = re.compile(r"session-\d{8}T[\d.]+Z-\d+-[0-9a-f]{8}\.log|\d{8}T\d{6}\.\d{6}Z-[0-9a-f]{8}")


class EvidenceUnreadable(RuntimeError):
    pass


@dataclass
class PruneResult:
    removed: list[str] = field(default_factory=list)
    protected: int = 0
    failed: list[str] = field(default_factory=list)
    skipped_reason: str = ""


def protected_names(root: Path) -> set[str]:
    """Session/test-run names referenced as evidence; raises EvidenceUnreadable."""
    names: set[str] = set()
    manifest = root / "docs" / "evidence" / "manifest.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            for item in data["files"]:
                names.update(_ENTRY_NAME.findall(str(item["path"])))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise EvidenceUnreadable(f"manifest.json: {type(exc).__name__}") from exc
    for name in EVIDENCE_TEXTS:
        path = root / name
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, ValueError) as exc:
            raise EvidenceUnreadable(f"{name}: {type(exc).__name__}") from exc
        names.update(_ENTRY_NAME.findall(text))
    return names


def prune(directory: Path, root: Path, *, keep: int, pattern: str,
          min_age_seconds: float = MIN_AGE_SECONDS, now: float | None = None,
          dry_run: bool = False) -> PruneResult:
    """Remove entries of `directory` matching `pattern` beyond the newest `keep`."""
    result = PruneResult()
    if not directory.is_dir():
        return result
    try:
        protected = protected_names(root)
    except EvidenceUnreadable as exc:
        result.skipped_reason = str(exc)
        logger.warning("Log retention skipped: evidence list unreadable (%s)", exc)
        return result
    now = time.time() if now is None else now
    # Names start with a UTC timestamp, so name order is creation order.
    entries = sorted(directory.glob(pattern), key=lambda path: path.name, reverse=True)
    for entry in entries[keep:]:
        if entry.name in protected:
            result.protected += 1
            continue
        try:
            if now - entry.stat().st_mtime < min_age_seconds:
                continue
            if not dry_run:
                _remove(entry)
            result.removed.append(entry.name)
        except OSError as exc:
            result.failed.append(entry.name)
            logger.warning("Log retention could not remove %s (%s)", entry.name, type(exc).__name__)
    return result


def _remove(entry: Path) -> None:
    if entry.is_dir():
        shutil.rmtree(entry)
    else:
        entry.unlink()


def prune_sessions(root: Path, *, dry_run: bool = False) -> PruneResult:
    return prune(root / "logs" / "sessions", root, keep=SESSION_KEEP, pattern="session-*.log", dry_run=dry_run)


def prune_test_runs(root: Path, *, dry_run: bool = False) -> PruneResult:
    return prune(root / "logs" / "tests", root, keep=TEST_RUN_KEEP, pattern="2*", dry_run=dry_run)


def prune_quietly(prune_function, root: Path) -> PruneResult | None:
    """Retention must never block startup or a test run; failures are only logged."""
    try:
        result = prune_function(root)
    except Exception as exc:  # noqa: BLE001 - housekeeping is best effort by design.
        logger.warning("Log retention failed (%s)", type(exc).__name__)
        return None
    if result.removed or result.failed:
        logger.info("Log retention: removed=%d protected=%d failed=%d",
                    len(result.removed), result.protected, len(result.failed))
    return result
