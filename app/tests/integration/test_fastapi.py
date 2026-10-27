"""M1: FastAPI adapter tests via in-process ASGI transport (no sockets).

Proves: same envelope/headers/statuses as the core pipeline, health/ready
never touch upstream (fault-mode upstream still 200), raw cap enforced
before parse at the HTTP layer, stub default retained.
"""

import unittest

from fastapi.testclient import TestClient
from helpers import auth_headers, chat_body, make_gateway, user_token

from gateway.web import create_app


class FastApiAdapterTests(unittest.TestCase):
    def client(self, **cfg):
        gw = make_gateway(**cfg)
        return TestClient(create_app(gw)), gw

    def test_chat_401_without_token(self):
        c, _ = self.client()
        r = c.post(
            "/v1/chat",
            content=chat_body(),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(r.status_code, 401)  # no token -> 401 envelope
        self.assertIn("X-Request-Id", r.headers)
        self.assertEqual(set(r.json()), {"error"})

    def test_chat_200_with_token(self):
        c, gw = self.client()
        r = c.post(
            "/v1/chat", content=chat_body(), headers=auth_headers(user_token(gw))
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["X-Request-Id"], r.json()["request_id"])
        self.assertIn("data", r.json())

    def test_415_413_422_at_http_layer(self):
        c, gw = self.client()
        tok = user_token(gw)
        r = c.post(
            "/v1/chat",
            content=chat_body(),
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "text/plain"},
        )
        self.assertEqual(r.status_code, 415)
        big = b'{"model":"x",' + b"y" * (32 * 1024 + 1) + b"}"
        r = c.post("/v1/chat", content=big, headers=auth_headers(tok))
        self.assertEqual(r.status_code, 413)
        import json

        bad = json.dumps(
            {
                "model": "nope",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 5,
            }
        ).encode()
        r = c.post("/v1/chat", content=bad, headers=auth_headers(tok))
        self.assertEqual(r.status_code, 422)

    def test_extended_output_limit_and_configured_override(self):
        # Exercise the real ASGI boundary and accounting with the stub provider.
        for ceiling in (2048, 512):
            client, gateway = self.client(max_tokens_ceiling=ceiling)
            headers = auth_headers(user_token(gateway))
            accepted = client.post(
                "/v1/chat", content=chat_body(max_tokens=ceiling), headers=headers
            )
            self.assertEqual(accepted.status_code, 200)
            rejected = client.post(
                "/v1/chat", content=chat_body(max_tokens=ceiling + 1), headers=headers
            )
            self.assertEqual(rejected.status_code, 422)

    def test_health_ready_skip_upstream(self):
        c, gw = self.client()
        gw.upstream.mode = "http_500"  # fault must not affect health
        self.assertEqual(c.get("/healthz").status_code, 200)
        r = c.get("/readyz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["upstream_mode"], "stub")
        self.assertIn("X-Request-Id", r.headers)

    def test_no_docs_routes_exposed(self):
        c, _ = self.client()
        for path in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(c.get(path).status_code, 404)


if __name__ == "__main__":
    unittest.main()
