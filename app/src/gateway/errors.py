"""Shared error envelope + status mapping (refined plan section 3.4).

Every response uses ``{"error": {"code", "message", "request_id"}}``.
Gateway-generated errors may need a gateway response policy to match this
envelope on the deployed tier; if they cannot be normalized, that is a
documented conformance gap (not silently assumed).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ApiError:
    code: str
    message: str  # safe message only — never internals, prompts, tokens, keys
    status: int
    request_id: str

    def envelope(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "request_id": self.request_id,
            }
        }


# Stable codes; messages are deliberately generic.
AUTH_REQUIRED = ("unauthenticated", 401, "Authentication required.")
FORBIDDEN = ("forbidden", 403, "Insufficient scope or role.")
BAD_REQUEST = ("bad_request", 400, "Malformed request.")
TOO_LARGE = ("body_too_large", 413, "Request body exceeds the size limit.")
UNSUPPORTED_MEDIA = (
    "unsupported_media_type",
    415,
    "Content-Type must be application/json.",
)
UNPROCESSABLE = ("unprocessable", 422, "Request failed schema or token-limit checks.")
RATE_LIMITED = ("rate_limited", 429, "Rate or budget limit exceeded.")
UPSTREAM_BAD = ("upstream_error", 502, "Upstream inference failed.")
UPSTREAM_UNAVAILABLE = ("upstream_unavailable", 503, "Service temporarily unavailable.")
UPSTREAM_TIMEOUT = ("upstream_timeout", 504, "Upstream request timed out.")
