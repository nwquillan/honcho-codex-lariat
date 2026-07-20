from __future__ import annotations

import json
import logging
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": int(time.time()),
            "level": record.levelname.lower(),
            "event": getattr(record, "event", "log"),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        elif record.getMessage():
            payload["message"] = record.getMessage()
        if record.exc_info:
            exception_type = record.exc_info[0]
            if exception_type is not None:
                payload["exception"] = exception_type.__name__
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def configure_logging(level: str = "info") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    logger.info("", extra={"event": event, "fields": fields})


@dataclass
class Metrics:
    counters: Counter[str] = field(default_factory=Counter)
    by_alias: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    sums: Counter[str] = field(default_factory=Counter)
    gauges: dict[str, float] = field(default_factory=dict)

    def request_started(self, alias: str) -> None:
        self.counters["requests_total"] += 1
        self.by_alias[alias]["requests_total"] += 1
        self.gauges["requests_active"] = self.gauges.get("requests_active", 0) + 1

    def request_finished(
        self,
        alias: str,
        *,
        status: str,
        duration_ms: int,
        queue_wait_ms: int = 0,
        usage: dict[str, int] | None = None,
    ) -> None:
        self.gauges["requests_active"] = max(0, self.gauges.get("requests_active", 0) - 1)
        self.counters[f"responses_{status}_total"] += 1
        self.by_alias[alias][f"responses_{status}_total"] += 1
        self.sums["request_duration_ms_total"] += duration_ms
        self.sums["queue_wait_ms_total"] += queue_wait_ms
        for name, value in (usage or {}).items():
            if name in {
                "input_tokens",
                "cached_input_tokens",
                "output_tokens",
                "reasoning_output_tokens",
            }:
                self.sums[f"{name}_total"] += int(value)
                self.by_alias[alias][f"{name}_total"] += int(value)

    def set_runner(self, status: dict[str, Any]) -> None:
        self.gauges["runner_ready"] = 1 if status.get("ready") else 0
        self.gauges["queue_waiting"] = float(status.get("waiting", 0))
        self.gauges["child_restarts_total"] = float(status.get("restart_count", 0))
        self.gauges["child_recycles_total"] = float(status.get("recycle_count", 0))

    def render_prometheus(self) -> str:
        lines = [
            "# HELP lariat_info Lariat build information.",
            "# TYPE lariat_info gauge",
            'lariat_info{version="0.5.0-rc.1"} 1',
        ]
        for name, value in sorted(self.counters.items()):
            metric = f"lariat_{name}"
            lines.extend((f"# TYPE {metric} counter", f"{metric} {value}"))
        for name, value in sorted(self.sums.items()):
            metric = f"lariat_{name}"
            lines.extend((f"# TYPE {metric} counter", f"{metric} {value}"))
        for name, gauge_value in sorted(self.gauges.items()):
            metric = f"lariat_{name}"
            lines.extend((f"# TYPE {metric} gauge", f"{metric} {gauge_value}"))
        for alias, counters in sorted(self.by_alias.items()):
            escaped = alias.replace('"', '\\"')
            for name, value in sorted(counters.items()):
                metric = f"lariat_workload_{name}"
                lines.append(f'{metric}{{alias="{escaped}"}} {value}')
        return "\n".join(lines) + "\n"
