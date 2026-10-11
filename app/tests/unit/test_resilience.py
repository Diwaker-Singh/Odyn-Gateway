"""Breaker + retry-boundary tests (fake clock; 401/402 never trip)."""

import unittest

from gateway.resilience import Breaker, is_presend_retryable


class BreakerTests(unittest.TestCase):
    def test_counts_failures_and_opens(self):
        b = Breaker(threshold=3, cool_down_s=30.0)
        b.record("conn_fail", 0.0)
        b.record("upstream_5xx", 0.0)
        self.assertFalse(b.is_open(0.0))
        b.record("upstream_429", 0.0)
        self.assertTrue(b.is_open(0.0))
        self.assertFalse(b.is_open(31.0))  # cools down

    def test_401_402_never_trip(self):
        b = Breaker(threshold=2)
        for _ in range(10):
            b.record("upstream_401", 0.0)
            b.record("upstream_402", 0.0)
        self.assertFalse(b.is_open(0.0))
        self.assertEqual(b.failures, 0)

    def test_caller_4xx_never_trip(self):
        b = Breaker(threshold=1)
        b.record("caller_4xx", 0.0)
        self.assertFalse(b.is_open(0.0))

    def test_per_instance_scope_documented(self):
        # Two instances do not share state: no global coordination claimed.
        a, c = Breaker(threshold=1), Breaker(threshold=1)
        a.record("timeout", 0.0)
        self.assertTrue(a.is_open(0.0))
        self.assertFalse(c.is_open(0.0))


class RetryBoundaryTests(unittest.TestCase):
    def test_only_presend_retryable(self):
        for kind in ("dns", "refused", "connect_timeout", "tls_handshake"):
            self.assertTrue(is_presend_retryable(kind))
        for kind in (
            "read_timeout",
            "reset_after_send",
            "http_500",
            "http_429",
            "cancelled",
        ):
            self.assertFalse(is_presend_retryable(kind))


if __name__ == "__main__":
    unittest.main()
