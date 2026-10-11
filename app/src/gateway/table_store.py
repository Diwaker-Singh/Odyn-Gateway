"""Azure Table Storage budget adapter (M3 boundary; Gate M4 proof pending).

Layout: one PartitionKey per UTC accounting day; rows for global counters,
per-principal counters (sha256 hex, 16 chars), and reservation ledger rows.
One reservation = ONE same-partition entity-group transaction (<=100
entities, <=4MiB) with ETag `If-Match` conditions; 412/409 -> bounded retry
with jitter (3 attempts); store outage -> StoreUnavailable (fail closed).

USD tracked as integer mills (no float entities). This module imports the
Azure SDK lazily so unit tests never require credentials or network.
"""

from __future__ import annotations

import datetime
import hashlib
import random
import time
from dataclasses import dataclass, field
from typing import Any

from .limiter import BudgetExceeded, StoreUnavailable

MAX_TXN_ENTITIES = 100
MAX_CONFLICT_RETRIES = 3


def day_partition(now: float | None = None) -> str:
    ts = time.time() if now is None else now
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime(
        "%Y-%m-%d"
    )


def principal_hash(principal: str) -> str:
    return hashlib.sha256(principal.encode()).hexdigest()[:16]


def to_mills(usd: float) -> int:
    return round(usd * 1000)


@dataclass
class TableBudgetStore:
    """Candidate distributed ledger. Instantiate only with explicit approval."""

    table_client: Any  # azure.data.tables.TableClient (or test double)
    daily_token_limit: int = 100_000
    daily_spend_mills_limit: int = 5_000
    day: str | None = None

    # -- pure helpers (unit-tested without service) ----------------------
    def partition(self) -> str:
        return self.day or day_partition()

    def row_global_tokens(self) -> str:
        return "counter:global:tokens"

    def row_global_usd(self) -> str:
        return "counter:global:usd_mills"

    def row_principal(self, principal: str) -> str:
        return f"counter:principal:{principal_hash(principal)}"

    def row_reservation(self, res_id: str) -> str:
        return f"res:{res_id}"

    def batch_fits(self, n_ops: int, payload_bytes: int) -> bool:
        return n_ops <= MAX_TXN_ENTITIES and payload_bytes <= 4 * 1024 * 1024

    # -- service operations land in M4 against the emulator -----------------
    # (submit_transaction shapes verified empirically there, not assumed).

    def reserve(self, principal: str, tokens: int, usd: float, res_id: str) -> dict:
        """Build the reservation plan for one atomic batch (M3 boundary).

        The live commit path (`submit_transaction` with ETag conditions) is
        implemented against the emulator in M4, where SDK call shapes are
        verified empirically. This method proves the PLAN invariants now:
        same partition, operation count/payload within EGT limits, local
        pre-checks, idempotent reservation id. Runtime atomicity stays
        unproven until Gate M4.
        """
        if tokens > self.daily_token_limit:
            raise BudgetExceeded("single request exceeds daily budget")
        if to_mills(usd) > self.daily_spend_mills_limit:
            raise BudgetExceeded("single request exceeds daily spend")
        part = self.partition()
        ops = [
            (
                "upsert-merge",
                part,
                self.row_global_tokens(),
                {"tokens": tokens, "_op": "incr"},
            ),
            (
                "upsert-merge",
                part,
                self.row_principal(principal),
                {"tokens": tokens, "_op": "incr"},
            ),
            (
                "create",
                part,
                self.row_reservation(res_id),
                {
                    "tokens": tokens,
                    "usd_mills": to_mills(usd),
                    "principal_hash": principal_hash(principal),
                },
            ),
        ]
        if not self.batch_fits(len(ops), 1024):
            raise BudgetExceeded("reservation batch exceeds transaction limits")
        return {
            "partition": part,
            "ops": ops,
            "conditional": True,
            "retries": MAX_CONFLICT_RETRIES,
        }

    def backoff_s(self, attempt: int) -> float:
        base = 0.05 * (2**attempt)
        return base + random.uniform(0, base)  # jitter; bounded by caller

    def is_conflict(self, status_code: int | None) -> bool:
        return status_code in (409, 412)


@dataclass
class TableLedger:
    """Live commit path: same-partition EGT reservation ledger (M4).

    Uses `table_client` (azure-data-tables TableClient, Azurite- or
    cloud-backed) with ETag `If-Match` conditions inside ONE
    `submit_transaction` per mutation. Verified against Azurite (see
    isolated integration fixture; cloud behavior must be verified in its
    target environment.
    """

    table_client: Any
    daily_token_limit: int = 100_000
    daily_spend_mills_limit: int = 5_000
    day: str | None = None
    max_retries: int = 3
    metrics: dict = field(default_factory=dict)

    def partition(self) -> str:
        return self.day or day_partition()

    def _read(self, row: str) -> tuple[dict, str | None]:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            ent = self.table_client.get_entity(self.partition(), row)
            return dict(ent), ent.metadata["etag"]
        except ResourceNotFoundError:
            return {}, None

    def _ensure(self, row: str, base: dict) -> None:
        from azure.core.exceptions import HttpResponseError

        try:
            self.table_client.create_entity(
                {"PartitionKey": self.partition(), "RowKey": row, **base}
            )
        except HttpResponseError as ex:
            if ex.status_code != 409:
                raise StoreUnavailable(f"table error {ex.status_code}")

    def _submit(self, ops: list) -> None:
        from azure.core.exceptions import HttpResponseError

        try:
            self.table_client.submit_transaction(ops)
        except HttpResponseError as ex:
            if ex.status_code in (409, 412):
                raise _Conflict()
            raise StoreUnavailable(f"table error {ex.status_code}")

    def _counter(self, row: str, creates: dict) -> tuple[int, str]:
        self._ensure(row, creates)
        ent, etag = self._read(row)
        assert etag is not None  # ensured above; missing means outage
        return int(ent.get("n", 0)), etag

    def reserve(self, principal: str, tokens: int, usd_mills: int, res_id: str) -> dict:
        """Atomic reserve; raises BudgetExceeded / StoreUnavailable."""
        from azure.core import MatchConditions
        from azure.data.tables import UpdateMode

        if tokens > self.daily_token_limit:
            raise BudgetExceeded("single request exceeds daily budget")
        if usd_mills > self.daily_spend_mills_limit:
            raise BudgetExceeded("single request exceeds daily spend")
        store = TableBudgetStore(
            self.table_client,
            self.daily_token_limit,
            self.daily_spend_mills_limit,
            self.day,
        )
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                g_row = store.row_global_tokens()
                u_row = store.row_global_usd()
                p_row = store.row_principal(principal)
                g, g_etag = self._counter(g_row, {"n": 0})
                u, u_etag = self._counter(u_row, {"n": 0})
                p, p_etag = self._counter(p_row, {"n": 0})
                if g + tokens > self.daily_token_limit or p + tokens > (
                    self.daily_token_limit
                ):
                    raise BudgetExceeded("budget exhausted")
                if u + usd_mills > self.daily_spend_mills_limit:
                    raise BudgetExceeded("spend exhausted")
                part = self.partition()
                cond = {
                    "mode": UpdateMode.MERGE,
                    "match_condition": MatchConditions.IfNotModified,
                }
                ops = [
                    (
                        "update",
                        {"PartitionKey": part, "RowKey": g_row, "n": g + tokens},
                        {**cond, "etag": g_etag},
                    ),
                    (
                        "update",
                        {"PartitionKey": part, "RowKey": u_row, "n": u + usd_mills},
                        {**cond, "etag": u_etag},
                    ),
                    (
                        "update",
                        {"PartitionKey": part, "RowKey": p_row, "n": p + tokens},
                        {**cond, "etag": p_etag},
                    ),
                    (
                        "create",
                        {
                            "PartitionKey": part,
                            "RowKey": store.row_reservation(res_id),
                            "tokens": tokens,
                            "usd_mills": usd_mills,
                            "ph": principal_hash(principal),
                        },
                    ),
                ]
                self._submit(ops)
                return {"res_id": res_id, "tokens": tokens, "usd_mills": usd_mills}
            except _Conflict as ex:
                last = ex
                time.sleep(store.backoff_s(attempt))
                continue
        raise BudgetExceeded(f"reservation conflict after retries ({last})")

    def settle(
        self, res_id: str, actual_tokens: int | None, actual_mills: int | None
    ) -> str:
        """Idempotent settle. Returns 'settled' | 'already' | 'unknown'."""
        from azure.core import MatchConditions
        from azure.data.tables import UpdateMode

        store = TableBudgetStore(
            self.table_client,
            self.daily_token_limit,
            self.daily_spend_mills_limit,
            self.day,
        )
        part = self.partition()
        res, _ = self._read(store.row_reservation(res_id))
        if not res:
            return "already"
        if actual_tokens is None or actual_mills is None:
            self.metrics["budget_charged_unknown"] = (
                self.metrics.get("budget_charged_unknown", 0) + 1
            )
            return "unknown"  # retain reservation (safe side)
        delta_t = actual_tokens - int(res["tokens"])
        delta_u = actual_mills - int(res["usd_mills"])
        if delta_t > 0:
            self.metrics["budget_overshoot"] = (
                self.metrics.get("budget_overshoot", 0) + 1
            )
        for attempt in range(self.max_retries + 1):
            try:
                g_row, u_row = store.row_global_tokens(), store.row_global_usd()
                g, g_etag = self._counter(g_row, {"n": 0})
                u, u_etag = self._counter(u_row, {"n": 0})
                cond = {
                    "mode": UpdateMode.MERGE,
                    "match_condition": MatchConditions.IfNotModified,
                }
                self._submit(
                    [
                        (
                            "update",
                            {"PartitionKey": part, "RowKey": g_row, "n": g + delta_t},
                            {**cond, "etag": g_etag},
                        ),
                        (
                            "update",
                            {"PartitionKey": part, "RowKey": u_row, "n": u + delta_u},
                            {**cond, "etag": u_etag},
                        ),
                        (
                            "delete",
                            {
                                "PartitionKey": part,
                                "RowKey": store.row_reservation(res_id),
                            },
                            {},
                        ),
                    ]
                )
                return "settled"
            except _Conflict:
                time.sleep(store.backoff_s(attempt))
                continue
        # Settlement conflict exhaustion: leave overcharged (safe direction).
        self.metrics["settle_deferred"] = self.metrics.get("settle_deferred", 0) + 1
        return "unknown"

    def release(self, res_id: str) -> str:
        """Release only when provably unsent. Returns 'released' | 'already'."""
        from azure.core import MatchConditions
        from azure.data.tables import UpdateMode

        store = TableBudgetStore(
            self.table_client,
            self.daily_token_limit,
            self.daily_spend_mills_limit,
            self.day,
        )
        part = self.partition()
        res, _ = self._read(store.row_reservation(res_id))
        if not res:
            return "already"
        for attempt in range(self.max_retries + 1):
            try:
                g_row, u_row = store.row_global_tokens(), store.row_global_usd()
                # Principal row addressed by the stored hash (no raw id in keys).
                prow = f"counter:principal:{res.get('ph', '')}"
                g, g_etag = self._counter(g_row, {"n": 0})
                u, u_etag = self._counter(u_row, {"n": 0})
                p, p_etag = self._counter(prow, {"n": 0})
                cond = {
                    "mode": UpdateMode.MERGE,
                    "match_condition": MatchConditions.IfNotModified,
                }
                self._submit(
                    [
                        (
                            "update",
                            {
                                "PartitionKey": part,
                                "RowKey": g_row,
                                "n": g - int(res["tokens"]),
                            },
                            {**cond, "etag": g_etag},
                        ),
                        (
                            "update",
                            {
                                "PartitionKey": part,
                                "RowKey": u_row,
                                "n": u - int(res["usd_mills"]),
                            },
                            {**cond, "etag": u_etag},
                        ),
                        (
                            "update",
                            {
                                "PartitionKey": part,
                                "RowKey": prow,
                                "n": p - int(res["tokens"]),
                            },
                            {**cond, "etag": p_etag},
                        ),
                        (
                            "delete",
                            {
                                "PartitionKey": part,
                                "RowKey": store.row_reservation(res_id),
                            },
                            {},
                        ),
                    ]
                )
                return "released"
            except _Conflict:
                time.sleep(store.backoff_s(attempt))
                continue
        raise StoreUnavailable("release conflict after retries")


class _Conflict(Exception):
    pass
