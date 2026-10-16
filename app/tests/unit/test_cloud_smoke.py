"""Public smoke checks reject redirects and never send credentials or real chat."""

import importlib.util
import unittest
from pathlib import Path

import httpx

SPEC = importlib.util.spec_from_file_location(
    "cloud_smoke", Path(__file__).resolve().parents[3] / "scripts/cloud-smoke.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CloudSmokeTests(unittest.TestCase):
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
