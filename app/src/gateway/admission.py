"""Atomic global/principal rate and budget admission in a single Table EGT.

Continuous rate rows and dated budget rows share one stable Table partition.
Settlement references the original budget rows even after midnight.
Retained terminal rows make settlement idempotent across replicas/processes.
"""

from __future__ import annotations

import hashlib
import math
import random
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Any, Protocol

from .budget import Reservation
from .config import GatewayConfig
from .limiter import AdmissionDeadlineExceeded, BudgetExceeded, StoreUnavailable
from .table_store import day_partition

PARTITION = "odyn-admission-v2"
MAX_CONFLICT_ATTEMPTS = 12
MAX_CONFLICT_BACKOFF_SECONDS = 0.25


def global_budget_row(day: str) -> str:
    return f"budget:{day}:global"


def micros(usd: float) -> int:
    if not math.isfinite(usd) or usd < 0:
        raise ValueError("invalid usage")
    return int(
        (Decimal(str(usd)) * 1_000_000).to_integral_value(rounding=ROUND_CEILING)
    )


def _conflict_backoff_s(attempt: int) -> float:
    """Return capped exponential backoff with jitter for competing writers."""
    ceiling = min(0.01 * (2**attempt), MAX_CONFLICT_BACKOFF_SECONDS)
    return random.uniform(ceiling / 2, ceiling)


class AdmissionConflict(Exception):
    pass


class RateRejected(Exception):
    def __init__(self, wait: float):
        self.wait = wait


class AtomicRows(Protocol):
    def read(
        self, partition: str, row: str, *, deadline: float | None = None
    ) -> tuple[dict, str | None]: ...
    def commit(
        self,
        partition: str,
        changes: list[tuple[str, dict, str | None]],
        *,
        deadline: float | None = None,
    ) -> None: ...


class LocalRows:
    """Process-local CAS transaction harness; never used by Azure runtime."""

    def __init__(self):
        self.data: dict[tuple[str, str], tuple[dict, str]] = {}
        self.lock = threading.Lock()
        self.down = False
        self.sequence = 0

    def read(
        self, partition: str, row: str, *, deadline: float | None = None
    ) -> tuple[dict, str | None]:
        with self.lock:
            if self.down:
                raise StoreUnavailable("store unavailable")
            fields, etag = self.data.get((partition, row), ({}, None))
            return dict(fields), etag

    def commit(
        self,
        partition: str,
        changes: list[tuple[str, dict, str | None]],
        *,
        deadline: float | None = None,
    ) -> None:
        with self.lock:
            if self.down:
                raise StoreUnavailable("store unavailable")
            for row, _, etag in changes:
                if self.data.get((partition, row), ({}, None))[1] != etag:
                    raise AdmissionConflict()
            for row, fields, _ in changes:
                self.sequence += 1
                self.data[partition, row] = dict(fields), str(self.sequence)


@dataclass
class AzureRows:
    client: Any
    deadline_clock: Callable[[], float] = time.monotonic

    def _timeouts(self, deadline: float | None) -> dict[str, float]:
        if deadline is None:
            return {}
        remaining = deadline - self.deadline_clock()
        if remaining <= 0:
            raise AdmissionDeadlineExceeded()
        # These are transport timeouts, not just Storage service-side hints.
        return {"connection_timeout": remaining, "read_timeout": remaining}

    def read(
        self, partition: str, row: str, *, deadline: float | None = None
    ) -> tuple[dict, str | None]:
        from azure.core.exceptions import AzureError, ResourceNotFoundError

        try:
            entity = self.client.get_entity(partition, row, **self._timeouts(deadline))
            if deadline is not None and self.deadline_clock() >= deadline:
                raise AdmissionDeadlineExceeded()
            return dict(entity), entity.metadata["etag"]
        except AdmissionDeadlineExceeded:
            raise
        except ResourceNotFoundError:
            return {}, None
        except AzureError:
            raise StoreUnavailable("store unavailable") from None

    def commit(
        self,
        partition: str,
        changes: list[tuple[str, dict, str | None]],
        *,
        deadline: float | None = None,
    ) -> None:
        from azure.core import MatchConditions
        from azure.core.exceptions import AzureError, HttpResponseError
        from azure.data.tables import TableErrorCode, UpdateMode

        operations: list[Any] = []
        for row, fields, etag in changes:
            entity = {"PartitionKey": partition, "RowKey": row, **fields}
            if etag is None:
                operations.append(("create", entity))
            else:
                operations.append(
                    (
                        "update",
                        entity,
                        {
                            "mode": UpdateMode.REPLACE,
                            "etag": etag,
                            "match_condition": MatchConditions.IfNotModified,
                        },
                    )
                )
        try:
            self.client.submit_transaction(operations, **self._timeouts(deadline))
            if deadline is not None and self.deadline_clock() >= deadline:
                raise AdmissionDeadlineExceeded()
        except AdmissionDeadlineExceeded:
            raise
        except HttpResponseError as ex:
            # Azure Tables returns HTTP 202 for an Entity Group Transaction
            # even when one operation fails. The SDK raises
            # TableTransactionError with the failed operation's error_code;
            # its outer status_code therefore is not 409/412. Treat the
            # conditional-write conflicts as retryable CAS conflicts.
            error_code = getattr(ex, "error_code", None)
            error_code = getattr(error_code, "value", error_code)
            retryable_codes = {
                TableErrorCode.CONDITION_NOT_MET.value,
                TableErrorCode.UPDATE_CONDITION_NOT_SATISFIED.value,
                TableErrorCode.ENTITY_ALREADY_EXISTS.value,
            }
            if ex.status_code in (409, 412) or error_code in retryable_codes:
                raise AdmissionConflict() from None
            raise StoreUnavailable("store unavailable") from None
        except AzureError:
            # Unknown commit outcome is never retried as a new billable request.
            raise StoreUnavailable("store unavailable") from None


@dataclass
class AtomicAdmission:
    rows: AtomicRows
    config: GatewayConfig
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    process_lock: Any | None = None
    metrics: dict[str, int] | None = None
    deadline_clock: Callable[[], float] = time.monotonic

    def _check_deadline(self, deadline: float | None) -> None:
        if deadline is not None and self.deadline_clock() >= deadline:
            raise AdmissionDeadlineExceeded()

    def _with_lock(self, deadline: float | None, operation: Callable[[], Any]) -> Any:
        if self.process_lock is None:
            self._check_deadline(deadline)
            result = operation()
            self._check_deadline(deadline)
            return result
        if deadline is None:
            deadline = self.deadline_clock() + self.config.deadline_seconds
        timeout = max(0.0, deadline - self.deadline_clock())
        if timeout <= 0 or not self.process_lock.acquire(timeout=timeout):
            raise AdmissionDeadlineExceeded()
        try:
            self._check_deadline(deadline)
            result = operation()
            self._check_deadline(deadline)
            return result
        finally:
            self.process_lock.release()

    def _record_conflict(self, metrics: dict[str, int] | None = None) -> None:
        """Expose bounded CAS contention as an aggregate operational signal."""
        target = metrics if metrics is not None else self.metrics
        if target is not None:
            target["admission_conflict"] = target.get("admission_conflict", 0) + 1

    def probe(self) -> bool:
        try:
            self.rows.read(PARTITION, "rate:global")
            return True
        except StoreUnavailable:
            return False

    def reserve(
        self,
        principal: str,
        tokens: int,
        usd: float,
        *,
        deadline: float | None = None,
    ) -> Reservation:
        """Serialize this process's hot-row transaction when configured.

        Azure runtime supplies one shared lock to prevent local request
        threads from stampeding the same Table ETags. Other replicas remain
        coordinated by Table ETags and the bounded conflict retry below.
        """
        if deadline is None and self.process_lock is not None:
            deadline = self.deadline_clock() + self.config.deadline_seconds
        return self._with_lock(
            deadline, lambda: self._reserve(principal, tokens, usd, deadline)
        )

    def _reserve(
        self,
        principal: str,
        tokens: int,
        usd: float,
        deadline: float | None,
    ) -> Reservation:
        self._check_deadline(deadline)
        if tokens < 0:
            raise ValueError("invalid usage")
        money = micros(usd)
        now = self.clock()
        day = day_partition(now)
        partition = PARTITION
        principal_id = hashlib.sha256(principal.encode()).hexdigest()
        principal_row = f"budget:{day}:principal:{principal_id}"
        global_row = global_budget_row(day)
        rid = f"{day}:{uuid.uuid4().hex}"
        for attempt in range(MAX_CONFLICT_ATTEMPTS):
            self._check_deadline(deadline)
            changes: list[tuple[str, dict, str | None]] = []
            for row in (global_row, principal_row):
                old, etag = self.rows.read(partition, row, deadline=deadline)
                self._check_deadline(deadline)
                spent, spent_money = (
                    int(old.get("tokens", 0)),
                    int(old.get("micros", 0)),
                )
                if (
                    spent + tokens > self.config.daily_token_limit
                    or spent_money + money > micros(self.config.daily_spend_usd_limit)
                ):
                    raise BudgetExceeded("budget exhausted")
                changes.append(
                    (
                        row,
                        {"tokens": spent + tokens, "micros": spent_money + money},
                        etag,
                    )
                )
            for row, rps, burst in (
                ("rate:global", self.config.global_rps, self.config.global_burst),
                (
                    f"rate:principal:{principal_id}",
                    self.config.per_principal_rps,
                    self.config.per_principal_burst,
                ),
            ):
                old, etag = self.rows.read(partition, row, deadline=deadline)
                self._check_deadline(deadline)
                bucket = min(
                    burst,
                    float(old.get("bucket", burst))
                    + max(0, now - float(old.get("updated", now))) * rps,
                )
                if bucket < 1:
                    raise RateRejected((1 - bucket) / rps)
                changes.append(
                    (
                        row,
                        {
                            "bucket": bucket - 1,
                            "updated": max(now, float(old.get("updated", now))),
                        },
                        etag,
                    )
                )
            changes.append(
                (
                    f"res:{rid}",
                    {
                        "tokens": tokens,
                        "micros": money,
                        "principal_row": principal_row,
                        "global_row": global_row,
                        "state": "reserved",
                    },
                    None,
                )
            )
            try:
                self.rows.commit(partition, changes, deadline=deadline)
                self._check_deadline(deadline)
                return Reservation(rid, principal, tokens, usd)
            except AdmissionConflict:
                self._record_conflict()
                if attempt + 1 < MAX_CONFLICT_ATTEMPTS:
                    delay = _conflict_backoff_s(attempt)
                    if deadline is not None:
                        remaining = deadline - self.deadline_clock()
                        if remaining <= 0:
                            raise AdmissionDeadlineExceeded()
                        delay = min(delay, remaining)
                    self.sleep(delay)
        raise StoreUnavailable("admission contention")

    def settle(
        self,
        res: Reservation,
        actual_tokens: int | None,
        actual_usd: float | None,
        metrics: dict,
        *,
        deadline: float | None = None,
    ) -> None:
        if actual_tokens is None or actual_usd is None:
            metrics["budget_charged_unknown"] = (
                metrics.get("budget_charged_unknown", 0) + 1
            )
            return
        if actual_tokens < 0:
            raise ValueError("invalid usage")
        self._finish(
            res, actual_tokens, micros(actual_usd), "settled", metrics, deadline
        )

    def release(self, res: Reservation, *, deadline: float | None = None) -> None:
        self._finish(res, 0, 0, "released", {}, deadline)

    def _finish(
        self,
        res: Reservation,
        tokens: int,
        money: int,
        state: str,
        metrics: dict,
        deadline: float | None,
    ) -> None:
        if deadline is None and self.process_lock is not None:
            deadline = self.deadline_clock() + self.config.deadline_seconds
        self._with_lock(
            deadline,
            lambda: self._finish_locked(res, tokens, money, state, metrics, deadline),
        )

    def _finish_locked(
        self,
        res: Reservation,
        tokens: int,
        money: int,
        state: str,
        metrics: dict,
        deadline: float | None,
    ) -> None:
        partition = PARTITION
        for attempt in range(MAX_CONFLICT_ATTEMPTS):
            self._check_deadline(deadline)
            row, etag = self.rows.read(
                partition, f"res:{res.res_id}", deadline=deadline
            )
            self._check_deadline(deadline)
            if not row or row.get("state") != "reserved":
                return
            dt, dm = tokens - int(row["tokens"]), money - int(row["micros"])
            changes: list[tuple[str, dict, str | None]] = []
            for counter in (row["global_row"], row["principal_row"]):
                old, counter_etag = self.rows.read(
                    partition, counter, deadline=deadline
                )
                self._check_deadline(deadline)
                if not old or counter_etag is None:
                    raise StoreUnavailable("missing counter")
                fields = {
                    k: v
                    for k, v in old.items()
                    if k not in ("PartitionKey", "RowKey", "Timestamp")
                }
                fields.update(
                    tokens=int(old["tokens"]) + dt, micros=int(old["micros"]) + dm
                )
                changes.append((counter, fields, counter_etag))
            final = {
                k: v
                for k, v in row.items()
                if k not in ("PartitionKey", "RowKey", "Timestamp")
            }
            final.update(state=state, actual_tokens=tokens, actual_micros=money)
            changes.append((f"res:{res.res_id}", final, etag))
            try:
                self.rows.commit(partition, changes, deadline=deadline)
                self._check_deadline(deadline)
                if dt > 0 or dm > 0:
                    metrics["budget_overshoot"] = metrics.get("budget_overshoot", 0) + 1
                return
            except AdmissionConflict:
                self._record_conflict(metrics)
                if attempt + 1 < MAX_CONFLICT_ATTEMPTS:
                    delay = _conflict_backoff_s(attempt)
                    if deadline is not None:
                        remaining = deadline - self.deadline_clock()
                        if remaining <= 0:
                            raise AdmissionDeadlineExceeded()
                        delay = min(delay, remaining)
                    self.sleep(delay)
        raise StoreUnavailable("settlement contention")
