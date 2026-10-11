"""End-to-end pipeline tests through Gateway (stub upstream, fake clock).

Covers: 200 happy path, 401/403 mapping, 413/415/422, 429+Retry-After
(per-principal isolation + global), 502/503/504 mapping, presend release vs
ambiguous retain, 401-credential no-breaker, envelope + X-Request-Id,
telemetry redaction (sentinel prompt never in logs), health/readiness.
"""

import unittest

from helpers import (
    KID,
    auth_headers,
    base_claims,
    chat_body,
    machine_token,
    make_gateway,
    user_token,
)

from gateway.admission import AdmissionConflict, AtomicAdmission, LocalRows
from gateway.auth import mint_fixture


class PipelineTests(unittest.TestCase):
    def test_happy_path_200(self):
        gw = make_gateway()
        s, body, h = gw.chat(auth_headers(user_token(gw)), chat_body())
        self.assertEqual(s, 200)
        self.assertIn("data", body)
        self.assertIn("X-Request-Id", h)
        self.assertEqual(gw.telemetry.events[-1]["status"], 200)
        self.assertIn("latency_ms", gw.telemetry.events[-1])

    def test_missing_token_401(self):
        gw = make_gateway()
        s, body, _ = gw.chat({"Content-Type": "application/json"}, chat_body())
        self.assertEqual(s, 401)
        self.assertEqual(body["error"]["code"], "unauthenticated")

    def test_bad_signature_401(self):
        gw = make_gateway()
        bad = mint_fixture(b"different-secret-32-bytes-minimum!", KID, base_claims())
        s, body, _ = gw.chat(auth_headers(bad), chat_body())
        self.assertEqual(s, 401)
        self.assertEqual(body["error"]["code"], "unauthenticated")

    def test_missing_scope_403(self):
        gw = make_gateway()
        s, body, _ = gw.chat(auth_headers(user_token(gw, scp="other")), chat_body())
        self.assertEqual(s, 403)
        self.assertEqual(body["error"]["code"], "forbidden")
        self.assertEqual(gw.telemetry.metrics.get("auth_403"), 1)
        self.assertIsNone(gw.telemetry.metrics.get("auth_401"))

    def test_machine_flow_200(self):
        gw = make_gateway()
        s, _, _ = gw.chat(auth_headers(machine_token(gw)), chat_body())
        self.assertEqual(s, 200)

    def test_machine_missing_role_403(self):
        gw = make_gateway()
        s, body, _ = gw.chat(
            auth_headers(machine_token(gw, roles=["Other"])), chat_body()
        )
        self.assertEqual(s, 403)
        self.assertEqual(body["error"]["code"], "forbidden")

    def test_per_principal_429_with_retry_after_and_isolation(self):
        gw = make_gateway(
            per_principal_burst=2,
            per_principal_rps=0.01,
            global_burst=1000,
            global_rps=1000.0,
        )
        tok = user_token(gw)
        gw.chat(auth_headers(tok), chat_body())
        gw.chat(auth_headers(tok), chat_body())
        s, _, h = gw.chat(auth_headers(tok), chat_body())
        self.assertEqual(s, 429)
        self.assertIn("Retry-After", h)
        int(h["Retry-After"])
        self.assertEqual(gw.telemetry.events[-1]["status"], 429)
        self.assertIn("latency_ms", gw.telemetry.events[-1])
        # second principal unaffected
        gw2tok = user_token(gw, oid="user-2")
        s2, _, _ = gw.chat(auth_headers(gw2tok), chat_body())
        self.assertEqual(s2, 200)

    def test_global_429(self):
        gw = make_gateway(
            per_principal_burst=1000,
            per_principal_rps=1000.0,
            global_burst=1,
            global_rps=0.01,
        )
        gw.chat(auth_headers(user_token(gw, oid="a")), chat_body())
        s, _, h = gw.chat(auth_headers(user_token(gw, oid="b")), chat_body())
        self.assertEqual(s, 429)
        self.assertIn("Retry-After", h)

    def test_upstream_500_maps_502_safe_body(self):
        gw = make_gateway()
        gw.upstream.mode = "http_500"
        s, body, _ = gw.chat(auth_headers(user_token(gw)), chat_body())
        self.assertEqual(s, 502)
        self.assertNotIn("Traceback", str(body))

    def test_timeout_maps_504_and_retains_budget(self):
        gw = make_gateway()
        before = gw.store.spent_tokens
        gw.upstream.mode = "timeout"
        s, _, _ = gw.chat(auth_headers(user_token(gw)), chat_body())
        self.assertEqual(s, 504)
        self.assertGreater(gw.store.spent_tokens, before)  # retained
        self.assertEqual(gw.telemetry.metrics.get("budget_charged_unknown"), 1)

    def test_presend_failure_releases_budget(self):
        gw = make_gateway()
        before = gw.store.spent_tokens
        gw.upstream.mode = "presend_refused"
        s, _, _ = gw.chat(auth_headers(user_token(gw)), chat_body())
        self.assertEqual(s, 502)
        self.assertEqual(gw.store.spent_tokens, before)  # released
        self.assertEqual(gw.upstream.calls, 2)  # one bounded retry only

    def test_credential_fault_does_not_trip_breaker(self):
        gw = make_gateway()
        gw.upstream.mode = "http_401"
        for _ in range(8):
            gw.chat(auth_headers(user_token(gw)), chat_body())
        self.assertFalse(gw.breaker.is_open(gw.now))
        self.assertGreater(gw.telemetry.metrics.get("upstream_credential_fault", 0), 0)

    def test_store_outage_503_fail_closed(self):
        gw = make_gateway()
        gw.store.down = True
        s, body, _ = gw.chat(auth_headers(user_token(gw)), chat_body())
        self.assertEqual(s, 503)
        self.assertEqual(body["error"]["code"], "upstream_unavailable")

    def test_table_contention_exhaustion_fails_closed_without_provider_dispatch(self):
        gw = make_gateway()
        admission = AtomicAdmission(
            LocalRows(), gw.config, metrics=gw.telemetry.metrics
        )

        def always_conflict(_partition, _changes, *, deadline=None):
            raise AdmissionConflict()

        admission.rows.commit = always_conflict
        gw.admission = admission
        gw.ledger = admission
        status, body, headers = gw.chat(auth_headers(user_token(gw)), chat_body())

        self.assertEqual(status, 503)
        self.assertEqual(body["error"]["code"], "upstream_unavailable")
        self.assertNotIn("Retry-After", headers)
        self.assertEqual(gw.upstream.calls, 0)
        self.assertEqual(gw.telemetry.metrics.get("admission_conflict"), 12)

    def test_telemetry_redaction_sentinel(self):
        gw = make_gateway()
        sentinel = "SENTINEL_PROMPT_XYZ_123"
        import json

        raw = json.dumps(
            {
                "model": "default",
                "messages": [{"role": "user", "content": sentinel}],
                "max_tokens": 5,
            }
        ).encode()
        gw.chat(auth_headers(user_token(gw)), raw)
        dump = str(gw.telemetry.events) + str(gw.telemetry.metrics)
        self.assertNotIn(sentinel, dump)
        self.assertNotIn("fixture-secret", dump)

    def test_health_and_ready(self):
        gw = make_gateway()
        s, _ = gw.healthz()
        self.assertEqual(s, 200)
        s, body = gw.readyz()
        self.assertEqual(s, 200)
        self.assertEqual(body["upstream_mode"], "stub")
        gw.store.down = True
        s, _ = gw.readyz()
        self.assertEqual(s, 503)

    def test_caller_request_id_accepted_and_bounded(self):
        gw = make_gateway()
        s, _, h = gw.chat(
            {**auth_headers(user_token(gw)), "X-Request-Id": "client-123"}, chat_body()
        )
        self.assertEqual(s, 200)
        self.assertEqual(h["X-Request-Id"], "client-123")
        _, _, h2 = gw.chat(
            {**auth_headers(user_token(gw)), "X-Request-Id": "!!!not an id!!!"},
            chat_body(),
        )
        self.assertNotEqual(h2["X-Request-Id"], "!!!not an id!!!")

    def test_missing_usage_retained_with_unknown_metric(self):
        gw = make_gateway()
        before = gw.store.spent_tokens
        gw.upstream.mode = "ok_no_usage"
        s, body, _ = gw.chat(auth_headers(user_token(gw)), chat_body())
        self.assertEqual(s, 200)
        self.assertEqual(body["data"].get("usage"), "unknown")
        self.assertGreater(gw.store.spent_tokens, before)  # retained
        self.assertEqual(gw.telemetry.metrics.get("budget_charged_unknown"), 1)

    def test_billing_fault_402_safe_and_no_breaker(self):
        gw = make_gateway()
        gw.upstream.mode = "http_402"
        s, body, _ = gw.chat(auth_headers(user_token(gw)), chat_body())
        self.assertEqual(s, 503)
        self.assertEqual(body["error"]["code"], "upstream_unavailable")
        self.assertFalse(gw.breaker.is_open(gw.now))
        self.assertGreater(gw.telemetry.metrics.get("upstream_credential_fault", 0), 0)

    def test_zero_outbound_network_calls(self):
        """Deny-network fixture: the stub pipeline must never touch sockets."""
        import socket
        from unittest import mock

        gw = make_gateway()
        with mock.patch.object(
            socket, "socket", side_effect=AssertionError("net call")
        ):
            s, _, _ = gw.chat(auth_headers(user_token(gw)), chat_body())
            hs, _ = gw.healthz()
            rs, _ = gw.readyz()
        self.assertEqual(s, 200)
        self.assertEqual(hs, 200)
        self.assertEqual(rs, 200)


if __name__ == "__main__":
    unittest.main()
