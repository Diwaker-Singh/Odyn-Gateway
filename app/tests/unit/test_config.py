"""Config invariants: demo defaults match user decisions; spend enforced."""

import unittest

from gateway.budget import BudgetLedger
from gateway.config import GatewayConfig
from gateway.limiter import BudgetExceeded, MemoryStore


class ConfigDefaultsTests(unittest.TestCase):
    def test_demo_defaults(self):
        c = GatewayConfig()
        self.assertEqual((c.per_principal_rps, c.per_principal_burst), (5.0, 10))
        self.assertEqual((c.global_rps, c.global_burst), (20.0, 40))
        self.assertEqual(c.max_body_bytes, 32 * 1024)
        self.assertEqual(c.max_tokens_ceiling, 1024)
        self.assertEqual(c.daily_token_limit, 100_000)
        self.assertEqual(c.upstream_mode, "stub")
        self.assertFalse(c.network_enforced)

    def test_single_alias_allowlist_structure(self):
        c = GatewayConfig()
        self.assertIn("default", c.models)
        entry = c.models["default"]
        self.assertTrue(entry.provider_model_id)
        self.assertGreaterEqual(entry.input_usd_per_1k, 0)
        self.assertGreaterEqual(entry.output_usd_per_1k, 0)
        self.assertTrue(c.price_table_version)

    def test_spend_budget_enforced_alongside_tokens(self):
        led = BudgetLedger(MemoryStore(), GatewayConfig())
        with self.assertRaises(BudgetExceeded):
            led.reserve("p", 10, 999.0)  # exceeds $5/day demo ceiling


if __name__ == "__main__":
    unittest.main()
