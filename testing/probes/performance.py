"""Summarize timing events from the latest run; never print conversation events."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path


def read_latest(path):
    run_id = None
    rows = []
    with Path(path).open(encoding="utf-8") as source:
        for line in source:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or row.get("event") != "performance":
                continue
            if not row.get("run_id") or not isinstance(row.get("stage"), str):
                continue
            duration = row.get("duration_ms")
            if not isinstance(duration, (float, int)) or not math.isfinite(duration) or duration < 0:
                continue
            rows.append(row)
            run_id = row["run_id"]
    return run_id, [row for row in rows if row["run_id"] == run_id]


def summarize(path):
    run_id, rows = read_latest(path)
    groups = defaultdict(list)
    for row in rows:
        groups[row["stage"]].append(row)
    result = []
    for stage, measurements in sorted(groups.items()):
        values = [row["duration_ms"] for row in measurements]
        result.append({"stage": stage, "count": len(values),
                       "average_ms": round(sum(values) / len(values), 2), "max_ms": max(values),
                       "not_ok": sum(row.get("status") != "ok" for row in measurements)})
    return run_id, result


def summarize_turns(path):
    run_id, rows = read_latest(path)
    groups = {}
    for row in rows:
        if row.get("turn_id") and row.get("status") == "ok":
            groups.setdefault(row["turn_id"], {}).setdefault(row["stage"], row["duration_ms"])
    result = []
    for turn_id, stages in groups.items():
        def at(name):
            return stages.get("turn.endpoint_to_" + name)

        def delta(end, start):
            a, b = at(end), at(start)
            return round(a - b, 2) if a is not None and b is not None and a >= b else None

        result.append({
            "turn_id": turn_id,
            "origin": "voice" if "turn.origin_voice" in stages else "dispatch",
            "endpoint_estimate_ms": stages.get("turn.endpoint_wait_estimate"),
            "recognition_ms": at("recognition_ready"),
            "input_queue_ms": delta("dispatch", "recognition_ready"),
            "first_text_ms": delta("llm_first_text", "dispatch"),
            "buffer_ms": delta("first_phrase", "llm_first_text"),
            "tts_queue_ms": delta("tts_start", "first_phrase"),
            "tts_submit_ms": delta("tts_submit", "tts_start"),
            "total_estimate_ms": stages.get("turn.speech_end_estimate_to_tts_submit"),
            "file_synthesis_ms": delta("tts_file_synthesis", "tts_start"),
            "file_ready_ms": delta("tts_file_ready", "tts_start"),
            "tts_error": at("tts_error") is not None,
        })
    return run_id, result


def main(args):
    if args.path is None:
        from config import load_settings
        args.path = load_settings().paths.data_dir / "metrics.jsonl"
    try:
        run_id, rows = summarize_turns(args.path) if args.turns else summarize(args.path)
    except OSError as exc:
        print(f"Cannot read timing report: {type(exc).__name__}")
        return 1
    if not rows:
        print("No performance measurements found. Run the application or tester.py --probe startup --allow-live first.")
        return 1
    if args.turns:
        print(f"Run: {run_id}. Software submission is NOT first audible sound; endpoint/total are estimates.")
        print("first_text includes processing/retries; buffer overlaps generation. File columns are NOT playback.")
        columns = ["endpoint_estimate_ms", "recognition_ms", "input_queue_ms", "first_text_ms", "buffer_ms",
                   "tts_queue_ms", "tts_submit_ms", "total_estimate_ms", "file_synthesis_ms", "file_ready_ms"]
        for row in rows:
            print(f"Turn {row['turn_id']} ({row['origin']}; tts_error={row['tts_error']}):")
            print("  " + " | ".join(f"{key}={row[key]:.1f}" if row[key] is not None else f"{key}=-" for key in columns))
        return 0
    print(f"Run: {run_id} (nested/parallel stages must NOT be summed)")
    print(f"{'Stage':48} {'Count':>6} {'Avg ms':>10} {'Max ms':>10} {'Not OK':>7}")
    for row in rows:
        print(f"{row['stage']:48} {row['count']:6} {row['average_ms']:10.1f} {row['max_ms']:10.1f} {row['not_ok']:7}")
    return 0

