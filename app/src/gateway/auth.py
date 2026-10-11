"""JWT validation: shared claim rules + HS256 fixture (test-only) + RS256.

- HS256 fixture path is TEST-ONLY: enabled solely when the validator is
  constructed with ``test_hs256=True``. Test helpers and the localhost load
  harness inject it explicitly; Gateway defaults to the RS256 validator
  independently of provider mode. Deployment configuration wiring is pending.
- RS256 path uses the maintained PyJWT library against a configured JWKS
  mapping (kid -> public key). No network in this milestone; the same class
  accepts a refresh callable for bounded unknown-kid refetch.

Rules: signature valid; alg in allowlist (never 'none'); iss/aud exact;
exp/nbf with small skew; delegated flow needs expected scope in `scp`;
app-only flow needs expected role in `roles`; ID tokens rejected as API
bearers; keys ONLY from configured JWKS (never token-supplied jku);
unknown kid triggers ONE bounded refresh, stale keys retained on transient
fetch failure, fail closed with no trusted key.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import hmac
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Claims:
    principal: str  # normalized rate-limit key: tid+oid or tid+azp
    is_app_only: bool


@dataclass
class LocalJwks:
    """Configured key source. `fetch_count` observes refresh bounds in tests."""

    keys: dict  # kid -> raw secret bytes (HS256 fixtures only)
    fetch_count: int = 0
    fail_fetch: bool = False

    def refresh(self) -> None:
        self.fetch_count += 1
        if self.fail_fetch:
            raise ConnectionError("jwks fetch failed")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_dec(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def mint_fixture(key: bytes, kid: str, claims: dict) -> str:
    header = {"alg": "HS256", "typ": "JWT", "kid": kid}
    h = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    sig = _b64url(hmac.new(key, f"{h}.{p}".encode(), hashlib.sha256).digest())
    return f"{h}.{p}.{sig}"


def normalize_claims(
    payload: dict,
    now: float,
    skew_seconds: int,
    required_scope: str,
    required_role: str,
) -> tuple[Claims | None, str | None]:
    """Shared post-signature claim rules for HS256 fixtures and RS256."""
    exp_raw = payload.get("exp")
    if (
        isinstance(exp_raw, bool)
        or not isinstance(exp_raw, (int, float))
        or not math.isfinite(exp_raw)
    ):
        return None, "missing_exp"
    if now >= float(exp_raw) + skew_seconds:
        return None, "expired"
    nbf = payload.get("nbf")
    if nbf is not None:
        try:
            nbf = float(nbf)
        except (TypeError, ValueError):
            return None, "bad_nbf"
        if not math.isfinite(nbf) or now < nbf - skew_seconds:
            return None, "not_yet_valid"
    if payload.get("token_type") == "id_token":
        return None, "id_token_rejected"
    tid = payload.get("tid", "")
    if not isinstance(tid, str) or not tid:
        return None, "missing_claims"
    if "scp" not in payload:  # Delegated tokens can also carry user app roles.
        roles = payload.get("roles", [])
        if not isinstance(roles, list) or required_role not in roles:
            return None, "missing_role"
        client = payload.get("azp") or payload.get("appid") or ""
        if not isinstance(client, str) or not client:
            return None, "missing_client"
        return Claims(principal=f"{tid}:{client}", is_app_only=True), None
    raw_scope = payload.get("scp", "")
    if not isinstance(raw_scope, str):
        return None, "missing_scope"
    scopes = raw_scope.split()
    if required_scope not in scopes:
        return None, "missing_scope"
    oid = payload.get("oid")
    if not isinstance(oid, str) or not oid:
        return None, "missing_claims"
    return Claims(principal=f"{tid}:{oid}", is_app_only=False), None


@dataclass
class JwtValidator:
    """HS256 fixture validator. TEST-ONLY (`test_hs256=True`); stub mode only."""

    issuer: str
    audience: str
    allowed_algorithms: tuple = ("RS256", "HS256")
    required_scope: str = "chat.invoke"
    required_role: str = "Chat.Invoke.Application"
    skew_seconds: int = 60
    jwks: LocalJwks = field(default_factory=lambda: LocalJwks({}))
    test_hs256: bool = False

    def validate(
        self, token: str | None, now: float | None = None
    ) -> tuple[Claims | None, str | None]:
        """Returns (claims, None) or (None, failure_reason). Never raises."""
        now = time.time() if now is None else now
        if not token or not isinstance(token, str) or len(token) > 16384:
            return None, "missing"
        parts = token.split(".")
        if len(parts) != 3:
            return None, "malformed"
        try:
            header = json.loads(_b64url_dec(parts[0]))
            payload = json.loads(_b64url_dec(parts[1]))
            sig = _b64url_dec(parts[2])
        except (ValueError, binascii.Error):
            return None, "malformed"
        if not isinstance(header, dict):
            return None, "malformed"
        alg = header.get("alg")
        if alg not in self.allowed_algorithms or alg == "none":
            return None, "disallowed_alg"
        if not isinstance(payload, dict):
            return None, "malformed"
        kid = header.get("kid")
        key = self.jwks.keys.get(kid) if isinstance(kid, str) else None
        if key is None:
            # ONE bounded refresh for unknown kid; stale keys retained on failure.
            with contextlib.suppress(ConnectionError):
                self.jwks.refresh()
            key = self.jwks.keys.get(kid) if isinstance(kid, str) else None
            if key is None:
                return None, "unknown_kid"
        if alg == "HS256":
            if not self.test_hs256:
                # Fixture path can never run in production configuration.
                return None, "needs_rs256_adapter"
            expect = hmac.new(
                key, f"{parts[0]}.{parts[1]}".encode(), hashlib.sha256
            ).digest()
            if not hmac.compare_digest(expect, sig):
                return None, "bad_signature"
        else:  # RS256 via this fixture path is unsupported; use Rs256Validator.
            return None, "needs_rs256_adapter"
        if payload.get("iss") != self.issuer:
            return None, "wrong_issuer"
        if payload.get("aud") != self.audience:
            return None, "wrong_audience"
        return normalize_claims(
            payload, now, self.skew_seconds, self.required_scope, self.required_role
        )


@dataclass
class Rs256Jwks:
    """Configured RS256 key source: kid -> public key object (cryptography).

    `refresh` is an optional bounded refetch hook for unknown kids;
    `fetch_count`/`fail_fetch` observe the same bounds as LocalJwks in tests.
    Live deployments MUST wire refresh to the configured issuer's JWKS
    endpoint only — never to token-supplied URLs.
    """

    keys: dict
    refresh_hook: Callable[[], None] | None = None
    fetch_count: int = 0
    fail_fetch: bool = False
    ensure_fresh_hook: Callable[[], None] | None = None

    def refresh(self) -> None:
        self.fetch_count += 1
        if self.fail_fetch:
            raise ConnectionError("jwks fetch failed")
        if self.refresh_hook is not None:
            self.refresh_hook()


@dataclass
class Rs256Validator:
    """Maintained-library (PyJWT) RS256 validator. Production path."""

    issuer: str
    audience: str
    required_scope: str = "chat.invoke"
    required_role: str = "Chat.Invoke.Application"
    skew_seconds: int = 60
    jwks: Rs256Jwks = field(default_factory=lambda: Rs256Jwks({}))

    def validate(
        self, token: str | None, now: float | None = None
    ) -> tuple[Claims | None, str | None]:
        """Returns (claims, None) or (None, failure_reason). Never raises."""
        import jwt as pyjwt

        now = time.time() if now is None else now
        if not token or not isinstance(token, str) or len(token) > 16384:
            return None, "missing"
        try:
            header = pyjwt.get_unverified_header(token)
        except pyjwt.PyJWTError:
            return None, "malformed"
        alg = header.get("alg")
        # Explicit allowlist: RS256 only. HS256-into-RS256 (algorithm
        # confusion) and `none` are rejected before any key use.
        if alg != "RS256":
            return None, "disallowed_alg"
        if self.jwks.ensure_fresh_hook is not None:
            self.jwks.ensure_fresh_hook()
        kid = header.get("kid")
        key = self.jwks.keys.get(kid) if isinstance(kid, str) else None
        if key is None:
            with contextlib.suppress(ConnectionError):
                self.jwks.refresh()
            key = self.jwks.keys.get(kid) if isinstance(kid, str) else None
            if key is None:
                return None, "unknown_kid"
        try:
            payload = pyjwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self.issuer,
                audience=self.audience,
                leeway=self.skew_seconds,
                options={
                    "require": ["exp", "iss", "aud"],
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                },
            )
        except pyjwt.ExpiredSignatureError:
            return None, "expired"
        except pyjwt.ImmatureSignatureError:
            return None, "not_yet_valid"
        except pyjwt.InvalidIssuerError:
            return None, "wrong_issuer"
        except pyjwt.InvalidAudienceError:
            return None, "wrong_audience"
        except pyjwt.InvalidSignatureError:
            return None, "bad_signature"
        except pyjwt.PyJWTError:
            return None, "malformed"
        if not isinstance(payload, dict):
            return None, "malformed"
        return normalize_claims(
            payload, now, self.skew_seconds, self.required_scope, self.required_role
        )
