"""Redacted telemetry: allowlisted fields only.

Logged/recorded: request_id, route, normalized principal (never token),
status, latency, model alias, retry count, breaker state, usage counts,
cost estimate. NEVER: prompts, completions, tokens, keys, auth headers,
sensitive bodies. A sentinel-string test proves prompts never reach logs.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field


@dataclass
class Telemetry:
    events: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def log(self, **fields: object) -> None:
        allow = {
            "request_id",
            "route",
            "replica_name",
            "principal",
            "status",
            "latency_ms",
            "model",
            "retries",
            "breaker",
            "prompt_tokens",
            "completion_tokens",
            "usd_estimate",
            "provider_http_status",
            "failure_reason",
        }
        self.events.append({k: v for k, v in fields.items() if k in allow})
        if len(self.events) > 256:
            del self.events[:-256]

    def span(self, name: str, headers: dict | None = None):
        return contextlib.nullcontext()

    def close(self) -> None:
        pass

    def record_response(self, status: int, duration: float) -> None:
        pass

    def record_upstream(
        self, kind: str, status: int, duration: float, model: str
    ) -> None:
        pass

    def record_usage(self, prompt: int, completion: int, model: str) -> None:
        pass

    def count(self, name: str) -> None:
        self.metrics[name] = self.metrics.get(name, 0) + 1

    def dump_last(self) -> str:
        return json.dumps(self.events[-1] if self.events else {})
