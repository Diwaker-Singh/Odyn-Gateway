"""Local SSO chat UI contract tests; all provider traffic is mocked."""

from __future__ import annotations

import json
import tempfile
import unittest
from email.message import Message
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace

import httpx
from scripts.local_chat_ui import (
    MAX_TOKENS,
    DemoError,
    LocalChatHandler,
    SessionStore,
    forward_chat,
    gateway_chat_url,
    load_sso_config,
    safe_chat_result,
    validate_chat_payload,
)


class LocalChatUiTests(unittest.TestCase):
    def test_local_demo_response_limit_is_deliberately_bounded(self):
        self.assertEqual(MAX_TOKENS, 1024)

    def test_gateway_origin_is_https_and_has_no_extra_url_components(self):
        self.assertEqual(
            gateway_chat_url("https://gateway.example.com"),
            "https://gateway.example.com/v1/chat",
        )
        for invalid in (
            "http://gateway.example.com",
            "https://user@gateway.example.com",
            "https://gateway.example.com/path",
            "https://gateway.example.com?query=1",
            "https://gateway.example.com:8443",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(DemoError):
                gateway_chat_url(invalid)

    def test_private_config_requires_owner_only_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "tenant_id": "tenant",
                        "api_app_id": "api",
                        "human_client_id": "human",
                        "machine_client_id": "unused",
                    }
                )
            )
            path.chmod(0o600)
            self.assertEqual(
                load_sso_config(path),
                {
                    "tenant_id": "tenant",
                    "api_app_id": "api",
                    "human_client_id": "human",
                },
            )
            path.chmod(0o644)
            with self.assertRaisesRegex(DemoError, "owner-only"):
                load_sso_config(path)

    def test_chat_payload_is_bounded_and_rejects_extra_fields(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "again"},
        ]
        self.assertEqual(validate_chat_payload({"messages": messages}), messages)
        with self.assertRaises(DemoError):
            validate_chat_payload({"messages": messages, "model": "other"})
        with self.assertRaises(DemoError):
            validate_chat_payload(
                {"messages": [{"role": "user", "content": "x" * 4001}]}
            )
        with self.assertRaises(DemoError):
            validate_chat_payload(
                {"messages": [{"role": "assistant", "content": "no"}]}
            )

    def test_gateway_response_is_reduced_to_safe_fields(self):
        status, result = safe_chat_result(
            200,
            json.dumps(
                {
                    "data": {
                        "model": "default",
                        "content": "hello",
                        "prompt_tokens": 3,
                        "completion_tokens": 2,
                        "usd": 0.001,
                    },
                    "request_id": "request-123",
                    "debug": "must not be reflected",
                }
            ).encode(),
            "fallback",
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            result,
            {
                "data": {
                    "model": "default",
                    "content": "hello",
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                },
                "request_id": "request-123",
            },
        )

    def test_gateway_errors_do_not_reflect_upstream_body(self):
        status, result = safe_chat_result(
            503, b'{"error":"private upstream diagnostic"}', "local-id"
        )
        self.assertEqual(status, 503)
        self.assertNotIn("private upstream diagnostic", json.dumps(result))

    def test_error_preserves_only_a_safe_gateway_request_id(self):
        for supplied, expected in (
            ("gateway-id-123", "gateway-id-123"),
            ("unsafe\r\nvalue", "local-id"),
            ("x" * 65, "local-id"),
        ):
            with self.subTest(supplied=supplied):
                _, result = safe_chat_result(
                    502,
                    json.dumps(
                        {
                            "error": {
                                "request_id": supplied,
                                "message": "PRIVATE_DETAIL",
                            }
                        }
                    ).encode(),
                    "local-id",
                )
                self.assertEqual(result["request_id"], expected)
                self.assertNotIn("PRIVATE_DETAIL", json.dumps(result))

    def test_chat_uses_configured_endpoint_bounded_output_and_single_request(self):
        seen = []

        def respond(request):
            seen.append(request)
            return httpx.Response(
                200,
                json={
                    "data": {"model": "default", "content": "OK", "prompt_tokens": 4},
                    "request_id": "gateway-id",
                },
            )

        status, result = forward_chat(
            "test-token",
            {"messages": [{"role": "user", "content": "Reply with OK."}]},
            transport=httpx.MockTransport(respond),
            chat_url="https://gateway.example.com/v1/chat",
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["data"]["content"], "OK")
        self.assertEqual(len(seen), 1)
        self.assertEqual(str(seen[0].url), "https://gateway.example.com/v1/chat")
        self.assertEqual(seen[0].headers["authorization"], "Bearer test-token")
        self.assertEqual(json.loads(seen[0].content)["max_tokens"], MAX_TOKENS)

    def test_ambiguous_transport_failure_is_not_retried(self):
        calls = 0

        def fail(_request):
            nonlocal calls
            calls += 1
            raise httpx.ReadTimeout("private diagnostic")

        status, result = forward_chat(
            "test-token",
            {"messages": [{"role": "user", "content": "hello"}]},
            transport=httpx.MockTransport(fail),
            chat_url="https://gateway.example.com/v1/chat",
        )
        self.assertEqual(status, 502)
        self.assertEqual(calls, 1)
        self.assertNotIn("private diagnostic", json.dumps(result))

    def test_pkce_access_token_stays_server_side_and_logout_clears_it(self):
        class FakeMsal:
            def initiate_auth_code_flow(self, scopes, **kwargs):
                self.scopes = scopes
                self.redirect_uri = kwargs["redirect_uri"]
                return {
                    "state": "state-1",
                    "auth_uri": "https://login.example/authorize",
                }

            def acquire_token_by_auth_code_flow(self, flow, response):
                self.response = response
                return {"access_token": "private-token", "expires_in": 300}

        fake = FakeMsal()
        store = SessionStore(
            {"human_client_id": "human", "tenant_id": "tenant", "api_app_id": "api"},
            app_factory=lambda *_args, **_kwargs: fake,
        )
        session_id = store.new_session()
        self.assertEqual(
            store.begin_login(session_id), "https://login.example/authorize"
        )
        self.assertEqual(fake.scopes, ["api://api/chat.invoke"])
        self.assertEqual(fake.redirect_uri, "http://localhost:8400")
        completed_session = store.finish_login(
            {"state": ["state-1"], "code": ["one-time-code"]}
        )
        self.assertEqual(completed_session, session_id)
        self.assertEqual(store.access_token(session_id), "private-token")
        store.logout(session_id)
        self.assertIsNone(store.access_token(session_id))

    def test_login_renews_a_stale_cookie_after_local_server_restart(self):
        class Sessions:
            def new_session(self):
                return "fresh-session"

            def has_session(self, session_id):
                return False

            def begin_login(self, session_id):
                if session_id != "fresh-session":
                    raise AssertionError("login did not renew the stale session")
                return "https://login.example/authorize?state=private"

        handler = object.__new__(LocalChatHandler)
        handler.headers = Message()
        handler.headers["Cookie"] = "odyn_local_session=expired-session"
        handler.path = "/api/login"
        handler.server = SimpleNamespace(sessions=Sessions())
        handler._host_ok = lambda: True
        handler._read_json = lambda: {}
        responses = []
        handler._json = lambda *args, **kwargs: responses.append((args, kwargs))

        handler.do_POST()

        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0][0][0], 200)
        self.assertEqual(
            responses[0][0][1],
            {"auth_uri": "https://login.example/authorize?state=private"},
        )
        self.assertEqual(responses[0][1]["cookie"], "fresh-session")

    def test_oauth_callback_uses_lax_cookie_for_cross_site_redirect(self):
        class Sessions:
            def finish_login(self, query):
                self.query = query
                return "authenticated-session"

        handler = object.__new__(LocalChatHandler)
        handler.path = "/?code=one-time-code&state=state-1"
        handler.server = SimpleNamespace(sessions=Sessions())
        handler._host_ok = lambda: True
        handler._headers = lambda: None
        responses = []
        handler.send_response = lambda status: responses.append([status, {}])
        handler.send_header = lambda name, value: responses[-1][1].__setitem__(
            name.lower(), value
        )
        handler.end_headers = lambda: None

        handler.do_GET()

        self.assertEqual(responses[0][0], HTTPStatus.SEE_OTHER)
        self.assertEqual(responses[0][1]["location"], "/")
        self.assertIn("SameSite=Lax", responses[0][1]["set-cookie"])
        self.assertNotIn("SameSite=Strict", responses[0][1]["set-cookie"])


if __name__ == "__main__":
    unittest.main()
