#!/usr/bin/env python3
"""Record sanitized soak snapshots and evaluate the v0.5 release gate."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

ALIASES = {
    "codex-deriver",
    "codex-summary",
    "codex-dialectic",
    "codex-dream-deduction",
    "codex-dream-induction",
}


def evaluate(state: dict[str, Any]) -> dict[str, Any]:
    snapshots = state.get("snapshots", [])
    if not snapshots:
        return {"ready": False, "reasons": ["no snapshots"]}
    first, latest = snapshots[0], snapshots[-1]
    duration = int(latest["timestamp"] - first["timestamp"])
    aliases = latest.get("alias_counts", {})
    checks = {
        "seven_days": duration >= 7 * 24 * 60 * 60,
        "successful_completions": latest.get("successful_completions", 0) >= 250,
        "dream_cycles": latest.get("dream_cycles", 0) >= 4,
        "all_aliases": all(aliases.get(alias, 0) > 0 for alias in ALIASES),
        "no_failed_jobs": latest.get("honcho_failed_jobs", 0) == 0,
        "no_stranded_jobs": latest.get("honcho_stranded_jobs", 0) == 0,
        "no_unhandled_5xx": latest.get("lariat_unhandled_5xx", 0) == 0,
        "no_oom": latest.get("oom_kills", 0) == 0,
        "queue_under_ten_minutes": latest.get("max_queue_age_seconds", 601) <= 600,
        "stable_memory": latest.get("stable_memory") is True,
        "idle_kill_drill": latest.get("idle_kill_drill") is True,
        "active_kill_drill": latest.get("active_kill_drill") is True,
    }
    return {
        "ready": all(checks.values()),
        "soak_seconds": duration,
        "checks": checks,
        "reasons": [name for name, passed in checks.items() if not passed],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    state = json.loads(args.state.read_text()) if args.state.exists() else {"snapshots": []}
    if args.snapshot:
        snapshot = json.loads(args.snapshot.read_text())
        snapshot.setdefault("timestamp", int(time.time()))
        state.setdefault("snapshots", []).append(snapshot)
        args.state.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    result = evaluate(state)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
