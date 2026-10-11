"""Strict request validation: content-type, raw size, schema, allowlist, ceiling.

Reject (never clamp) excessive max_tokens. Extra fields rejected.
Raw body size enforced BEFORE parsing (413). Non-JSON content type -> 415.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .config import GatewayConfig
from .errors import BAD_REQUEST, TOO_LARGE, UNPROCESSABLE, UNSUPPORTED_MEDIA, ApiError


@dataclass(frozen=True)
class ChatRequest:
    model_alias: str
    messages: tuple
    max_tokens: int
    request_id: str


def _fail(code_msg: tuple, request_id: str) -> ApiError:
    code, status, message = code_msg
    return ApiError(code=code, message=message, status=status, request_id=request_id)


def validate_chat(
    content_type: str | None,
    raw_body: bytes,
    config: GatewayConfig,
    request_id: str,
) -> tuple[ChatRequest | None, ApiError | None]:
    if (content_type or "").split(";")[0].strip().lower() != "application/json":
        return None, _fail(UNSUPPORTED_MEDIA, request_id)
    if len(raw_body) > config.max_body_bytes:
        return None, _fail(TOO_LARGE, request_id)
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None, _fail(BAD_REQUEST, request_id)
    if not isinstance(payload, dict):
        return None, _fail(BAD_REQUEST, request_id)

    allowed = {"model", "messages", "max_tokens"}
    if set(payload) - allowed:
        return None, _fail(UNPROCESSABLE, request_id)

    alias = payload.get("model")
    messages = payload.get("messages")
    max_tokens = payload.get("max_tokens")
    if not isinstance(alias, str) or alias not in config.models:
        return None, _fail(UNPROCESSABLE, request_id)
    if not isinstance(messages, list) or not messages:
        return None, _fail(UNPROCESSABLE, request_id)
    for m in messages:
        if (
            not isinstance(m, dict)
            or set(m) - {"role", "content"}
            or m.get("role") not in ("system", "user", "assistant")
            or not isinstance(m.get("content"), str)
            or not m["content"]
        ):
            return None, _fail(UNPROCESSABLE, request_id)
    if (
        not isinstance(max_tokens, int)
        or isinstance(max_tokens, bool)
        or max_tokens <= 0
        or max_tokens > config.max_tokens_ceiling
    ):
        # Over-ceiling is REJECTED, never clamped.
        return None, _fail(UNPROCESSABLE, request_id)
    return (
        ChatRequest(
            model_alias=alias,
            messages=tuple(messages),
            max_tokens=max_tokens,
            request_id=request_id,
        ),
        None,
    )
