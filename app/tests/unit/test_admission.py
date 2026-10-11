"""Azure Table transaction conflicts must be retried as admission CAS races."""

import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock, local
from unittest.mock import Mock

from azure.core.exceptions import HttpResponseError
from azure.data.tables import TableErrorCode, TableTransactionError

from gateway.admission import (
    PARTITION,
    AdmissionConflict,
    AdmissionDeadlineExceeded,
    AtomicAdmission,
    AzureRows,
    LocalRows,
)
from gateway.config import GatewayConfig
from gateway.limiter import StoreUnavailable


class AzureRowsTests(unittest.TestCase):
    def test_table_requests_receive_remaining_transport_deadline(self):
        ticks = [10.0]

        class Entity(dict):
            def __init__(self, **values):
                super().__init__(**values)
                self.metadata = {"etag": "etag-1"}

        entity = Entity(value=1)
        client = Mock()
        client.get_entity.return_value = entity
        rows = AzureRows(client, deadline_clock=lambda: ticks[0])

        rows.read(PARTITION, "row", deadline=12.5)

        self.assertEqual(
            client.get_entity.call_args.kwargs,
            {"connection_timeout": 2.5, "read_timeout": 2.5},
        )

    def test_table_transaction_receives_remaining_transport_deadline(self):
        client = Mock()
        rows = AzureRows(client, deadline_clock=lambda: 10.0)

        rows.commit(
            PARTITION,
            [("row", {"value": 1}, None)],
            deadline=12.5,
        )

        self.assertEqual(
            client.submit_transaction.call_args.kwargs,
            {"connection_timeout": 2.5, "read_timeout": 2.5},
        )

    def transaction_error(self, code: TableErrorCode) -> TableTransactionError:
        error = TableTransactionError(message="transaction operation failed")
        error.status_code = (
            202  # EGT HTTP envelope is accepted despite sub-operation failure.
        )
        error.error_code = code
        return error

    def test_egt_conditional_failure_is_a_retryable_conflict(self):
        client = Mock()
        client.submit_transaction.side_effect = self.transaction_error(
            TableErrorCode.UPDATE_CONDITION_NOT_SATISFIED
        )

        with self.assertRaises(AdmissionConflict):
            AzureRows(client).commit(
                PARTITION, [("rate:global", {"bucket": 1}, "etag")]
            )

    def test_egt_entity_already_exists_is_a_retryable_conflict(self):
        client = Mock()
        client.submit_transaction.side_effect = self.transaction_error(
            TableErrorCode.ENTITY_ALREADY_EXISTS
        )

        with self.assertRaises(AdmissionConflict):
            AzureRows(client).commit(PARTITION, [("rate:global", {"bucket": 1}, None)])

    def test_unrelated_table_error_remains_fail_closed(self):
        client = Mock()
        error = self.transaction_error(TableErrorCode.SERVER_BUSY)
        client.submit_transaction.side_effect = error

        with self.assertRaises(StoreUnavailable):
            AzureRows(client).commit(
                PARTITION, [("rate:global", {"bucket": 1}, "etag")]
            )

    def test_http_conditional_status_remains_a_conflict(self):
        client = Mock()
        error = HttpResponseError(message="condition failed")
        error.status_code = 412
        client.submit_transaction.side_effect = error

        with self.assertRaises(AdmissionConflict):
            AzureRows(client).commit(
                PARTITION, [("rate:global", {"bucket": 1}, "etag")]
            )


class AdmissionRetryTests(unittest.TestCase):
    def test_cas_conflict_retries_and_reserves_instead_of_reporting_store_outage(self):
        rows = LocalRows()
        original_commit = rows.commit
        attempts = 0

        def conflict_once(partition, changes, *, deadline=None):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise AdmissionConflict()
            original_commit(partition, changes, deadline=deadline)

        rows.commit = conflict_once  # type: ignore[method-assign]

        def clock():
            return 1_760_000_000.0

        admission = AtomicAdmission(
            rows, GatewayConfig(), clock=clock, sleep=lambda _: None
        )

        reservation = admission.reserve("principal", 128, 0.01)

        self.assertEqual(attempts, 2)
        self.assertIsNotNone(reservation)

    def test_retryable_table_conflicts_are_counted_for_alerting(self):
        rows = LocalRows()
        original_commit = rows.commit
        attempts = 0
        observed = {}

        def conflict_once(partition, changes, *, deadline=None):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise AdmissionConflict()
            original_commit(partition, changes, deadline=deadline)

        rows.commit = conflict_once  # type: ignore[method-assign]
        admission = AtomicAdmission(
            rows,
            GatewayConfig(),
            clock=lambda: 1_760_000_000.0,
            sleep=lambda _: None,
            metrics=observed,
        )

        admission.reserve("principal", 128, 0.01)

        self.assertEqual(observed, {"admission_conflict": 1})

    def test_contention_retries_are_jittered_and_bounded(self):
        rows = LocalRows()
        attempts = 0
        waits = []

        def always_conflict(partition, changes, *, deadline=None):
            nonlocal attempts
            attempts += 1
            raise AdmissionConflict()

        rows.commit = always_conflict  # type: ignore[method-assign]
        admission = AtomicAdmission(
            rows,
            GatewayConfig(),
            clock=lambda: 1_760_000_000.0,
            sleep=waits.append,
        )

        with self.assertRaises(StoreUnavailable):
            admission.reserve("principal", 128, 0.01)

        self.assertEqual(attempts, 12)
        self.assertEqual(len(waits), 11)
        for attempt, wait in enumerate(waits):
            ceiling = min(0.01 * (2**attempt), 0.25)
            self.assertGreaterEqual(wait, ceiling / 2)
            self.assertLessEqual(wait, ceiling)

    def test_simultaneous_admissions_make_progress_under_hot_row_contention(self):
        rows = LocalRows()
        original_commit = rows.commit
        first_wave = Barrier(10)
        thread_state = local()

        def synchronize_first_commit(partition, changes, *, deadline=None):
            if not getattr(thread_state, "joined_first_wave", False):
                thread_state.joined_first_wave = True
                first_wave.wait(timeout=3)
            original_commit(partition, changes, deadline=deadline)

        rows.commit = synchronize_first_commit  # type: ignore[method-assign]
        config = GatewayConfig(
            per_principal_burst=100,
            global_burst=100,
            per_principal_rps=100,
            global_rps=100,
        )

        def reserve(index):
            admission = AtomicAdmission(rows, config)
            return admission.reserve("same-principal", 1, 0.000001)

        with ThreadPoolExecutor(max_workers=10) as pool:
            reservations = list(pool.map(reserve, range(10)))

        self.assertEqual(len(reservations), 10)
        self.assertTrue(all(reservation.tokens == 1 for reservation in reservations))

    def test_shared_process_coordinator_avoids_local_hot_row_stampede(self):
        rows = LocalRows()
        original_commit = rows.commit
        commits = 0
        commits_lock = Lock()

        def count_commit(partition, changes, *, deadline=None):
            nonlocal commits
            with commits_lock:
                commits += 1
            original_commit(partition, changes, deadline=deadline)

        rows.commit = count_commit  # type: ignore[method-assign]
        admission = AtomicAdmission(
            rows,
            GatewayConfig(),
            clock=lambda: 1_760_000_000.0,
            sleep=lambda _: None,
            process_lock=Lock(),
        )
        start = Barrier(10)

        def reserve(_index):
            start.wait(timeout=3)
            return admission.reserve("same-principal", 1, 0.000001)

        with ThreadPoolExecutor(max_workers=10) as pool:
            reservations = list(pool.map(reserve, range(10)))

        self.assertEqual(len(reservations), 10)
        self.assertEqual(commits, 10)
        self.assertEqual(len({r.res_id for r in reservations}), 10)

    def test_slow_store_expires_request_and_releases_coordinator_for_other_principal(
        self,
    ):
        rows = LocalRows()
        original_read = rows.read
        slow_started = Event()

        def slow_read(partition, row, *, deadline=None):
            if not slow_started.is_set():
                slow_started.set()
                remaining = max(0.0, deadline - time.monotonic())
                time.sleep(remaining)
                raise AdmissionDeadlineExceeded()
            return original_read(partition, row, deadline=deadline)

        rows.read = slow_read  # type: ignore[method-assign]
        coordinator = Lock()
        admission = AtomicAdmission(
            rows,
            GatewayConfig(
                global_burst=20,
                global_rps=20,
                per_principal_burst=20,
                per_principal_rps=20,
            ),
            process_lock=coordinator,
        )

        def slow_request():
            return admission.reserve(
                "slow-principal", 1, 0.000001, deadline=time.monotonic() + 0.12
            )

        with ThreadPoolExecutor(max_workers=3) as pool:
            slow = pool.submit(slow_request)
            self.assertTrue(slow_started.wait(timeout=1))
            queued_start = time.monotonic()
            blocked = pool.submit(
                admission.reserve,
                "other-principal",
                1,
                0.000001,
                deadline=time.monotonic() + 0.025,
            )
            with self.assertRaises(AdmissionDeadlineExceeded):
                blocked.result(timeout=0.2)
            self.assertLess(time.monotonic() - queued_start, 0.15)
            with self.assertRaises(AdmissionDeadlineExceeded):
                slow.result(timeout=0.2)
            # The slow store call released the lock at its request deadline;
            # a different principal can then complete admission.
            reservation = admission.reserve(
                "other-principal", 1, 0.000001, deadline=time.monotonic() + 1
            )
            self.assertEqual(reservation.principal, "other-principal")


if __name__ == "__main__":
    unittest.main()
