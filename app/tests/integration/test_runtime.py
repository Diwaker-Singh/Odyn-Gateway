"""Runtime wiring and bounded ASGI receive regressions; no external network."""

import asyncio
import json
import time
import unittest

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from helpers import (
    AUDIENCE,
    ISSUER,
    auth_headers,
    base_claims,
    chat_body,
    make_gateway,
    user_token,
)

from gateway.api import Gateway
from gateway.auth import Rs256Jwks, Rs256Validator
from gateway.web import create_app


class RuntimeWiringTests(unittest.TestCase):
    def test_default_uses_rs256_even_with_stub_provider(self):
        gw = Gateway()
        self.assertIsInstance(gw.validator, Rs256Validator)
        fixture = make_gateway()
        status, _, _ = gw.chat(auth_headers(user_token(fixture)), chat_body())
        self.assertEqual(status, 401)
        self.assertEqual(gw.upstream.calls, 0)

    def test_injected_rs256_runs_in_chat_pipeline(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        validator = Rs256Validator(
            issuer=ISSUER,
            audience=AUDIENCE,
            jwks=Rs256Jwks({"runtime": key.public_key()}),
        )
        gw = Gateway(validator=validator)
        token = jwt.encode(
            base_claims(), key, algorithm="RS256", headers={"kid": "runtime"}
        )
        status, _, _ = gw.chat(auth_headers(token), chat_body())
        self.assertEqual(status, 200)
        self.assertIs(gw.validator, validator)
        self.assertEqual(gw.upstream.calls, 1)

    def test_elapsed_clock_is_injected_and_advances(self):
        ticks = [10.0]
        gw = Gateway(clock=lambda: ticks[0], wall_clock=lambda: 123.0)
        self.assertEqual(gw.now, 10.0)
        ticks[0] = 12.0
        self.assertEqual(gw.now, 12.0)

    def test_expired_fixture_rejected_using_wall_time(self):
        gw = make_gateway()
        status, _, _ = gw.chat(
            auth_headers(user_token(gw, exp=time.time() - 120)), chat_body()
        )
        self.assertEqual(status, 401)
        self.assertEqual(gw.upstream.calls, 0)


class BoundedReceiveTests(unittest.IsolatedAsyncioTestCase):
    async def exchange(self, gw, events, extra_headers=None):
        headers = auth_headers(user_token(gw))
        headers.update(extra_headers or {})
        messages = []
        read = []

        async def receive():
            # Unexpected reads fail instead of silently producing an empty body.
            event = events[len(read)]
            read.append(event)
            return event

        async def send(event):
            messages.append(event)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/chat",
            "raw_path": b"/v1/chat",
            "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "server": ("test", 80),
            "client": ("test", 123),
        }
        await create_app(gw)(scope, receive, send)
        start = next(m for m in messages if m["type"] == "http.response.start")
        body = json.loads(b"".join(m.get("body", b"") for m in messages))
        return start, body, len(read)

    async def test_chunks_over_cap_stop_receiving_before_next_chunk(self):
        gw = make_gateway(max_body_bytes=16)
        start, body, reads = await self.exchange(
            gw,
            [
                {"type": "http.request", "body": b"a" * 10, "more_body": True},
                {"type": "http.request", "body": b"b" * 7, "more_body": True},
            ],
        )
        self.assertEqual(start["status"], 413)
        self.assertEqual(body["error"]["code"], "body_too_large")
        self.assertEqual(reads, 2)
        self.assertEqual(gw.upstream.calls, 0)
        self.assertIn(b"x-request-id", dict(start["headers"]))
        self.assertEqual(gw.telemetry.events[-1]["status"], 413)
        self.assertIn("latency_ms", gw.telemetry.events[-1])

    async def test_false_content_length_does_not_bypass_stream_cap(self):
        gw = make_gateway(max_body_bytes=16)
        start, _, _ = await self.exchange(
            gw,
            [
                {"type": "http.request", "body": b"a" * 17, "more_body": False},
            ],
            {"Content-Length": "1"},
        )
        self.assertEqual(start["status"], 413)
        self.assertEqual(gw.upstream.calls, 0)

    async def test_declared_oversize_rejected_without_receive(self):
        gw = make_gateway(max_body_bytes=16)
        start, _, reads = await self.exchange(gw, [], {"Content-Length": "9" * 5000})
        self.assertEqual(start["status"], 413)
        self.assertEqual(reads, 0)

    async def test_invalid_content_length_is_safe_bad_request(self):
        gw = make_gateway()
        for length in ("-1", "hello", "1.0"):
            start, _, reads = await self.exchange(gw, [], {"Content-Length": length})
            self.assertEqual(start["status"], 400)
            self.assertEqual(reads, 0)

    async def test_exact_cap_and_below_reach_pipeline(self):
        raw = chat_body()
        for cap in (len(raw), len(raw) + 1):
            gw = make_gateway(max_body_bytes=cap)
            start, _, _ = await self.exchange(
                gw,
                [
                    {"type": "http.request", "body": raw[:10], "more_body": True},
                    {"type": "http.request", "body": raw[10:], "more_body": False},
                ],
            )
            self.assertEqual(start["status"], 200)
            self.assertEqual(gw.upstream.calls, 1)

    async def test_disconnect_does_not_dispatch(self):
        gw = make_gateway()
        start, _, _ = await self.exchange(
            gw,
            [
                {"type": "http.request", "body": b"{", "more_body": True},
                {"type": "http.disconnect"},
            ],
        )
        self.assertEqual(start["status"], 400)
        self.assertEqual(gw.upstream.calls, 0)

    async def test_slow_request_body_is_bounded_by_request_deadline(self):
        gw = make_gateway(deadline_seconds=0.02)
        reads = 0

        async def receive():
            nonlocal reads
            reads += 1
            await asyncio.sleep(0.05)
            return {"type": "http.request", "body": b"{", "more_body": True}

        messages = []

        async def send(event):
            messages.append(event)

        headers = auth_headers(user_token(gw))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/chat",
            "raw_path": b"/v1/chat",
            "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "server": ("test", 80),
            "client": ("test", 123),
        }
        await create_app(gw)(scope, receive, send)
        start = next(m for m in messages if m["type"] == "http.response.start")
        body = json.loads(b"".join(m.get("body", b"") for m in messages))
        self.assertEqual(start["status"], 504)
        self.assertEqual(body["error"]["code"], "upstream_timeout")
        self.assertEqual(reads, 1)
        self.assertEqual(gw.upstream.calls, 0)
        self.assertEqual(gw.telemetry.metrics["request_deadline_exceeded"], 1)
        self.assertEqual(gw.telemetry.events[-1]["status"], 504)
        self.assertIn("latency_ms", gw.telemetry.events[-1])
