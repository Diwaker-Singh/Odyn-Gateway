"""Authoritative rate/budget store port + in-memory harness.

Table Storage design (proof pending real endpoint): one PartitionKey per
accounting day; rows `global`, `principal:<hash>`, `res:<id>`; updates via a
same-partition entity-group transaction (<=100 entities, <=4MiB) with ETag
conditional writes; bounded conflict retry with jitter. Hot-partition
trade-off documented (demo scale only).

`MemoryStore` is the local harness: same conditional-write semantics
(expected-etag CAS + single lock standing in for the EGT), so the reserve/
settle/race tests exercise the real code path. Distributed (multi-replica)
atomicity is proven only against the real Table endpoint (pending).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


class StoreUnavailable(Exception):
    pass


class AdmissionDeadlineExceeded(Exception):
    """The request-wide deadline expired during admission or settlement."""


class BudgetExceeded(Exception):
    pass


class Conflict(Exception):
    """ETag/conditional-write conflict: caller retries with jitter, bounded."""


@dataclass
class TokenBucket:
    tokens: float
    updated_at: float
    etag: int = 0


@dataclass
class MemoryStore:
    """Conditional-write harness. `down=True` simulates store outage."""

    down: bool = False
    buckets: dict = field(default_factory=dict)  # key -> TokenBucket
    reservations: dict = field(default_factory=dict)  # res_id -> tokens
    spent_tokens: int = 0
    spent_usd: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def _need(self) -> None:
        if self.down:
            raise StoreUnavailable("budget store unavailable")

    def take_bucket(self, key: str) -> tuple[TokenBucket | None, int]:
        with self.lock:
            self._need()
            b = self.buckets.get(key)
            return (
                None if b is None else TokenBucket(b.tokens, b.updated_at, b.etag),
                -1 if b is None else b.etag,
            )

    def put_bucket(self, key: str, bucket: TokenBucket, expect_etag: int) -> None:
        """CAS write: single EGT-equivalent for one row (multi-row in budget)."""
        with self.lock:
            self._need()
            cur = self.buckets.get(key)
            cur_etag = -1 if cur is None else cur.etag
            if cur_etag != expect_etag:
                raise Conflict(key)
            bucket.etag = cur_etag + 1
            self.buckets[key] = bucket


@dataclass
class RateLimiter:
    """Token bucket: sustained rate + burst capacity (user decision section 2).

    Fixed refill from a fake-clock-testable `now`. APIM coarse throttle is a
    SEPARATE layer with different (tier-dependent) algorithm/accuracy; the two
    are never claimed equivalent. Fixed-window boundary bursts do not apply
    (token bucket), but distributed exactness is only as good as the store CAS.
    """

    rate_per_sec: float
    capacity: int

    def allow(
        self,
        store: MemoryStore,
        key: str,
        now: float,
    ) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds). Consumes 1 token if allowed."""
        bucket, etag = store.take_bucket(key)
        if bucket is None:
            bucket = TokenBucket(float(self.capacity), now)
            etag = -1
        elapsed = max(0.0, now - bucket.updated_at)
        bucket.tokens = min(
            float(self.capacity), bucket.tokens + elapsed * self.rate_per_sec
        )
        bucket.updated_at = now
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            store.put_bucket(key, bucket, etag)
            return True, 0.0
        deficit = 1.0 - bucket.tokens
        return False, deficit / self.rate_per_sec
