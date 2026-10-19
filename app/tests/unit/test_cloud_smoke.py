"""Public smoke checks reject redirects and never send credentials or real chat."""

import importlib.util
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

SPEC = importlib.util.spec_from_file_location(
    "cloud_smoke", Path(__file__).resolve().parents[3] / "scripts/cloud-smoke.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CloudSmokeTests(unittest.TestCase):
    def endpoint(self):
        return {
            "id": (
                "/subscriptions/test-sub/resourceGroups/rg-odyn-gateway-auto19"
                "/providers/Microsoft.Cdn/profiles/fd-odyn-gateway-auto19"
                "/afdEndpoints/fd-odyn-gateway-auto19"
            ),
            "hostName": "fd-odyn-auto19.example.z03.azurefd.net",
            "enabledState": "Enabled",
        }

    def test_resolves_selected_deployment_not_configured_demo_url(self):
        endpoint = self.endpoint()
        with (
            patch.dict(os.environ, {"AZURE_SUBSCRIPTION_ID": "test-sub"}),
            patch.object(
                MODULE.subprocess,
                "run",
                return_value=SimpleNamespace(stdout=json.dumps([endpoint])),
            ) as run,
        ):
            self.assertEqual(
                MODULE.deployment_chat_url("rg-odyn-gateway-auto19"),
                "https://" + endpoint["hostName"] + "/v1/chat",
            )
        self.assertIn("rg-odyn-gateway-auto19", run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs["timeout"], 30)

    def test_rejects_foreign_disabled_or_untrusted_endpoint(self):
        for field, value in (
            ("id", "foreign-group"),
            ("hostName", "attacker.invalid"),
            ("enabledState", "Disabled"),
        ):
            endpoint = self.endpoint() | {field: value}
            with (
                self.subTest(field=field),
                patch.dict(os.environ, {"AZURE_SUBSCRIPTION_ID": "test-sub"}),
                patch.object(
                    MODULE.subprocess,
                    "run",
                    return_value=SimpleNamespace(stdout=json.dumps([endpoint])),
                ),
                self.assertRaises(ValueError),
            ):
                MODULE.deployment_chat_url("rg-odyn-gateway-auto19")

    def test_rejects_ambiguous_endpoint_inventory(self):
        for endpoints in ([], [self.endpoint(), self.endpoint()]):
            with (
                self.subTest(count=len(endpoints)),
                patch.dict(os.environ, {"AZURE_SUBSCRIPTION_ID": "test-sub"}),
                patch.object(
                    MODULE.subprocess,
                    "run",
                    return_value=SimpleNamespace(stdout=json.dumps(endpoints)),
                ),
                self.assertRaises(ValueError),
            ):
                MODULE.deployment_chat_url("rg-odyn-gateway-auto19")

    def test_rejects_arbitrary_group_before_azure_call(self):
        with (
            patch.object(MODULE.subprocess, "run") as run,
            self.assertRaises(ValueError),
        ):
            MODULE.deployment_chat_url("rg-unrelated")
        run.assert_not_called()

    def test_checks_are_bounded_and_do_not_dispatch_valid_chat(self):
        calls = []

        def handler(request):
            calls.append(request)
            self.assertNotIn("authorization", request.headers)
            if request.method == "GET":
                return httpx.Response(
                    200, headers={"Strict-Transport-Security": "max-age=31536000"}
                )
            if request.method == "POST":
                self.assertEqual(request.content, b"{}")
                return httpx.Response(401)
            return httpx.Response(
                200, headers={"Access-Control-Allow-Origin": "https://ui.invalid"}
            )

        results = MODULE.check_edge(
            "https://edge.invalid/v1/chat",
            "https://ui.invalid",
            httpx.MockTransport(handler),
        )
        self.assertTrue(all(results.values()))
        self.assertEqual(len(calls), 3)

    def test_untrusted_url_shapes_are_rejected_before_network(self):
        for url in (
            "http://edge.invalid/v1/chat",
            "https://user:pass@edge.invalid/v1/chat",
            "https://edge.invalid/v1/chat?key=private",
            "https://edge.invalid/other",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                MODULE.check_edge(url, "https://ui.invalid")

    def test_redirect_and_wildcard_cors_fail_acceptance(self):
        results = MODULE.check_edge(
            "https://edge.invalid/v1/chat",
            "https://ui.invalid",
            httpx.MockTransport(
                lambda _: httpx.Response(
                    302,
                    headers={
                        "Location": "https://other.invalid",
                        "Access-Control-Allow-Origin": "*",
                    },
                )
            ),
        )
        self.assertFalse(any(results.values()))


class LocalLoadContractTests(unittest.TestCase):
    def test_load_scenario_requires_integer_retry_after(self):
        source = (
            Path(__file__).resolve().parents[3] / "loadtest/k6-limits.js"
        ).read_text()
        self.assertIn("/^[1-9][0-9]*$/", source)
        self.assertNotIn("Number(r.headers['Retry-After'])", source)
        self.assertIn("Local limit proof requires an explicit loopback", source)
