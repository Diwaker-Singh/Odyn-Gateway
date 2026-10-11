"""Release integration regressions using actual adapters and isolated fakes."""

import asyncio
import importlib.util
import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx
from helpers import auth_headers, chat_body, make_gateway, user_token

from gateway.admission import (
    PARTITION,
    AtomicAdmission,
    LocalRows,
    RateRejected,
    global_budget_row,
    micros,
)
from gateway.config import GatewayConfig, ModelEntry
from gateway.jwks import TrustedJwks
from gateway.limiter import BudgetExceeded, StoreUnavailable
from gateway.otel import ExportedTelemetry
from gateway.provider import URL, OpenRouter
from gateway.runtime import build_gateway


class SharedAdmissionTests(unittest.TestCase):
    def ledger(self, **kw):
        self.clock = [216000.0]
        rows = LocalRows()
        cfg = GatewayConfig(**kw)
        return AtomicAdmission(
            rows, cfg, clock=lambda: self.clock[0], sleep=lambda _: None
        )

    def test_two_gateways_use_same_atomic_backend(self):
        ledger = self.ledger(global_burst=1)
        a, b = make_gateway(), make_gateway()
        for gw in (a, b):
            gw.admission = ledger
            gw.ledger = ledger
        self.assertEqual(a.chat(auth_headers(user_token(a)), chat_body())[0], 200)
        self.assertEqual(b.chat(auth_headers(user_token(b)), chat_body())[0], 429)
        self.assertEqual(b.upstream.calls, 0)

    def test_provider_receives_remaining_request_budget_after_slow_admission(self):
        from gateway.upstream import StubResult

        ticks = [100.0]
        gw = make_gateway(deadline_seconds=3.0)
        gw._clock = lambda: ticks[0]
        rows = LocalRows()
        original_read = rows.read
        advanced = False

        def slow_read(partition, row, *, deadline=None):
            nonlocal advanced
            if not advanced:
                advanced = True
                ticks[0] += 2.0
            return original_read(partition, row, deadline=deadline)

        rows.read = slow_read  # type: ignore[method-assign]
        admission = AtomicAdmission(
            rows,
            gw.config,
            clock=lambda: 1_760_000_000.0,
            deadline_clock=lambda: gw.now,
        )
        gw.admission = gw.ledger = admission
        observed = []

        class Provider:
            def send_with_deadline(self, model, messages, cap, rid, budget):
                observed.append(budget)
                return StubResult("timeout")

        gw.upstream = Provider()
        status, _, _ = gw.chat(auth_headers(user_token(gw)), chat_body())

        self.assertEqual(status, 504)
        self.assertEqual(observed, [1.0])

    def test_concurrent_final_slot_has_one_winner(self):
        led = self.ledger(daily_token_limit=1)
        barrier = threading.Barrier(20)

        def attempt(i):
            barrier.wait()
            try:
                led.reserve(str(i), 1, 0)
                return 1
            except (BudgetExceeded, StoreUnavailable):
                return 0

        with ThreadPoolExecutor(max_workers=20) as pool:
            self.assertEqual(sum(pool.map(attempt, range(20))), 1)

    def test_budget_rejection_consumes_no_rate_tokens(self):
        led = self.ledger(daily_token_limit=10, global_burst=1)
        with self.assertRaises(BudgetExceeded):
            led.reserve("p", 11, 0)
        res = led.reserve("p", 10, 0)
        self.assertTrue(res.res_id)

    def test_settle_releases_principal_and_global_exactly_once(self):
        led = self.ledger()
        res = led.reserve("p", 100, 0.01)
        for _ in range(2):
            led.settle(res, 40, 0.002, {})
        for row, (fields, _) in led.rows.data.items():
            if row[1].startswith("budget:"):
                self.assertEqual(fields["tokens"], 40)
                self.assertEqual(fields["micros"], 2000)

    def test_concurrent_settlement_is_idempotent(self):
        led = self.ledger()
        res = led.reserve("p", 100, 0.01)
        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(lambda _: led.settle(res, 40, 0.002, {}), range(10)))
        self.assertEqual(
            led.rows.read(PARTITION, global_budget_row("1970-01-03"))[0]["tokens"], 40
        )

    def test_settlement_uses_original_day_after_midnight(self):
        led = self.ledger()
        res = led.reserve("p", 100, 0.01)
        self.clock[0] += 86400
        led.settle(res, 40, 0.002, {})
        self.assertEqual(
            led.rows.read(PARTITION, global_budget_row("1970-01-03"))[0]["tokens"], 40
        )
        self.assertEqual(
            led.rows.read(PARTITION, global_budget_row("1970-01-04"))[0], {}
        )

    def test_refill_and_clock_rollback(self):
        led = self.ledger(global_burst=1)
        led.reserve("p", 1, 0)
        self.clock[0] -= 0.01
        with self.assertRaises(RateRejected):
            led.reserve("p", 1, 0)
        self.clock[0] += 1
        led.reserve("p", 1, 0)

    def test_outage_prevents_dispatch_and_unknown_retains(self):
        led = self.ledger()
        gw = make_gateway()
        gw.admission = gw.ledger = led
        led.rows.down = True
        self.assertEqual(gw.chat(auth_headers(user_token(gw)), chat_body())[0], 503)
        self.assertEqual(gw.upstream.calls, 0)
        led.rows.down = False
        res = led.reserve("p", 100, 0.01)
        led.settle(res, None, None, {})
        self.assertEqual(
            led.rows.read(PARTITION, global_budget_row("1970-01-03"))[0]["tokens"], 100
        )
        led.rows.down = True
        with self.assertRaises(StoreUnavailable):
            led.settle(res, 1, 0, {})

    def test_microdollar_reservation_rounds_up(self):
        self.assertEqual(micros(0.0000001), 1)
        with self.assertRaises(ValueError):
            micros(float("nan"))

    def test_global_rate_does_not_reset_at_midnight(self):
        led = self.ledger(global_rps=1, global_burst=1, daily_token_limit=1)
        self.clock[0] = 86399.95
        led.reserve("a", 1, 0)
        self.clock[0] = 86400.05
        with self.assertRaises(RateRejected):
            led.reserve("b", 1, 0)
        self.assertEqual(
            led.rows.read(PARTITION, global_budget_row("1970-01-02"))[0], {}
        )
        self.clock[0] = 86400.96
        self.assertTrue(led.reserve("b", 1, 0).res_id)

    def test_principal_rate_does_not_reset_at_midnight(self):
        led = self.ledger(per_principal_rps=1, per_principal_burst=1)
        self.clock[0] = 86399.95
        led.reserve("a", 1, 0)
        self.clock[0] = 86400.05
        with self.assertRaises(RateRejected):
            led.reserve("a", 1, 0)
        self.assertTrue(led.reserve("b", 1, 0).res_id)


class RuntimeConfigTests(unittest.TestCase):
    def test_defaults_stub_and_runtime_reads_env(self):
        cfg = GatewayConfig.from_env({"MAX_BODY_BYTES": "128", "GLOBAL_RPS": "2"})
        self.assertEqual(cfg.max_body_bytes, 128)
        self.assertEqual(cfg.global_rps, 2)
        self.assertEqual(cfg.upstream_mode, "stub")

    def test_invalid_numeric_error_hides_value(self):
        for value in ("nan", "inf", "-1", "secret-canary"):
            with self.assertRaises(ValueError) as ctx:
                GatewayConfig.from_env({"GLOBAL_RPS": value})
            self.assertNotIn(value, str(ctx.exception))

    def test_azure_requires_trusted_identity_and_table(self):
        with self.assertRaises(ValueError):
            GatewayConfig.from_env({"GATEWAY_ENVIRONMENT": "azure"})

    def test_local_factory_uses_atomic_local_admission(self):
        gw = build_gateway(GatewayConfig())
        self.assertIsInstance(gw.admission, AtomicAdmission)
        self.assertIsInstance(gw.admission.rows, LocalRows)
        for r in gw.resources:
            r.close()

    def test_live_requires_approval_and_sourced_pricing(self):
        with self.assertRaises(ValueError):
            GatewayConfig(upstream_mode="live")


class AzureAuthorizationContractTests(unittest.TestCase):
    def test_key_vault_reader_is_present_only_for_live_mode(self):
        source = (
            Path(__file__).resolve().parents[3]
            / "infra/modules/llm-gateway/azure/main.tf"
        ).read_text()
        block = source.split('resource "azurerm_role_assignment" "api_kv_reader" {', 1)[
            1
        ].split("\n}", 1)[0]
        self.assertIn(
            'count                = var.upstream_mode == "live" ? 1 : 0', block
        )
        self.assertIn('role_definition_name = "Key Vault Secrets User"', block)
        self.assertIn("azurerm_key_vault.main.id", block)


class ProviderAdapterTests(unittest.TestCase):
    def config(self, **kw):
        return GatewayConfig(
            upstream_mode="live",
            live_enabled=True,
            price_table_version="test-sourced",
            models={"default": ModelEntry("test/model", 0.1, 0.2)},
            **kw,
        )

    def provider(self, handler, **kw):
        return OpenRouter(
            self.config(**kw), "fixture-only-not-a-key", httpx.MockTransport(handler)
        )

    def test_fixed_destination_and_usage_response(self):
        observed = []

        def handler(request):
            observed.append(request)
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "answer"}}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 3},
                },
            )

        p = self.provider(handler)
        r = p.send("test/model", ({"role": "user", "content": "hello"},), 5, "req-1")
        self.assertEqual(r.kind, "ok")
        self.assertEqual(r.content, "answer")
        self.assertEqual(str(observed[0].url), URL)
        self.assertEqual(observed[0].headers["x-request-id"], "req-1")
        self.assertNotIn("traceparent", observed[0].headers)

    def test_only_provider_trace_context_and_safe_id_propagated(self):
        from opentelemetry.sdk.trace import TracerProvider

        requests = []
        provider = self.provider(
            lambda request: requests.append(request) or httpx.Response(503)
        )
        traces = TracerProvider()
        with traces.get_tracer("test").start_as_current_span("provider.chat") as span:
            provider.send("m", (), 1, "bad\r\nheader")
            self.assertIn(
                format(span.get_span_context().trace_id, "032x"),
                requests[0].headers["traceparent"],
            )
        self.assertNotIn("baggage", requests[0].headers)
        self.assertNotIn("tracestate", requests[0].headers)
        self.assertTrue(requests[0].headers["x-request-id"].startswith("req-"))
        self.assertEqual(
            requests[0].headers["authorization"], "Bearer fixture-only-not-a-key"
        )
        traces.shutdown()

    def test_missing_or_placeholder_key_rejected_before_network(self):
        for key in ("", "DUMMY-NOT-FOR-LIVE", " trailing "):
            with self.assertRaises(ValueError):
                OpenRouter(self.config(), key)

    def test_redirect_not_followed(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(302, headers={"Location": "https://example.invalid"})

        self.assertEqual(self.provider(handler).send("m", (), 1, "r").kind, "http")
        self.assertEqual(len(calls), 1)

    def test_response_cap_and_malformed_payload(self):
        for response in (
            httpx.Response(200, content=b"a" * 101),
            httpx.Response(200, content=b"{"),
        ):
            p = self.provider(lambda _: response, max_response_bytes=100)
            self.assertEqual(p.send("m", (), 1, "r").kind, "http")

    def test_actual_http_error_is_distinct_from_invalid_http_success(self):
        cases = (
            (
                httpx.Response(502, content=b"PRIVATE_PROVIDER_DIAGNOSTIC"),
                502,
                "upstream_http_error",
            ),
            (httpx.Response(200, content=b"{"), 200, "invalid_response"),
            (httpx.Response(200, content=b"x" * 101), 200, "response_too_large"),
        )
        for response, actual_status, reason in cases:
            with self.subTest(reason=reason):
                result = self.provider(lambda _: response, max_response_bytes=100).send(
                    "m", (), 1, "r"
                )
                self.assertEqual(result.status, 502)
                self.assertEqual(result.provider_http_status, actual_status)
                self.assertEqual(result.failure_reason, reason)
                self.assertNotIn("PRIVATE_PROVIDER_DIAGNOSTIC", repr(result))

    def test_reasoning_only_or_empty_completion_is_not_a_success(self):
        for content in (None, ""):
            for finish_reason, category in (
                ("length", "no_text_token_limit"),
                ("stop", "no_text_content"),
            ):
                with self.subTest(content=content, finish_reason=finish_reason):
                    result = self.provider(
                        lambda _: httpx.Response(
                            200,
                            json={
                                "choices": [
                                    {
                                        "finish_reason": finish_reason,
                                        "message": {
                                            "content": content,
                                            "reasoning": "PRIVATE_REASONING",
                                        },
                                    }
                                ],
                                "usage": {
                                    "prompt_tokens": 10,
                                    "completion_tokens": 128,
                                },
                            },
                        )
                    ).send("m", (), 128, "r")
                    self.assertEqual(result.kind, "http")
                    self.assertEqual(result.provider_http_status, 200)
                    self.assertEqual(result.failure_reason, category)
                    self.assertNotIn("PRIVATE_REASONING", repr(result))

    def test_total_deadline_applies_to_slow_transport(self):
        async def handler(request):
            await asyncio.sleep(0.1)
            return httpx.Response(200, json={})

        p = self.provider(handler, deadline_seconds=0.01)
        start = time.monotonic()
        self.assertEqual(p.send("m", (), 1, "r").kind, "timeout")
        self.assertLess(time.monotonic() - start, 0.1)

    def test_presend_and_postsend_failures_distinct(self):
        for fault, kind in (
            (httpx.ConnectError("safe"), "presend_fail"),
            (httpx.ReadError("safe"), "reset_after_send"),
        ):

            def handler(_, fault=fault):
                raise fault

            self.assertEqual(self.provider(handler).send("m", (), 1, "r").kind, kind)

    def test_retry_receives_only_remaining_shared_deadline(self):
        from gateway.upstream import StubResult

        ticks = [0.0]
        remaining = []

        class Provider:
            def send_with_deadline(self, model, messages, cap, rid, budget):
                remaining.append(budget)
                if len(remaining) == 1:
                    ticks[0] += 2
                    return StubResult("presend_fail")
                return StubResult("timeout")

        gateway = make_gateway()
        gateway._clock = lambda: ticks[0]
        gateway._retry_jitter = lambda: 0.05
        gateway._sleep = lambda delay: ticks.__setitem__(0, ticks[0] + delay)
        gateway.upstream = Provider()
        status, _, _ = gateway.chat(auth_headers(user_token(gateway)), chat_body())
        self.assertEqual(status, 504)
        self.assertEqual(remaining[0], 15.0)
        self.assertAlmostEqual(remaining[1], 12.95)


class DeploymentDriverTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[3] / "scripts/deploy.py"
        spec = importlib.util.spec_from_file_location("deployment_driver", path)
        self.driver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.driver)

    def test_default_preflight_never_calls_azure_or_plan(self):
        with patch("sys.argv", ["deploy.py"]), patch.object(self.driver, "run") as run:
            self.assertEqual(self.driver.main(), 0)
            commands = [c.args[0] for c in run.call_args_list]
            self.assertEqual(len(commands), 2)
            self.assertFalse(
                any("az" in c or "plan" in c or "apply" in c for c in commands)
            )

    def test_plan_and_apply_refused_without_separate_approval(self):
        for action in ("plan", "bind-principal", "apply"):
            with (
                patch(
                    "sys.argv",
                    [
                        "deploy.py",
                        action,
                        "--subscription",
                        "s",
                        "--tenant",
                        "t",
                        "--resource-group",
                        "r",
                    ],
                ),
                patch.dict("os.environ", {}, clear=True),
                patch.object(self.driver, "run") as run,
            ):
                with self.assertRaises(SystemExit):
                    self.driver.main()
                run.assert_not_called()

    def test_wrong_account_refused_before_terraform(self):
        with (
            patch(
                "sys.argv",
                [
                    "deploy.py",
                    "plan",
                    "--subscription",
                    "s",
                    "--tenant",
                    "t",
                    "--resource-group",
                    "r",
                ],
            ),
            patch.dict("os.environ", {"ODYN_PLAN_APPROVED": "1"}, clear=True),
            patch.object(self.driver, "run") as run,
        ):
            run.return_value.stdout = json.dumps({"id": "wrong", "tenantId": "t"})
            with self.assertRaises(SystemExit):
                self.driver.main()
            self.assertEqual(run.call_count, 1)

    def test_plan_uses_private_explicit_application_tfvars(self):
        import tempfile
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as directory:
            tfvars = Path(directory) / "application.tfvars.json"
            tfvars.write_text("{}")
            plan = Path(directory) / "plan.tfplan"
            with (
                patch(
                    "sys.argv",
                    [
                        "deploy.py",
                        "plan",
                        "--subscription",
                        "s",
                        "--tenant",
                        "t",
                        "--resource-group",
                        "r",
                    ],
                ),
                patch.dict("os.environ", {"ODYN_PLAN_APPROVED": "1"}, clear=True),
                patch.object(self.driver, "TFVARS", tfvars),
                patch.object(self.driver, "PLAN", plan),
                patch.object(
                    self.driver, "RECEIPT", Path(directory) / "plan.receipt.json"
                ),
                patch.object(self.driver, "config_hash", return_value="config"),
                patch.object(
                    self.driver,
                    "principal_binding",
                    return_value={"owner_effective": True, "sha256": "principal"},
                ),
                patch.object(self.driver, "run") as run,
            ):

                def fake_run(command, capture=False):
                    if command[0] == "az":
                        return SimpleNamespace(
                            stdout=json.dumps({"id": "s", "tenantId": "t"})
                        )
                    if "plan" in command:
                        plan.write_bytes(b"mock-plan")
                        return SimpleNamespace(stdout="plan saved")
                    return SimpleNamespace(
                        stdout=json.dumps(
                            {
                                "resource_changes": [
                                    {
                                        "type": "azurerm_resource_group",
                                        "change": {"after": {"name": "r"}},
                                    }
                                ]
                            }
                        )
                    )

                run.side_effect = fake_run
                self.assertEqual(self.driver.main(), 0)
                plan_command = run.call_args_list[1].args[0]
                self.assertIn("-var-file=" + str(tfvars), plan_command)
                receipt = json.loads(
                    (Path(directory) / "plan.receipt.json").read_text()
                )
                self.assertEqual(receipt["principal_binding"]["sha256"], "principal")

    def test_bind_principal_requires_verified_owner_and_preserves_plan_hash(self):
        import hashlib
        import tempfile
        from types import SimpleNamespace

        for is_owner in (True, False):
            with (
                self.subTest(is_owner=is_owner),
                tempfile.TemporaryDirectory() as directory,
            ):
                plan = Path(directory) / "plan.tfplan"
                receipt = plan.with_suffix(".receipt.json")
                plan.write_bytes(b"plan")
                digest = hashlib.sha256(plan.read_bytes()).hexdigest()
                receipt.write_text(
                    json.dumps(
                        {
                            "subscription": "s",
                            "tenant": "t",
                            "resource_group": "r",
                            "config": "config",
                            "sha256": digest,
                        }
                    )
                )
                with (
                    patch(
                        "sys.argv",
                        [
                            "deploy.py",
                            "bind-principal",
                            "--subscription",
                            "s",
                            "--tenant",
                            "t",
                            "--resource-group",
                            "r",
                        ],
                    ),
                    patch.dict("os.environ", {"ODYN_PLAN_APPROVED": "1"}, clear=True),
                    patch.object(self.driver, "PLAN", plan),
                    patch.object(self.driver, "RECEIPT", receipt),
                    patch.object(self.driver, "config_hash", return_value="config"),
                    patch.object(
                        self.driver,
                        "principal_binding",
                        return_value={"owner_effective": is_owner, "sha256": "owner"},
                    ),
                    patch.object(
                        self.driver,
                        "run",
                        return_value=SimpleNamespace(
                            stdout=json.dumps({"id": "s", "tenantId": "t"})
                        ),
                    ) as run,
                    patch.object(self.driver.os, "umask"),
                ):
                    if is_owner:
                        self.assertEqual(self.driver.main(), 0)
                        self.assertEqual(
                            json.loads(receipt.read_text())["principal_binding"][
                                "sha256"
                            ],
                            "owner",
                        )
                    else:
                        with self.assertRaises(SystemExit):
                            self.driver.main()
                        self.assertNotIn(
                            "principal_binding", json.loads(receipt.read_text())
                        )
                    self.assertEqual(
                        hashlib.sha256(plan.read_bytes()).hexdigest(), digest
                    )
                    self.assertEqual(run.call_count, 1)

    def test_saved_apply_binds_hash_destination_principal_and_owner_role(self):
        import hashlib
        import tempfile
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as directory:
            plan = Path(directory) / "private.tfplan"
            receipt = plan.with_suffix(".receipt.json")
            plan.write_bytes(b"synthetic-plan")
            digest = hashlib.sha256(plan.read_bytes()).hexdigest()
            baseline = {
                "subscription": "s",
                "tenant": "t",
                "resource_group": "r",
                "config": "config",
                "sha256": digest,
                "principal_binding": {
                    "owner_effective": True,
                    "sha256": "principal",
                },
            }
            for fault in (
                "destination",
                "config",
                "plan",
                "review",
                "confirmation",
                "missing_principal",
                "principal",
                "owner_role",
                "none",
            ):
                record = dict(baseline)
                if fault == "destination":
                    record["tenant"] = "wrong"
                if fault == "config":
                    record["config"] = "stale"
                if fault == "plan":
                    record["sha256"] = "wrong"
                if fault == "missing_principal":
                    record.pop("principal_binding")
                receipt.write_text(json.dumps(record))
                with (
                    patch(
                        "sys.argv",
                        [
                            "deploy.py",
                            "apply",
                            "--subscription",
                            "s",
                            "--tenant",
                            "t",
                            "--resource-group",
                            "r",
                        ],
                    ),
                    patch.dict(
                        "os.environ",
                        {
                            "ODYN_DEPLOY_APPROVED": "1",
                            "ODYN_REVIEWED_PLAN_SHA256": "wrong"
                            if fault == "review"
                            else digest,
                        },
                        clear=True,
                    ),
                    patch.object(self.driver, "PLAN", plan),
                    patch.object(self.driver, "RECEIPT", receipt),
                    patch.object(self.driver, "config_hash", return_value="config"),
                    patch.object(
                        self.driver,
                        "principal_binding",
                        return_value={
                            "owner_effective": fault != "owner_role",
                            "sha256": "changed"
                            if fault == "principal"
                            else "principal",
                        },
                    ),
                    patch.object(
                        self.driver,
                        "run",
                        return_value=SimpleNamespace(
                            stdout=json.dumps({"id": "s", "tenantId": "t"})
                        ),
                    ) as run,
                    patch(
                        "builtins.input",
                        return_value="wrong" if fault == "confirmation" else "r",
                    ),
                    patch.object(self.driver.os, "umask"),
                ):
                    if fault != "none":
                        with self.assertRaises(SystemExit):
                            self.driver.main()
                        self.assertEqual(run.call_count, 1)
                    else:
                        self.assertEqual(self.driver.main(), 0)
                        self.assertEqual(run.call_count, 2)
                        command = run.call_args.args[0]
                        self.assertEqual(command[-1], str(plan))
                        self.assertIn("apply", command)
                        self.assertNotIn("plan", command)


class TrustedKeyCacheTests(unittest.TestCase):
    def test_failed_refresh_rate_bounded_and_stale_keys_expire(self):
        ticks = [0.0]
        cache = TrustedJwks(
            "https://example.invalid/keys",
            httpx.MockTransport(lambda _: httpx.Response(503)),
            clock=lambda: ticks[0],
        )
        cache.keys = {"cached": object()}
        cache._last_success = 0.0
        ticks[0] = 301
        for _ in range(20):
            cache.ensure_fresh()
        self.assertEqual(cache.fetch_count, 1)
        self.assertIn("cached", cache.keys)
        ticks[0] = 901
        cache.ensure_fresh()
        self.assertEqual(cache.keys, {})
        cache.close()

    def test_valid_rsa_jwks_and_rotation(self):
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        jwk.update(kid="one", use="sig", alg="RS256")
        ticks = [0.0]
        cache = TrustedJwks(
            "https://example.invalid/keys",
            httpx.MockTransport(lambda _: httpx.Response(200, json={"keys": [jwk]})),
            clock=lambda: ticks[0],
        )
        cache.ensure_fresh()
        self.assertIn("one", cache.keys)
        jwk["kid"] = "two"
        ticks[0] = 301
        cache.ensure_fresh()
        self.assertNotIn("one", cache.keys)
        self.assertIn("two", cache.keys)
        cache.close()


class ExportedTelemetryTests(unittest.TestCase):
    def test_replica_identity_is_attached_to_resources_and_redacted_logs(self):
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        replica_name = "odyn-gateway--revision-abc123-xyz789"
        exporter, reader = InMemorySpanExporter(), InMemoryMetricReader()
        with patch.dict("os.environ", {"CONTAINER_APP_REPLICA_NAME": replica_name}):
            telemetry = ExportedTelemetry("http://127.0.0.1:4318", exporter, reader)
            try:
                telemetry.log(request_id="req-replica", status=200)
                self.assertEqual(telemetry.events[-1]["replica_name"], replica_name)
                telemetry.record_response(200, 0.01)
                resource_metrics = reader.get_metrics_data().resource_metrics
                self.assertEqual(len(resource_metrics), 1)
                self.assertEqual(
                    resource_metrics[0].resource.attributes["service.instance.id"],
                    replica_name,
                )
                with telemetry.span("gateway.chat"):
                    pass
                telemetry.traces.force_flush()
                spans = exporter.get_finished_spans()
                self.assertEqual(len(spans), 1)
                self.assertEqual(
                    spans[0].resource.attributes["service.instance.id"],
                    replica_name,
                )
            finally:
                telemetry.close()

    def test_real_sdk_spans_metrics_and_redaction(self):
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        exporter, reader = InMemorySpanExporter(), InMemoryMetricReader()
        telemetry = ExportedTelemetry("http://127.0.0.1:4318", exporter, reader)
        gw = make_gateway()
        gw.telemetry = telemetry
        try:
            self.assertEqual(gw.chat(auth_headers(user_token(gw)), chat_body())[0], 200)
            telemetry.traces.force_flush()
            spans = exporter.get_finished_spans()
            self.assertEqual({s.name for s in spans}, {"gateway.chat", "provider.chat"})
            self.assertEqual(len({s.context.trace_id for s in spans}), 1)
            telemetry.log(
                request_id="req-1",
                principal="personal-canary",
                prompt="prompt-canary",
                key="key-canary",
            )
            output = telemetry.dump_last()
            for secret in ("personal-canary", "prompt-canary", "key-canary"):
                self.assertNotIn(secret, output)
            names = {
                m.name
                for resource in reader.get_metrics_data().resource_metrics
                for scope in resource.scope_metrics
                for m in scope.metrics
            }
            self.assertIn("gateway.requests", names)
            self.assertIn("gateway.duration", names)
            self.assertTrue(
                {
                    "gateway.upstream.duration",
                    "gateway.upstream.requests",
                    "gateway.tokens",
                }.issubset(names)
            )
            for resource in reader.get_metrics_data().resource_metrics:
                for scope in resource.scope_metrics:
                    for metric in scope.metrics:
                        if metric.name != "gateway.events":
                            for point in metric.data.data_points:
                                self.assertEqual(point.attributes["route"], "/v1/chat")
        finally:
            telemetry.close()

    def test_unsafe_exporter_uri_rejected(self):
        for endpoint in (
            "http://example.invalid",
            "https://user:password@example.invalid",
            "https://example.invalid/?key=value",
        ):
            with self.assertRaises(ValueError):
                ExportedTelemetry(endpoint)

    def test_w3c_parent_propagates_without_identity_or_auth_attributes(self):
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        exporter = InMemorySpanExporter()
        telemetry = ExportedTelemetry(
            "http://127.0.0.1:4318", exporter, InMemoryMetricReader()
        )
        try:
            with (
                telemetry.span(
                    "gateway.chat",
                    headers={
                        "traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01",
                        "Authorization": "secret-canary",
                        "baggage": "personal-canary",
                    },
                ),
                telemetry.span("provider.chat"),
            ):
                pass
            telemetry.traces.force_flush()
            spans = exporter.get_finished_spans()
            self.assertEqual(
                {span.context.trace_id for span in spans}, {int("1" * 32, 16)}
            )
            root = next(span for span in spans if span.name == "gateway.chat")
            self.assertEqual(root.parent.span_id, int("2" * 16, 16))
            self.assertNotIn("secret-canary", str(root.attributes))
            self.assertNotIn("personal-canary", str(root.attributes))
        finally:
            telemetry.close()


class BreakerConcurrencyTests(unittest.TestCase):
    def test_success_resets_consecutive_failure_streak(self):
        from gateway.resilience import Breaker

        breaker = Breaker(threshold=2)
        breaker.record("timeout", 1)
        breaker.success(2)
        breaker.record("timeout", 3)
        self.assertFalse(breaker.is_open(3))
        breaker.record("timeout", 4)
        self.assertTrue(breaker.is_open(4))
        breaker.success(5)
        self.assertTrue(breaker.is_open(5))
        breaker.success(35)
        self.assertEqual(breaker.failures, 0)

    def test_failure_updates_thread_safe_and_history_bounded(self):
        from concurrent.futures import ThreadPoolExecutor

        from gateway.resilience import Breaker

        breaker = Breaker(threshold=1)
        with ThreadPoolExecutor(max_workers=20) as pool:
            list(pool.map(lambda _: breaker.record("timeout", 1), range(300)))
        self.assertEqual(breaker.failures, 300)
        self.assertLessEqual(len(breaker.state_changes), 256)

    def test_only_one_half_open_probe_and_owner_releases(self):
        from concurrent.futures import ThreadPoolExecutor

        from gateway.resilience import Breaker

        breaker = Breaker(threshold=1, cool_down_s=1)
        breaker.record("timeout", 0)
        self.assertFalse(breaker.is_open(2))
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertTrue(all(pool.map(lambda _: breaker.is_open(2), range(4))))
            list(pool.map(lambda _: breaker.finish_probe(), range(4)))
        self.assertTrue(breaker.is_open(2))
        breaker.finish_probe()
        self.assertFalse(breaker.is_open(2))
        breaker.success(2)
        self.assertFalse(breaker.is_open(2))


class SsoClientTests(unittest.TestCase):
    def module(self, name):
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).resolve().parents[3] / "scripts" / (name + ".py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_real_sso_demo_requires_explicit_approval(self):
        module = self.module("sso-demo")
        with (
            patch("sys.argv", ["sso-demo.py", "human"]),
            patch.object(module, "human") as human,
        ):
            with self.assertRaises(SystemExit):
                module.main()
            human.assert_not_called()

    def test_maintained_library_generates_s256_pkce_and_rejects_wrong_state(self):
        from urllib.parse import parse_qs, urlsplit

        from msal.oauth2cli.oauth2 import Client

        client = Client(
            {
                "authorization_endpoint": "https://example.invalid/authorize",
                "token_endpoint": "https://example.invalid/token",
            },
            "synthetic-client",
        )
        flow = client.initiate_auth_code_flow(
            scope=["chat.invoke"], redirect_uri="http://localhost:8400"
        )
        parameters = parse_qs(urlsplit(flow["auth_uri"]).query)
        self.assertEqual(parameters["code_challenge_method"], ["S256"])
        self.assertGreaterEqual(len(flow["code_verifier"]), 43)
        with self.assertRaises(ValueError):
            client.obtain_token_by_auth_code_flow(
                flow, {"state": "wrong", "code": "synthetic-code"}
            )

    def test_token_storage_is_atomic_private_and_never_printed(self):
        import contextlib
        import io
        import tempfile
        import uuid

        module = self.module("sso-demo")
        with tempfile.TemporaryDirectory() as location:
            root = Path(location)
            directory = root / ".secrets/sso"
            directory.mkdir(parents=True)
            config = directory / "config.json"
            config.write_text(
                json.dumps(
                    {
                        key: str(uuid.uuid4())
                        for key in (
                            "tenant_id",
                            "api_app_id",
                            "human_client_id",
                            "machine_client_id",
                        )
                    }
                )
            )
            config.chmod(0o600)
            token = directory / "human-token.json"
            token.write_text("old")
            token.chmod(0o644)
            output = io.StringIO()
            with (
                patch.object(module, "ROOT", root),
                patch("sys.argv", ["sso-demo.py", "human", "--approved-entra-demo"]),
                patch.object(
                    module,
                    "human",
                    return_value={
                        "access_token": "private-jwt-canary",
                        "expires_in": 60,
                    },
                ),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(module.main(), 0)
            self.assertEqual(token.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                json.loads(token.read_text())["access_token"], "private-jwt-canary"
            )
            self.assertNotIn("private-jwt-canary", output.getvalue())
            self.assertEqual(list(directory.glob(".token-*")), [])


class ProviderSpanOutcomeTests(unittest.TestCase):
    def test_invalid_http_success_preserves_status_and_safe_failure_category(self):
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        exporter = InMemorySpanExporter()
        telemetry = ExportedTelemetry(
            "http://127.0.0.1:4318", exporter, InMemoryMetricReader()
        )
        config = GatewayConfig(
            upstream_mode="live",
            live_enabled=True,
            price_table_version="fixture",
            models={"default": ModelEntry("test/model", 0, 0)},
        )
        gateway = make_gateway()
        gateway.telemetry = telemetry
        gateway.upstream = OpenRouter(
            config,
            "fixture-only-not-a-key",
            httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "finish_reason": "length",
                                "message": {
                                    "content": None,
                                    "reasoning": "PRIVATE_REASONING",
                                },
                            }
                        ]
                    },
                ),
            ),
        )
        try:
            status, body, _ = gateway.chat(
                auth_headers(user_token(gateway)), chat_body()
            )
            self.assertEqual(status, 502)
            telemetry.traces.force_flush()
            dependency = next(
                s for s in exporter.get_finished_spans() if s.name == "provider.chat"
            )
            self.assertEqual(dependency.attributes["http.response.status_code"], 200)
            self.assertEqual(dependency.attributes["provider.http_status"], 200)
            self.assertEqual(
                dependency.attributes["provider.failure_reason"], "no_text_token_limit"
            )
            provider_event = next(
                event
                for event in telemetry.events
                if event.get("provider_http_status") == 200
            )
            self.assertEqual(provider_event["failure_reason"], "no_text_token_limit")
            self.assertEqual(telemetry.events[-1]["status"], 502)
            self.assertIn("latency_ms", telemetry.events[-1])
            self.assertNotIn("PRIVATE_REASONING", repr(dependency.attributes))
            self.assertNotIn("PRIVATE_REASONING", repr(telemetry.events))
            self.assertNotIn("no_text_token_limit", json.dumps(body))
        finally:
            telemetry.close()

    def test_real_sdk_marks_failure_dependencies_without_sensitive_content(self):
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )
        from opentelemetry.trace import StatusCode

        for mode, status, outcome in (
            ("ok", 200, "ok"),
            ("http_500", 502, "http"),
            ("http_429", 503, "http"),
            ("timeout", 504, "timeout"),
            ("reset_after_send", 502, "reset_after_send"),
        ):
            with self.subTest(mode=mode):
                exporter = InMemorySpanExporter()
                telemetry = ExportedTelemetry(
                    "http://127.0.0.1:4318", exporter, InMemoryMetricReader()
                )
                gateway = make_gateway()
                gateway.upstream.mode = mode
                gateway.telemetry = telemetry
                try:
                    self.assertEqual(
                        gateway.chat(auth_headers(user_token(gateway)), chat_body())[0],
                        status,
                    )
                    telemetry.traces.force_flush()
                    dependency = next(
                        s
                        for s in exporter.get_finished_spans()
                        if s.name == "provider.chat"
                    )
                    self.assertEqual(dependency.attributes["provider.outcome"], outcome)
                    self.assertEqual(
                        dependency.status.status_code,
                        StatusCode.UNSET if outcome == "ok" else StatusCode.ERROR,
                    )
                    self.assertIn("request_id", dependency.attributes)
                    for field in ("Authorization", "prompt", "completion", "principal"):
                        self.assertNotIn(field, dependency.attributes)
                finally:
                    telemetry.close()

    def test_changed_allowlist_reaches_dispatch_and_old_alias_is_rejected(self):
        from gateway.config import ModelEntry

        gateway = make_gateway(models={"new": ModelEntry("fixture/new", 0, 0)})
        status, _, _ = gateway.chat(auth_headers(user_token(gateway)), chat_body())
        self.assertEqual(status, 422)
        self.assertEqual(gateway.upstream.calls, 0)
        status, _, _ = gateway.chat(
            auth_headers(user_token(gateway)), chat_body(model="new")
        )
        self.assertEqual(status, 200)
        self.assertEqual(gateway.upstream.calls, 1)
