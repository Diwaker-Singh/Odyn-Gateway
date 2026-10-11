"""Loopback-only SSO chat page for the Odyn Gateway.

The browser never receives the Entra access token. This small local server
handles the native-client PKCE callback and forwards bounded chat requests to
the configured HTTPS gateway origin. It stores tokens only in memory.
"""

from __future__ import annotations

import http.server
import json
import os
import re
import secrets
import threading
import time
import webbrowser
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import msal

ROOT = Path(__file__).resolve().parent.parent
UI_ROOT = ROOT / "local-ui"
SSO_CONFIG = ROOT / ".secrets/sso/config.json"
HOST = "localhost"
PORT = 8400
ORIGIN = f"http://{HOST}:{PORT}"
REDIRECT_URI = ORIGIN
MAX_BODY_BYTES = 64 * 1024
MAX_MESSAGES = 12
MAX_MESSAGE_CHARS = 4000
MAX_TOTAL_CHARS = 12_000
MAX_RESPONSE_BYTES = 64 * 1024
MAX_TOKENS = 128
COOKIE_NAME = "odyn_local_session"
COOKIE_AGE = 3600
MAX_SESSIONS = 32


class DemoError(ValueError):
    """Safe local-demo error with no credentials or provider diagnostics."""


def gateway_chat_url(value: str | None = None) -> str:
    """Resolve a configured HTTPS origin without accepting URL injection."""
    origin = os.environ.get("ODYN_GATEWAY_URL", "") if value is None else value
    parsed = urlsplit(origin.strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.netloc != parsed.hostname
    ):
        raise DemoError("Set ODYN_GATEWAY_URL to the HTTPS gateway origin")
    return f"{origin.rstrip('/')}/v1/chat"


def load_sso_config(path: Path = SSO_CONFIG) -> dict[str, str]:
    """Load the ignored private Entra config without exposing its contents."""
    try:
        if path.stat().st_mode & 0o077:
            raise DemoError("SSO config permissions must be owner-only")
        value = json.loads(path.read_text())
    except DemoError:
        raise
    except (OSError, json.JSONDecodeError):
        raise DemoError("SSO config is unavailable") from None
    required = ("tenant_id", "api_app_id", "human_client_id")
    if not isinstance(value, dict) or any(
        not isinstance(value.get(key), str) or not value[key] for key in required
    ):
        raise DemoError("SSO config is incomplete")
    return {key: value[key] for key in required}


def validate_chat_payload(value: Any) -> list[dict[str, str]]:
    """Accept a bounded user/assistant conversation and return safe messages."""
    if not isinstance(value, dict) or set(value) != {"messages"}:
        raise DemoError("Invalid chat request")
    messages = value["messages"]
    if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES:
        raise DemoError("Keep the conversation to 12 messages or fewer")
    clean: list[dict[str, str]] = []
    total = 0
    for message in messages:
        if (
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message.get("role") not in {"user", "assistant"}
            or not isinstance(message.get("content"), str)
        ):
            raise DemoError("Invalid conversation message")
        content = message["content"]
        if not content.strip() or len(content) > MAX_MESSAGE_CHARS:
            raise DemoError("Each message must contain 1–4,000 characters")
        total += len(content)
        if total > MAX_TOTAL_CHARS:
            raise DemoError("Conversation is too long; start a new chat")
        clean.append({"role": message["role"], "content": content})
    if clean[-1]["role"] != "user":
        raise DemoError("The latest message must be from you")
    return clean


def safe_chat_result(
    status: int, body: bytes, request_id: str
) -> tuple[int, dict[str, Any]]:
    """Extract only the gateway's user-facing response fields."""
    try:
        payload = json.loads(body)
    except (UnicodeError, json.JSONDecodeError):
        return HTTPStatus.BAD_GATEWAY, {"error": "Gateway returned an invalid response"}
    if status == HTTPStatus.OK and isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("content"), str):
            result: dict[str, Any] = {
                "data": {
                    "model": str(data.get("model", "default"))[:100],
                    "content": data["content"][:16_000],
                },
                "request_id": str(payload.get("request_id", request_id))[:64],
            }
            for field in ("prompt_tokens", "completion_tokens"):
                count = data.get(field)
                if (
                    isinstance(count, int)
                    and not isinstance(count, bool)
                    and count >= 0
                ):
                    result["data"][field] = count
            return HTTPStatus.OK, result
        return HTTPStatus.BAD_GATEWAY, {"error": "Gateway response was incomplete"}
    # Do not reflect provider diagnostics or upstream response bodies in the UI.
    error = payload.get("error") if isinstance(payload, dict) else None
    response_id = error.get("request_id") if isinstance(error, dict) else None
    if isinstance(response_id, str) and re.fullmatch(
        r"[A-Za-z0-9_-]{1,64}", response_id
    ):
        request_id = response_id
    message = "Request was not accepted"
    if status == HTTPStatus.UNAUTHORIZED:
        message = "Your sign-in expired; sign in again"
    elif status == HTTPStatus.TOO_MANY_REQUESTS:
        message = "The gateway rate limit was reached; wait before trying again"
    elif status >= 500:
        message = "The gateway could not complete the request"
    return status if 400 <= status < 600 else HTTPStatus.BAD_GATEWAY, {
        "error": message,
        "request_id": request_id,
    }


class SessionStore:
    """Single-process in-memory session and MSAL flow state."""

    def __init__(self, config: dict[str, str], app_factory=None):
        self.config = config
        self._lock = threading.RLock()
        self._sessions: dict[str, tuple[str, float]] = {}
        self._flows: dict[str, tuple[str, dict[str, Any]]] = {}
        factory = app_factory or msal.PublicClientApplication
        self._app = factory(
            config["human_client_id"],
            authority=f"https://login.microsoftonline.com/{config['tenant_id']}",
            enable_pii_log=False,
        )

    def new_session(self) -> str:
        session_id = secrets.token_urlsafe(32)
        with self._lock:
            now = time.time()
            for key, (_, expiry) in list(self._sessions.items()):
                if expiry <= now and not self._sessions[key][0]:
                    self._sessions.pop(key, None)
            while len(self._sessions) >= MAX_SESSIONS:
                self._sessions.pop(next(iter(self._sessions)))
            self._sessions.setdefault(session_id, ("", 0.0))
        return session_id

    def has_session(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._sessions

    def begin_login(self, session_id: str) -> str:
        if not self.has_session(session_id):
            raise DemoError("Session expired; reload the page")
        try:
            flow = self._app.initiate_auth_code_flow(
                [f"api://{self.config['api_app_id']}/chat.invoke"],
                redirect_uri=REDIRECT_URI,
                response_mode="query",
            )
        except Exception:  # noqa: BLE001 - MSAL errors can contain OAuth response data.
            raise DemoError("Could not start Entra sign-in") from None
        state = flow.get("state")
        auth_uri = flow.get("auth_uri")
        if not isinstance(state, str) or not isinstance(auth_uri, str):
            raise DemoError("Could not start Entra sign-in")
        with self._lock:
            self._flows[state] = (session_id, flow)
        return auth_uri

    def finish_login(self, query: dict[str, list[str]]) -> str:
        state = (query.get("state") or [""])[0]
        with self._lock:
            pending = self._flows.pop(state, None)
        if pending is None:
            raise DemoError("Sign-in expired or did not match this browser")
        session_id, flow = pending
        if (query.get("error") or [None])[0] is not None:
            raise DemoError("Entra sign-in was not completed")
        response = {key: values[0] for key, values in query.items() if values}
        try:
            result = self._app.acquire_token_by_auth_code_flow(flow, response)
        except Exception:  # noqa: BLE001 - suppress MSAL diagnostics and response data.
            raise DemoError("Entra sign-in could not be completed") from None
        token = result.get("access_token") if isinstance(result, dict) else None
        expires = result.get("expires_in", 0) if isinstance(result, dict) else 0
        if not isinstance(token, str) or not token:
            raise DemoError("Entra did not issue an API access token")
        try:
            expires_at = time.time() + max(0, int(expires))
        except (TypeError, ValueError):
            expires_at = time.time()
        with self._lock:
            self._sessions[session_id] = (token, expires_at)
        return session_id

    def access_token(self, session_id: str) -> str | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            token, expires_at = session
            if not token or expires_at <= time.time() + 30:
                self._sessions[session_id] = ("", 0.0)
                return None
            return token

    def logout(self, session_id: str) -> None:
        with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id] = ("", 0.0)


def forward_chat(
    token: str,
    payload: dict[str, Any],
    *,
    transport: httpx.BaseTransport | None = None,
    chat_url: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Send one bounded request to the configured gateway."""
    messages = validate_chat_payload(payload)
    target = (
        gateway_chat_url()
        if chat_url is None
        else gateway_chat_url(chat_url.rsplit("/v1/chat", 1)[0])
    )
    request_id = "local-ui-" + uuid4().hex
    body = bytearray()
    try:
        with (
            httpx.Client(
                transport=transport,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(45.0, connect=5.0),
            ) as client,
            client.stream(
                "POST",
                target,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "X-Request-Id": request_id,
                },
                json={
                    "model": "default",
                    "messages": messages,
                    "max_tokens": MAX_TOKENS,
                },
            ) as response,
        ):
            status = response.status_code
            for chunk in response.iter_bytes():
                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                    return HTTPStatus.BAD_GATEWAY, {
                        "error": "Gateway response exceeded the local display limit",
                        "request_id": request_id,
                    }
                body.extend(chunk)
    except httpx.HTTPError:
        # The request may have reached the provider; never retry automatically.
        return HTTPStatus.BAD_GATEWAY, {
            "error": "Connection failed; check the gateway before sending again",
            "request_id": request_id,
        }
    return safe_chat_result(status, bytes(body), request_id)


class LocalChatHandler(http.server.BaseHTTPRequestHandler):
    server_version = "OdynLocalDemo"
    sys_version = ""

    @property
    def sessions(self) -> SessionStore:
        return self.server.sessions  # type: ignore[attr-defined]

    def log_message(self, _format: str, *args: Any) -> None:
        # Default HTTP logs can include URLs, OAuth codes, and user messages.
        return

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "").lower() in {
            f"localhost:{PORT}",
            f"127.0.0.1:{PORT}",
        }

    def _session_id(self, create: bool = False) -> str | None:
        raw = self.headers.get("Cookie", "")
        for item in raw.split(";"):
            name, sep, value = item.strip().partition("=")
            if sep and name == COOKIE_NAME and self.sessions.has_session(value):
                return value
        return self.sessions.new_session() if create else None

    def _headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )

    def _json(
        self, status: int, value: dict[str, Any], cookie: str | None = None
    ) -> None:
        encoded = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._headers()
        self.send_header("Content-Length", str(len(encoded)))
        if cookie is not None:
            self.send_header(
                "Set-Cookie",
                f"{COOKIE_NAME}={cookie}; Path=/; HttpOnly; SameSite=Lax; Max-Age={COOKIE_AGE}",
            )
        self.end_headers()
        self.wfile.write(encoded)

    def _read_json(self) -> dict[str, Any]:
        if self.headers.get("Origin") != ORIGIN:
            raise DemoError("Request origin is not allowed")
        if self.headers.get_content_type() != "application/json":
            raise DemoError("Expected a JSON request")
        length = self.headers.get("Content-Length", "")
        if not length.isascii() or not length.isdecimal() or len(length) > 5:
            raise DemoError("Invalid request size")
        size = int(length)
        if size > MAX_BODY_BYTES:
            raise DemoError("Request is too large")
        try:
            value = json.loads(self.rfile.read(size))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise DemoError("Invalid JSON") from None
        if not isinstance(value, dict):
            raise DemoError("Invalid request")
        return value

    def do_GET(self) -> None:
        if not self._host_ok():
            self.send_error(HTTPStatus.MISDIRECTED_REQUEST)
            return
        parsed = urlsplit(self.path)
        if parsed.path == "/" and parsed.query:
            query = parse_qs(parsed.query, keep_blank_values=True)
            if "code" in query or "error" in query:
                try:
                    session_id = self.sessions.finish_login(query)
                except DemoError as exc:
                    self._html_error(str(exc), HTTPStatus.BAD_REQUEST)
                    return
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", "/")
                self._headers()
                self.send_header(
                    "Set-Cookie",
                    f"{COOKIE_NAME}={session_id}; Path=/; HttpOnly; SameSite=Lax; Max-Age={COOKIE_AGE}",
                )
                self.end_headers()
                return
        if parsed.path == "/api/session":
            session_id = self._session_id()
            self._json(
                HTTPStatus.OK,
                {
                    "authenticated": bool(
                        session_id and self.sessions.access_token(session_id)
                    )
                },
            )
            return
        if parsed.path not in {"/", "/index.html", "/app.js", "/style.css"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        filename = (
            "index.html" if parsed.path in {"/", "/index.html"} else parsed.path[1:]
        )
        content_type = (
            "text/javascript; charset=utf-8"
            if filename.endswith(".js")
            else "text/css; charset=utf-8"
            if filename.endswith(".css")
            else "text/html; charset=utf-8"
        )
        try:
            content = (UI_ROOT / filename).read_bytes()
        except OSError:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        session_id = self._session_id(create=parsed.path in {"/", "/index.html"})
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self._headers()
        self.send_header("Content-Length", str(len(content)))
        if session_id is not None and parsed.path in {"/", "/index.html"}:
            self.send_header(
                "Set-Cookie",
                f"{COOKIE_NAME}={session_id}; Path=/; HttpOnly; SameSite=Lax; Max-Age={COOKIE_AGE}",
            )
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:
        if not self._host_ok():
            self._json(
                HTTPStatus.MISDIRECTED_REQUEST, {"error": "Use the local demo address"}
            )
            return
        path = urlsplit(self.path).path
        if path == "/api/login":
            session_id = None
            try:
                payload = self._read_json()
                if payload:
                    raise DemoError("Invalid sign-in request")
                # The server keeps sessions in memory. A browser can retain its
                # HttpOnly cookie across a server restart, when that session no
                # longer exists. Renew it here so the sign-in button recovers
                # without requiring a manual page reload.
                session_id = self._session_id(create=True)
                auth_uri = self.sessions.begin_login(session_id)
            except DemoError as exc:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": str(exc)},
                    cookie=session_id,
                )
                return
            self._json(HTTPStatus.OK, {"auth_uri": auth_uri}, cookie=session_id)
            return
        session_id = self._session_id()
        if path == "/api/logout":
            if self.headers.get("Origin") != ORIGIN:
                self._json(
                    HTTPStatus.FORBIDDEN, {"error": "Request origin is not allowed"}
                )
                return
            if session_id:
                self.sessions.logout(session_id)
            self._json(HTTPStatus.OK, {"authenticated": False})
            return
        if path == "/api/chat":
            token = self.sessions.access_token(session_id or "")
            if token is None:
                self._json(
                    HTTPStatus.UNAUTHORIZED,
                    {"error": "Sign in before sending a message"},
                )
                return
            try:
                payload = self._read_json()
                status, result = forward_chat(token, payload)
            except DemoError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            if status == HTTPStatus.UNAUTHORIZED and session_id is not None:
                self.sessions.logout(session_id)
            self._json(status, result)
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def _html_error(self, message: str, status: int) -> None:
        safe = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        body = (
            "<!doctype html><meta charset=utf-8><title>Sign-in problem</title>"
            f"<p>{safe}</p><p><a href='/'>Return to Odyn Gateway</a></p>"
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class QuietThreadingHTTPServer(http.server.ThreadingHTTPServer):
    """Avoid default tracebacks that can expose request and OAuth context."""

    def handle_error(self, _request, _client_address) -> None:
        return


def main() -> int:
    try:
        config = load_sso_config()
        gateway_chat_url()
        server = QuietThreadingHTTPServer(("127.0.0.1", PORT), LocalChatHandler)
    except DemoError as exc:
        print(f"Local demo unavailable: {exc}")
        return 2
    except OSError:
        print("Local demo unavailable: port 8400 is already in use")
        return 2
    server.sessions = SessionStore(config)  # type: ignore[attr-defined]
    server.daemon_threads = True
    print(f"Odyn Gateway local chat is listening at {ORIGIN}/ (Ctrl-C to stop).")
    try:
        webbrowser.open(f"{ORIGIN}/")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by local operator
    raise SystemExit(main())
