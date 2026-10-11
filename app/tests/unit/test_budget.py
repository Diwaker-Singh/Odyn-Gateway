"""Limiter + ledger tests: bounds, isolation, races, outage, settlement."""

import threading
import unittest

from gateway.budget import BudgetLedger
from gateway.config import GatewayConfig
from gateway.limiter import BudgetExceeded, MemoryStore, RateLimiter, StoreUnavailable


class LimiterTests(unittest.TestCase):
    def test_below_limit_allows(self):
        s, lim = MemoryStore(), RateLimiter(5.0, 10)
        for _ in range(10):
            ok, _ = lim.allow(s, "k", 1000.0)
            self.assertTrue(ok)

    def test_burst_then_429_with_retry_after(self):
        s, lim = MemoryStore(), RateLimiter(5.0, 10)
        for _ in range(10):
            lim.allow(s, "k", 1000.0)
        ok, wait = lim.allow(s, "k", 1000.0)
        self.assertFalse(ok)
        self.assertGreater(wait, 0)

    def test_refill_over_time(self):
        s, lim = MemoryStore(), RateLimiter(5.0, 10)
        for _ in range(10):
            lim.allow(s, "k", 1000.0)
        ok, _ = lim.allow(s, "k", 1002.0)  # +10 tokens @5/s
        self.assertTrue(ok)

    def test_second_principal_unaffected(self):
        s, lim = MemoryStore(), RateLimiter(5.0, 10)
        for _ in range(10):
            lim.allow(s, "a", 1000.0)
        ok, _ = lim.allow(s, "b", 1000.0)
        self.assertTrue(ok)

    def test_store_outage_fails_closed(self):
        s, lim = MemoryStore(), RateLimiter(5.0, 10)
        s.down = True
        with self.assertRaises(StoreUnavailable):
            lim.allow(s, "k", 1000.0)


class LedgerTests(unittest.TestCase):
    def ledger(self, **cfg) -> BudgetLedger:
        return BudgetLedger(MemoryStore(), GatewayConfig(**cfg))

    def test_reserve_settle_actual(self):
        led = self.ledger()
        m = {}
        r = led.reserve("p", 100, 0.01)
        led.settle(r, 60, 0.006, m)
        self.assertEqual(led.store.spent_tokens, 60)
        self.assertNotIn("budget_charged_unknown", m)

    def test_overshoot_recorded(self):
        led = self.ledger()
        m = {}
        r = led.reserve("p", 100, 0.01)
        led.settle(r, 150, 0.02, m)
        self.assertEqual(m.get("budget_overshoot"), 1)
        self.assertEqual(led.store.spent_tokens, 150)

    def test_unknown_retained(self):
        led = self.ledger()
        m = {}
        r = led.reserve("p", 100, 0.01)
        led.settle(r, None, None, m)
        self.assertEqual(m.get("budget_charged_unknown"), 1)
        self.assertEqual(led.store.spent_tokens, 100)  # retained, not freed

    def test_duplicate_settle_once(self):
        led = self.ledger()
        m = {}
        r = led.reserve("p", 100, 0.01)
        led.settle(r, 60, 0.006, m)
        led.settle(r, 60, 0.006, m)
        self.assertEqual(led.store.spent_tokens, 60)

    def test_release_only_when_unsent(self):
        led = self.ledger()
        r = led.reserve("p", 100, 0.01)
        led.release(r)
        self.assertEqual(led.store.spent_tokens, 0)
        led.release(r)  # idempotent
        self.assertEqual(led.store.spent_tokens, 0)

    def test_oversized_rejected(self):
        led = self.ledger()
        with self.assertRaises(BudgetExceeded):
            led.reserve("p", 200_000, 1.0)

    def test_exhaustion_rejected(self):
        led = self.ledger(daily_token_limit=100)
        led.reserve("p", 100, 0.001)
        with self.assertRaises(BudgetExceeded):
            led.reserve("p", 1, 0.001)

    def test_store_outage_fails_closed(self):
        led = self.ledger()
        led.store.down = True
        with self.assertRaises(StoreUnavailable):
            led.reserve("p", 10, 0.001)

    def test_last_slot_race_single_winner(self):
        """Concurrent reservations at the last budget slot: at most one wins."""
        led = self.ledger(daily_token_limit=100)
        wins, lock = [], threading.Lock()
        barrier = threading.Barrier(20)

        def attempt():
            barrier.wait()
            try:
                led.reserve("p", 100, 0.001)
                with lock:
                    wins.append(1)
            except BudgetExceeded:
                pass

        threads = [threading.Thread(target=attempt) for _ in range(20)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(wins), 1)
        self.assertLessEqual(led.store.spent_tokens, 100)

    def test_independent_clients_under_contention(self):
        led = self.ledger(daily_token_limit=1000)
        errs = []

        def attempt(i):
            try:
                r = led.reserve(f"client-{i % 4}", 10, 0.001)
                led.settle(r, 10, 0.001, {})
            except Exception as e:
                errs.append(e)

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(40)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errs, [])
        self.assertEqual(led.store.spent_tokens, 400)


if __name__ == "__main__":
    unittest.main()
