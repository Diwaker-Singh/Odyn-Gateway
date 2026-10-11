"""Deterministic no-network provider fixture.

The runtime defaults to this stub; separately approved live dispatch uses
provider.OpenRouter. Fault injection exercises safe retries and accounting.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StubResult:
    kind: str  # ok | presend_fail | timeout | reset_after_send | http
    status: int = 200
    prompt_tokens: int | None = 12
    completion_tokens: int | None = 8
    content: str = "Stub response."
    # Actual provider HTTP status differs from the gateway's mapped status when
    # a successful HTTP response contains an unusable inference payload.
    provider_http_status: int | None = None
    failure_reason: str | None = None


@dataclass
class StubUpstream:
    mode: str = "ok"  # test-controlled fault selector
    calls: int = 0

    def close(self) -> None:
        pass

    def send(
        self, model_id: str, messages: tuple, max_tokens: int, request_id: str
    ) -> StubResult:
        self.calls += 1
        if self.mode == "ok":
            return StubResult("ok")
        if self.mode == "ok_no_usage":
            return StubResult("ok", prompt_tokens=None, completion_tokens=None)
        if self.mode == "presend_refused":
            return StubResult("presend_fail")
        if self.mode == "timeout":
            return StubResult("timeout")
        if self.mode == "reset_after_send":
            return StubResult("reset_after_send")
        if self.mode == "http_429":
            return StubResult("http", status=429)
        if self.mode == "http_500":
            return StubResult("http", status=500)
        if self.mode == "http_401":
            return StubResult("http", status=401)
        if self.mode == "http_402":
            return StubResult("http", status=402)
        raise ValueError(f"unknown stub mode {self.mode}")
