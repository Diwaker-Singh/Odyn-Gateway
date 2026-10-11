"""Ledger backend port: the contract any authoritative store must satisfy.

An implementation MUST provide atomic multi-row updates for one reservation
(global counters + principal counters + reservation row) with optimistic
concurrency (conflict -> caller retries, bounded, with jitter), idempotent
settlement, and fail-closed outage behavior. `MemoryStore` (limiter.py) is
the LOCAL/TEST-ONLY implementation. `TableBudgetStore` (table_store.py) is
the Azure Table candidate — distributed claims pending Gate M4 runtime proof.
"""

from __future__ import annotations

from typing import Protocol


class LedgerBackend(Protocol):
    def reserve(self, principal: str, tokens: int, usd: float): ...

    def settle(
        self, res, actual_tokens: int | None, actual_usd: float | None, metrics: dict
    ) -> None: ...

    def release(self, res) -> None: ...
