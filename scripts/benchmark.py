#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import urllib.error
import urllib.request

PAYLOAD = {
    "model": "codex-deriver",
    "messages": [
        {
            "role": "system",
            "content": (
                "Return one JSON object with an observations array. Each observation "
                "must be a concise fact explicitly supported by the user message."
            ),
        },
        {"role": "user", "content": "The user prefers tea over coffee."},
    ],
    "response_format": {"type": "json_object"},
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark the local Codex bridge")
    parser.add_argument("--url", default="http://127.0.0.1:5055")
    parser.add_argument("--requests", type=int, default=3)
    args = parser.parse_args()
    if args.requests <= 0:
        parser.error("--requests must be positive")

    api_key = os.getenv("CODEX_BRIDGE_API_KEY", "")
    if not api_key:
        print("CODEX_BRIDGE_API_KEY is required", file=sys.stderr)
        return 2

    rows: list[dict[str, int]] = []
    for number in range(1, args.requests + 1):
        request = urllib.request.Request(
            f"{args.url.rstrip('/')}/v1/chat/completions",
            data=json.dumps(PAYLOAD).encode(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=240) as response:
                body = json.load(response)
        except urllib.error.HTTPError as exc:
            print(exc.read().decode(errors="replace"), file=sys.stderr)
            return 1

        usage = body["bridge"]["raw_usage"]
        row = {
            "input": int(usage.get("input_tokens", 0)),
            "cached": int(usage.get("cached_input_tokens", 0)),
            "uncached": int(usage.get("input_tokens", 0))
            - int(usage.get("cached_input_tokens", 0)),
            "output": int(usage.get("output_tokens", 0)),
            "reasoning": int(usage.get("reasoning_output_tokens", 0)),
            "duration_ms": int(body["bridge"]["duration_ms"]),
        }
        rows.append(row)
        print(f"request {number}: {json.dumps(row, sort_keys=True)}")

    print(
        "summary:",
        json.dumps(
            {
                "requests": len(rows),
                "median_input": int(statistics.median(row["input"] for row in rows)),
                "median_cached": int(statistics.median(row["cached"] for row in rows)),
                "median_uncached": int(statistics.median(row["uncached"] for row in rows)),
                "median_output": int(statistics.median(row["output"] for row in rows)),
                "median_duration_ms": int(statistics.median(row["duration_ms"] for row in rows)),
            },
            sort_keys=True,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
