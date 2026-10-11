"""Gateway and infrastructure contract regressions."""

import json
import unittest
from pathlib import Path

from gateway.auth import normalize_claims
from gateway.config import GatewayConfig

ROOT = Path(__file__).resolve().parents[3]


class GatewayConfigurationContractTests(unittest.TestCase):
    def live_environment(self, **overrides):
        values = {
            "UPSTREAM_MODE": "live",
            "ODYN_LIVE_UPSTREAM_APPROVED": "1",
            "PRICE_TABLE_VERSION": "reviewed-fixture-source-2026-10-10",
            "MODEL_PRICES_JSON": json.dumps(
                {
                    "reviewed": {
                        "provider_model_id": "fixture/model",
                        "input_usd_per_1k": 0.01,
                        "output_usd_per_1k": 0.02,
                    }
                }
            ),
        }
        values.update(overrides)
        return values

    def test_complete_live_configuration_uses_reviewed_allowlist(self):
        config = GatewayConfig.from_env(self.live_environment())
        self.assertEqual(set(config.models), {"reviewed"})
        self.assertEqual(config.models["reviewed"].provider_model_id, "fixture/model")
        self.assertEqual(config.models["reviewed"].output_usd_per_1k, 0.02)

    def test_live_approval_cannot_be_inferred_from_price_or_secret(self):
        with self.assertRaises(ValueError):
            GatewayConfig.from_env(
                self.live_environment(ODYN_LIVE_UPSTREAM_APPROVED="0")
            )

    def test_live_without_explicit_model_prices_is_rejected(self):
        env = self.live_environment()
        del env["MODEL_PRICES_JSON"]
        with self.assertRaises(ValueError):
            GatewayConfig.from_env(env)

    def test_live_demo_pricing_is_rejected(self):
        with self.assertRaises(ValueError):
            GatewayConfig.from_env(self.live_environment(PRICE_TABLE_VERSION="demo"))

    def test_empty_or_negative_model_configuration_rejected(self):
        for models in (
            {},
            {
                "x": {
                    "provider_model_id": "m",
                    "input_usd_per_1k": -1,
                    "output_usd_per_1k": 0,
                }
            },
        ):
            with self.subTest(models=models), self.assertRaises(ValueError):
                GatewayConfig.from_env(
                    self.live_environment(MODEL_PRICES_JSON=json.dumps(models))
                )

    def test_tf_runtime_environment_contains_live_contract(self):
        # Static contract check; Terraform validate independently checks syntax/types.
        source = (ROOT / "infra/modules/llm-gateway/azure/main.tf").read_text()
        for field in (
            "ODYN_LIVE_UPSTREAM_APPROVED",
            "PRICE_TABLE_VERSION",
            "MODEL_PRICES_JSON",
            "CORS_ORIGINS",
        ):
            self.assertIn(field, source)
        self.assertIn("jsonencode(local.model_config)", source)
        self.assertIn("provider_model_id = model", source)
        self.assertIn("var.model_prices[alias]", source)

    def test_shared_adapter_exposes_only_supported_algorithm(self):
        source = (ROOT / "infra/modules/llm-gateway/variables.tf").read_text()
        self.assertIn('var.allowed_algorithms == tolist(["RS256"])', source)
        for path in ("variables.tf", "main.tf", "azure/variables.tf"):
            self.assertNotIn(
                'variable "telemetry_endpoint"',
                (ROOT / "infra/modules/llm-gateway" / path).read_text(),
            )

    def test_no_scope_role_confusion_for_delegated_tokens(self):
        claims, reason = normalize_claims(
            {
                "exp": 100,
                "tid": "t",
                "oid": "u",
                "azp": "client",
                "roles": ["Chat.Invoke.Application"],
                "scp": "chat.invoke",
            },
            1,
            0,
            "chat.invoke",
            "Chat.Invoke.Application",
        )
        self.assertIsNone(reason)
        self.assertEqual(claims.principal, "t:u")
        self.assertFalse(claims.is_app_only)


class ObservabilityContractTests(unittest.TestCase):
    def setUp(self):
        self.workbook = json.loads((ROOT / "infra/telemetry/workbook.json").read_text())
        self.items = {item["name"]: item["content"] for item in self.workbook["items"]}

    def test_dashboard_separates_counts_seconds_and_tokens(self):
        self.assertIn("gateway.tokens", self.items["token-usage"]["query"])
        self.assertNotIn(
            "gateway.upstream.duration", self.items["token-usage"]["query"]
        )
        self.assertIn("MeanSeconds", self.items["upstream-latency"]["query"])
        self.assertIn(
            "gateway.upstream.requests", self.items["upstream-request-counts"]["query"]
        )

    def test_dashboard_uses_emitted_route_and_provider_dependency_dimensions(self):
        requests = self.items["Request rate and errors"]["query"]
        latency = self.items["Latency percentiles"]["query"]
        provider = self.items["Dependency outcomes"]["query"]
        self.assertIn('customDimensions["http.route"]', requests)
        self.assertIn("by Route, bin(timestamp, 1m)", requests)
        self.assertIn('customDimensions["http.route"]', latency)
        self.assertIn("P50=percentile(duration, 50)", latency)
        self.assertIn("P95=percentile(duration, 95)", latency)
        self.assertIn("P99=percentile(duration, 99)", latency)
        self.assertIn('name == "POST" and target == "POST"', provider)

    def test_accounting_delta_events_use_interval_sum(self):
        query = self.items["Security and accounting events"]["query"]
        self.assertIn("sum(valueSum)", query)
        self.assertNotIn("max(value)", query)

    def test_alert_runbooks_include_safe_first_actions(self):
        content = self.items["alert-runbooks"]["json"]
        for runbook in ("5xx", "auth-spike", "429", "budget", "table-contention"):
            text = (ROOT / "docs/runbooks" / (runbook + ".md")).read_text().strip()
            self.assertIn(text, content)
        alerts = (ROOT / "infra/modules/llm-gateway/azure/alerts.tf").read_text()
        self.assertIn("https://portal.azure.com/#resource/", alerts)

    def test_embedded_runbooks_contain_current_operational_guidance(self):
        content = self.items["alert-runbooks"]["json"].lower()
        self.assertIn("monitorcondition", content)
        self.assertIn("user acknowledgement", content)
        # Exact windows live as code in alerts.tf; workbook states principles.
        alerts = (ROOT / "infra/modules/llm-gateway/azure/alerts.tf").read_text()
        self.assertIn('window_size         = "PT15M"', alerts)
        self.assertIn('window_duration         = "PT15M"', alerts)
        self.assertIn("retry-after", content)
        self.assertIn("fail-closed", content)
        self.assertIn("bearer tokens", content)

    def test_latency_alert_query_returns_numeric_seconds(self):
        alerts = (ROOT / "infra/modules/llm-gateway/azure/alerts.tf").read_text()
        latency = alerts.split("latency = {", 1)[1].split("\n    }", 1)[0]
        self.assertIn("percentile(duration,95)", latency)
        self.assertNotIn("/1s", latency)
        self.assertIn("threshold = 2", latency)

    def test_email_actions_require_explicit_receiver_configuration(self):
        shared = (ROOT / "infra/modules/llm-gateway/variables.tf").read_text()
        azure = (ROOT / "infra/modules/llm-gateway/azure/main.tf").read_text()
        env = (ROOT / "infra/envs/dev/azure/main.tf").read_text()
        self.assertIn('variable "alert_notifications_enabled"', shared)
        self.assertIn(
            'var.alert_notifications_enabled && var.alert_email != "" ? 1 : 0', azure
        )
        self.assertIn(
            "alert_notifications_enabled = var.alert_notifications_enabled", env
        )

    def test_every_configured_alert_is_exported(self):
        outputs = (ROOT / "infra/modules/llm-gateway/azure/outputs.tf").read_text()
        for rule in ("http_5xx", "gateway_status", "application"):
            self.assertIn(rule, outputs)

    def test_table_conflict_alert_uses_aggregate_admission_event(self):
        admission = (ROOT / "app/src/gateway/admission.py").read_text()
        runtime = (ROOT / "app/src/gateway/runtime.py").read_text()
        alerts = (ROOT / "infra/modules/llm-gateway/azure/alerts.tf").read_text()
        self.assertIn('target["admission_conflict"]', admission)
        self.assertLess(
            runtime.index("telemetry = ExportedTelemetry(config.telemetry_endpoint)"),
            runtime.index("metrics=telemetry.metrics"),
        )
        conflict = alerts.split("storage_conflicts = {", 1)[1].split("\n    }", 1)[0]
        self.assertIn("admission_conflict", conflict)
        self.assertIn("threshold = 25", conflict)

    def test_backend_readiness_is_not_static_edge_health(self):
        edge = (ROOT / "infra/modules/llm-gateway/azure/edge.tf").read_text()
        self.assertIn('template="/readyz"', edge)
        self.assertIn("<base/>", edge)
        self.assertIn("authenticated", edge)


class AzureConfigurationRegressionTests(unittest.TestCase):
    def test_minimum_replicas_are_explicitly_bounded_for_cloud_acceptance(self):
        variables = (ROOT / "infra/modules/llm-gateway/azure/variables.tf").read_text()
        main = (ROOT / "infra/modules/llm-gateway/azure/main.tf").read_text()
        shared_main = (ROOT / "infra/modules/llm-gateway/main.tf").read_text()
        environment = (ROOT / "infra/envs/dev/azure/main.tf").read_text()
        self.assertIn('variable "min_replicas"', variables)
        self.assertIn("default = 1", variables)
        self.assertIn("var.min_replicas >= 1 && var.min_replicas <= 2", variables)
        self.assertIn("floor(var.min_replicas) == var.min_replicas", variables)
        self.assertRegex(main, r"min_replicas\s*=\s*var\.min_replicas")
        self.assertRegex(shared_main, r"min_replicas\s*=\s*var\.min_replicas")
        self.assertRegex(environment, r"min_replicas\s*=\s*var\.min_replicas")
        self.assertIn("max_replicas = 2", main)

    def test_static_edge_health_sets_hsts_and_no_store(self):
        edge = (ROOT / "infra/modules/llm-gateway/azure/edge.tf").read_text()
        health = edge.split(
            'resource "azurerm_api_management_api_operation_policy" "health"',
            1,
        )[1].split('\nresource "', 1)[0]
        self.assertIn('name="Strict-Transport-Security"', health)
        self.assertIn("max-age=31536000", health)
        self.assertIn('name="Cache-Control"', health)
        self.assertIn("no-store", health)

    def test_apim_rejects_legacy_tls_and_restricts_gateway_origin(self):
        main = (ROOT / "infra/modules/llm-gateway/azure/main.tf").read_text()
        network = (ROOT / "infra/modules/llm-gateway/azure/network.tf").read_text()
        policy = (ROOT / "infra/modules/llm-gateway/azure/policy.xml.tftpl").read_text()
        apim = main.split('resource "azurerm_api_management" "main"', 1)[1].split(
            '\nresource "', 1
        )[0]
        for protocol in (
            "enable_frontend_tls10 = false",
            "enable_frontend_tls11 = false",
            "enable_backend_tls10  = false",
            "enable_backend_tls11  = false",
        ):
            self.assertIn(protocol, apim)
        self.assertIn('source_address_prefix      = "AzureFrontDoor.Backend"', network)
        self.assertIn('name                       = "DenyDirectGateway"', network)
        self.assertIn("${frontdoor_id}", policy)
        self.assertIn("X-Azure-FDID", policy)

    def azure_environment(self, endpoint):
        tenant = "00000000-0000-0000-0000-000000000001"
        return {
            "GATEWAY_ENVIRONMENT": "azure",
            "IDP_ISSUER_URL": f"https://login.microsoftonline.com/{tenant}/v2.0",
            "IDP_AUDIENCE": "fixture-api",
            "JWKS_URL": f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys",
            "TABLE_ENDPOINT": endpoint,
            "AZURE_CLIENT_ID": "fixture-managed-identity",
            "UPSTREAM_MODE": "stub",
        }

    def test_azure_table_endpoint_accepts_provider_trailing_slash(self):
        for endpoint in (
            "https://example.table.core.windows.net",
            "https://example.table.core.windows.net/",
        ):
            config = GatewayConfig.from_env(self.azure_environment(endpoint))
            self.assertEqual(
                config.table_endpoint, "https://example.table.core.windows.net"
            )
            self.assertEqual(config.runtime_environment, "azure")

    def test_azure_endpoint_rejects_paths_queries_userinfo_and_non_https(self):
        for endpoint in (
            "https://example.table.core.windows.net//",
            "https://example.table.core.windows.net/path",
            "https://example.table.core.windows.net/?q=x",
            "https://user@example.table.core.windows.net/",
            "http://example.table.core.windows.net/",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                GatewayConfig.from_env(self.azure_environment(endpoint))

    def test_profile_delegation_readiness_and_log_cap_are_explicit(self):
        source = (ROOT / "infra/modules/llm-gateway/azure/main.tf").read_text()
        for text in (
            'workload_profile_type = "Consumption"',
            'workload_profile_name = "Consumption"',
            "readiness_probe {",
            'path = "/readyz"',
            "liveness_probe {",
            "startup_probe {",
            "daily_quota_gb      = 0.1",
            "infrastructure_resource_group_name",
        ):
            self.assertIn(" ".join(text.split()), " ".join(source.split()))
        self.assertRegex(
            " ".join(source.split()),
            r"startup_probe\s*\{[^}]*failure_count_threshold\s*=\s*30",
        )

    def test_azure_front_door_redirect_and_workbook_provider_contracts(self):
        edge = (ROOT / "infra/modules/llm-gateway/azure/edge.tf").read_text()
        route = edge.split('resource "azurerm_cdn_frontdoor_route" "main"', 1)[1]
        route = " ".join(route.split())
        self.assertRegex(route, r'supported_protocols\s*=\s*\["Http", "Https"\]')
        self.assertRegex(route, r"https_redirect_enabled\s*=\s*true")

        workbook = (
            ROOT / "infra/modules/llm-gateway/azure/observability.tf"
        ).read_text()
        self.assertRegex(
            " ".join(workbook.split()),
            r"source_id\s*=\s*lower\(azurerm_application_insights\.main\.id\)",
        )

    def test_apim_unknown_operation_is_not_misreported_as_upstream_failure(self):
        policy = (ROOT / "infra/modules/llm-gateway/azure/policy.xml.tftpl").read_text()
        compact = " ".join(policy.split())
        self.assertIn(
            "context.LastError.Reason == &quot;OperationNotFound&quot; ? 404", compact
        )
        self.assertIn(
            'context.LastError.Reason == "OperationNotFound" ? "not_found"',
            compact,
        )
        self.assertIn('"upstream_unavailable"', compact)

    def test_email_disabled_alerts_do_not_create_empty_action_blocks(self):
        alerts = (ROOT / "infra/modules/llm-gateway/azure/alerts.tf").read_text()
        compact = " ".join(alerts.split())
        self.assertIn(
            "for_each = length(azurerm_monitor_action_group.email) > 0 ? [true] : []",
            compact,
        )

    def test_table_diagnostics_match_explicit_disabled_capacity_category(self):
        main = (ROOT / "infra/modules/llm-gateway/azure/main.tf").read_text()
        compact = " ".join(main.split())
        self.assertRegex(
            compact,
            (
                r'category = "Transaction" enabled = true '
                r"retention_policy \{ enabled = false days = 0 \}"
            ),
        )
        self.assertRegex(
            compact,
            (
                r'category = "Capacity" enabled = false '
                r"retention_policy \{ enabled = false days = 0 \}"
            ),
        )
