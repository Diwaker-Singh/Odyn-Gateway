"""Helpers: deterministic fixtures (HS256 local JWKS only; no network, no key)."""

from __future__ import annotations

import time

from gateway.api import Gateway
from gateway.auth import JwtValidator, LocalJwks, mint_fixture
from gateway.config import GatewayConfig

ISSUER = "https://login.microsoftonline.com/<tenant-id>/v2.0"
AUDIENCE = "<api-app-id>"
KEY = b"fixture-secret-32-bytes-minimum!!"
KID = "test-kid-1"


def base_claims(**over) -> dict:
    now = time.time()
    c = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": now + 600,
        "tid": "tenant-1",
        "oid": "user-1",
        "scp": "chat.invoke",
    }
    c.update(over)
    return c


def machine_claims(**over) -> dict:
    now = time.time()
    c = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": now + 600,
        "tid": "tenant-1",
        "azp": "machine-app-1",
        "roles": ["Chat.Invoke.Application"],
    }
    c.update(over)
    return c


def make_gateway(**cfg) -> Gateway:
    validator = JwtValidator(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks=LocalJwks({KID: KEY}),
        allowed_algorithms=("HS256",),
        test_hs256=True,
    )
    gw = Gateway(GatewayConfig(**cfg), validator=validator)
    return gw


def user_token(gw: Gateway, **over) -> str:
    return mint_fixture(KEY, KID, base_claims(**over))


def machine_token(gw: Gateway, **over) -> str:
    return mint_fixture(KEY, KID, machine_claims(**over))


def chat_body(model: str = "default", max_tokens: int = 10) -> bytes:
    import json

    return json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": max_tokens,
        }
    ).encode()


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
