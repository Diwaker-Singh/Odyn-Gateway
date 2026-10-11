"""Bounded trusted JWKS cache; no discovery or token-selected destinations."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable

import httpx
import jwt

from .auth import Rs256Jwks


class TrustedJwks(Rs256Jwks):
    def __init__(
        self,
        url: str,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        super().__init__({})
        self.url = url
        self.clock = clock
        self._last_attempt = float("-inf")
        self._last_success = float("-inf")
        self._lock = threading.Lock()
        self.client = httpx.Client(
            transport=transport, timeout=2.0, follow_redirects=False, trust_env=False
        )
        self.ensure_fresh_hook = self.ensure_fresh

    def close(self) -> None:
        self.client.close()

    def ensure_fresh(self) -> None:
        if self.clock() - self._last_success >= 300:
            self.refresh()
        if self.clock() - self._last_success > 900:
            self.keys = {}

    def refresh(self) -> None:
        with self._lock:
            if self.clock() - self._last_attempt < 10:
                return
            self._last_attempt = self.clock()
            self.fetch_count += 1
            try:
                with self.client.stream("GET", self.url) as response:
                    response.raise_for_status()
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        if len(chunk) > 65536 - len(body):
                            raise ValueError("JWKS too large")
                        body.extend(chunk)
                parsed = json.loads(body)
                keys = parsed["keys"]
                if not isinstance(keys, list) or len(keys) > 32:
                    raise ValueError("invalid JWKS")
                new = {}
                for key in keys:
                    if (
                        key.get("kty") == "RSA"
                        and key.get("use", "sig") == "sig"
                        and key.get("alg", "RS256") == "RS256"
                    ):
                        kid = key["kid"]
                        if (
                            not isinstance(kid, str)
                            or not 1 <= len(kid) <= 128
                            or kid in new
                        ):
                            raise ValueError("invalid kid")
                        public_key = jwt.PyJWK.from_dict(key).key
                        if public_key.key_size < 2048:
                            raise ValueError("RSA key too small")
                        new[kid] = public_key
                if not new:
                    raise ValueError("empty JWKS")
                self.keys = new
                self._last_success = self.clock()
            except (
                httpx.HTTPError,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                jwt.PyJWTError,
            ):
                # Keep a trusted cached key for at most 15 minutes; never log payloads.
                if self.clock() - self._last_success > 900:
                    self.keys = {}
