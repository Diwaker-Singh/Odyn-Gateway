"""Spend reservation ledger: reserve-before-dispatch, settle-at-most-once.

Invariants (refined plan section 5.1):
1. Reserve a bounded estimate BEFORE dispatch (tokens + spend).
2. Release ONLY if provably unsent (local reject / pre-send connect failure).
3. Ambiguous outcomes (timeout/reset/cancel/5xx-after-send/missing usage)
   RETAIN the reservation + emit `budget_charged_unknown`; never assume zero.
4. Success with usage settles to actual; overshoot records actual + metric.
5. Store outage before dispatch -> fail closed (503), never unaccounted send.
6. Each reservation settles exactly once (idempotent reservation id).

Estimate (user decision section 2): NO bytes/4 claim. Interim bound =
UTF-8 byte length of concatenated prompt text (1 byte ~= 1 token upper bound
for text; binary rejected by schema) + framing margin + FULL max_tokens
ceiling. Labelled interim bound, not tokenization; a model-compatible
tokenizer + versioned price table replaces it when available.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable
from dataclasses import dataclass

from .config import GatewayConfig
from .limiter import (
    AdmissionDeadlineExceeded,
    BudgetExceeded,
    MemoryStore,
    StoreUnavailable,
)

FRAMING_MARGIN_TOKENS = 16
_id_seq = itertools.count(1)


@dataclass
class Reservation:
    res_id: str
    principal: str
    tokens: int
    usd: float
    settled: bool = False


def estimate_tokens(messages: tuple, max_tokens: int) -> int:
    prompt_bytes = sum(len(str(m.get("content", "")).encode("utf-8")) for m in messages)
    return prompt_bytes + FRAMING_MARGIN_TOKENS * (len(messages) + 1) + max_tokens


def estimate_usd(
    model_input_rate: float, model_output_rate: float, prompt_text: str, max_tokens: int
) -> float:
    # Spend enforced alongside tokens: reserve input at input rate + FULL
    # output ceiling at output rate (worst case), per-1k pricing.
    prompt_est = len(prompt_text.encode("utf-8")) + FRAMING_MARGIN_TOKENS
    return (
        prompt_est / 1000.0 * model_input_rate + max_tokens / 1000.0 * model_output_rate
    )


@dataclass
class BudgetLedger:
    store: MemoryStore
    config: GatewayConfig
    day: str = "2026-10-09"  # PartitionKey per accounting day (Table design)
    deadline_clock: Callable[[], float] = time.monotonic

    def _keys(self, principal: str) -> tuple[str, str, str]:
        return (
            f"{self.day}:spent",
            f"{self.day}:principal:{principal}",
            f"{self.day}:spent_usd_x1000",
        )

    def reserve(
        self,
        principal: str,
        tokens: int,
        usd: float,
        *,
        deadline: float | None = None,
    ) -> Reservation:
        """Atomic multi-row update (single EGT on Table). Raises on breach/outage."""
        self._acquire(deadline)
        try:
            if self.store.down:
                raise StoreUnavailable("budget store unavailable")
            if tokens > self.config.daily_token_limit:
                raise BudgetExceeded("single request exceeds daily budget")
            if (
                self.store.spent_tokens + tokens > self.config.daily_token_limit
                or self.store.spent_usd + usd > self.config.daily_spend_usd_limit
            ):
                raise BudgetExceeded("budget exhausted")
            res_id = f"res-{next(_id_seq)}"
            self.store.reservations[res_id] = tokens
            self.store.spent_tokens += tokens
            # spend tracked in float usd; Table rows would carry integer mills.
            self.store.spent_usd += usd
            return Reservation(res_id, principal, tokens, usd)
        finally:
            self.store.lock.release()

    def _acquire(self, deadline: float | None) -> None:
        if deadline is None:
            self.store.lock.acquire()
            return
        remaining = deadline - self.deadline_clock()
        if remaining <= 0 or not self.store.lock.acquire(timeout=remaining):
            raise AdmissionDeadlineExceeded()

    def settle(
        self,
        res: Reservation,
        actual_tokens: int | None,
        actual_usd: float | None,
        metrics: dict,
        *,
        deadline: float | None = None,
    ) -> None:
        """Exactly-once. Unknown actuals retain the reservation (safe side)."""
        if res.settled:
            return
        self._acquire(deadline)
        try:
            if self.store.down:
                raise StoreUnavailable("budget store unavailable")
            if res.settled:
                return
            res.settled = True
            if actual_tokens is None or actual_usd is None:
                metrics["budget_charged_unknown"] = (
                    metrics.get("budget_charged_unknown", 0) + 1
                )
                return  # retain full reservation
            delta = actual_tokens - res.tokens
            self.store.spent_tokens += delta
            self.store.spent_usd += actual_usd - res.usd
            self.store.reservations.pop(res.res_id, None)
            if delta > 0:
                metrics["budget_overshoot"] = metrics.get("budget_overshoot", 0) + 1
        finally:
            self.store.lock.release()

    def release(self, res: Reservation, *, deadline: float | None = None) -> None:
        """Only when provably unsent. Idempotent."""
        if res.settled:
            return
        self._acquire(deadline)
        try:
            if self.store.down:
                raise StoreUnavailable("budget store unavailable")
            if res.settled:
                return
            res.settled = True
            self.store.spent_tokens -= res.tokens
            self.store.spent_usd -= res.usd
            self.store.reservations.pop(res.res_id, None)
        finally:
            self.store.lock.release()
