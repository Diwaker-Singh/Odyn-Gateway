"""M3: Table adapter boundary tests (no service, no network).

Proves plan invariants only. Runtime EGT/ETag/412 behavior is Gate M4 and
must NOT be inferred from these tests (validator `table_race` stays
not_applicable until then).
"""

import unittest

from gateway.limiter import BudgetExceeded
from gateway.table_store import (
    TableBudgetStore,
    day_partition,
    principal_hash,
    to_mills,
)


class TableBoundaryTests(unittest.TestCase):
    def store(self, **kw):
        return TableBudgetStore(table_client=object(), **kw)

    def test_utc_day_partition_format(self):
        self.assertRegex(day_partition(0.0), r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(day_partition(0.0), "1970-01-01")

    def test_principal_hash_stable_bounded(self):
        h1, h2 = principal_hash("tenant-1:user-1"), principal_hash("tenant-1:user-1")
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 16)
        self.assertNotEqual(h1, principal_hash("tenant-1:user-2"))
        # Raw bearer material never becomes a row key (hash only).
        self.assertNotIn("user-1", h1)

    def test_mills_conversion(self):
        self.assertEqual(to_mills(0.001), 1)
        self.assertEqual(to_mills(5.0), 5000)

    def test_reserve_plan_same_partition_and_limits(self):
        s = self.store(day="2026-10-09")
        plan = s.reserve("tenant-1:user-1", 100, 0.01, "res-1")
        self.assertEqual(plan["partition"], "2026-10-09")
        parts = {op[1] for op in plan["ops"]}
        self.assertEqual(parts, {"2026-10-09"})
        self.assertTrue(plan["conditional"])
        self.assertTrue(s.batch_fits(len(plan["ops"]), 1024))

    def test_reserve_plan_idempotent_id(self):
        s = self.store(day="2026-10-09")
        plan = s.reserve("p", 10, 0.001, "res-42")
        rows = [op[2] for op in plan["ops"]]
        self.assertIn("res:res-42", rows)

    def test_oversized_rejected_locally(self):
        s = self.store()
        with self.assertRaises(BudgetExceeded):
            s.reserve("p", 200_000, 0.001, "res-1")
        with self.assertRaises(BudgetExceeded):
            s.reserve("p", 10, 999.0, "res-2")

    def test_batch_limits(self):
        s = self.store()
        self.assertTrue(s.batch_fits(3, 1024))
        self.assertFalse(s.batch_fits(101, 100))
        self.assertFalse(s.batch_fits(3, 5 * 1024 * 1024))

    def test_backoff_bounded_with_jitter(self):
        s = self.store()
        for attempt in range(3):
            for _ in range(20):
                w = s.backoff_s(attempt)
                self.assertGreaterEqual(w, 0)
                self.assertLess(w, 0.05 * (2**attempt) * 2 + 0.001)

    def test_conflict_codes(self):
        s = self.store()
        self.assertTrue(s.is_conflict(409))
        self.assertTrue(s.is_conflict(412))
        self.assertFalse(s.is_conflict(200))
        self.assertFalse(s.is_conflict(500))
        self.assertFalse(s.is_conflict(None))


if __name__ == "__main__":
    unittest.main()
