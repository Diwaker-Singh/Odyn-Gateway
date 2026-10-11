"""Resilience: pre-send-only retry, per-instance breaker, Azure-derived deadlines.

Retry (user decision section 2): at most ONE retry (2 attempts total) and ONLY
for proven pre-send connection failures (DNS, refused, connect-timeout, TLS
handshake before bytes). NEVER after bytes sent, any response, read timeout,
reset-after-send, or uncertain cancellation.

Breaker: per-instance (fake clock in tests), NOT distributed. Counts connection
failures, resets, timeouts, upstream 5xx, upstream 429. Does NOT count 401/402
(surfaced as credential/billing faults + separate alert) nor caller 4xx.

Deadlines: app deadline nested inside platform timeouts. Numeric platform
values must be re-derived from observed APIM + Container Apps behavior
(Central India) — AWS timings are NOT carried over. Defaults below are
placeholders pending that observation.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

CONNECT_TIMEOUT_S = 2.0
APP_DEADLINE_S = 15.0  # PENDING: verify < APIM + Container Apps observed timeouts
MAX_ATTEMPTS = 2  # initial + at most one pre-send retry


@dataclass
class Breaker:
    failures: int = 0
    threshold: int = 5
    open_until: float = 0.0
    cool_down_s: float = 30.0
    state_changes: list = field(default_factory=list)

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _probe_owner: int | None = field(default=None, repr=False)

    def is_open(self, now: float) -> bool:
        with self._lock:
            if now < self.open_until:
                return True
            if self.failures >= self.threshold:
                if self._probe_owner is not None:
                    return True
                self._probe_owner = threading.get_ident()
            return False

    def finish_probe(self) -> None:
        """Release only this worker's probe when admission or dispatch ends."""
        with self._lock:
            if self._probe_owner == threading.get_ident():
                self._probe_owner = None

    def success(self, now: float) -> None:
        with self._lock:
            if now >= self.open_until and self._probe_owner in (
                None,
                threading.get_ident(),
            ):
                self.failures = 0
                self.open_until = 0.0
                self._probe_owner = None

    def record(self, kind: str, now: float) -> None:
        """kind: conn_fail|reset|timeout|upstream_5xx|upstream_429 counts;
        upstream_401|upstream_402|caller_4xx never count."""
        if kind in ("conn_fail", "reset", "timeout", "upstream_5xx", "upstream_429"):
            with self._lock:
                self.failures += 1
                if self.failures >= self.threshold:
                    self.open_until = now + self.cool_down_s
                    self.state_changes.append((now, "open"))
                    self.state_changes[:] = self.state_changes[-256:]
        # 401/402 + caller errors intentionally ignored (separate handling).


def is_presend_retryable(kind: str) -> bool:
    return kind in ("dns", "refused", "connect_timeout", "tls_handshake")
