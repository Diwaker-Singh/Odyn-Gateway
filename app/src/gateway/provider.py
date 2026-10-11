"""Fixed-destination provider adapter. Tests inject transport; no live default."""

from __future__ import annotations

import asyncio
import json
import re
import uuid

import httpx
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from .config import GatewayConfig
from .upstream import StubResult

URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouter:
    def __init__(
        self,
        config: GatewayConfig,
        key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if (
            not config.live_enabled
            or not key
            or key.startswith("DUMMY")
            or key.strip() != key
        ):
            raise ValueError("missing or invalid provider credential")
        self.config = config
        self._key = key
        self._transport = transport

    def close(self) -> None:
        pass  # Each dispatch owns/closes its bounded client.

    def send(
        self, model_id: str, messages: tuple, max_tokens: int, request_id: str
    ) -> StubResult:
        return asyncio.run(self._send(model_id, messages, max_tokens, request_id))

    def send_with_deadline(
        self,
        model_id: str,
        messages: tuple,
        max_tokens: int,
        request_id: str,
        remaining: float,
    ) -> StubResult:
        if remaining <= 0:
            return StubResult("timeout")
        return asyncio.run(
            self._send(model_id, messages, max_tokens, request_id, remaining)
        )

    async def _send(
        self,
        model_id: str,
        messages: tuple,
        max_tokens: int,
        request_id: str,
        deadline: float | None = None,
    ) -> StubResult:
        deadline = (
            min(self.config.deadline_seconds, deadline)
            if deadline is not None
            else self.config.deadline_seconds
        )
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "X-Request-Id": request_id
            if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id)
            else "req-" + uuid.uuid4().hex,
        }
        # Only W3C trace context; never forward caller auth, baggage or identity.
        trace_headers: dict[str, str] = {}
        TraceContextTextMapPropagator().inject(trace_headers)
        if "traceparent" in trace_headers:
            headers["traceparent"] = trace_headers["traceparent"]
        provider_http_status = None
        try:
            async with (
                asyncio.timeout(deadline),
                httpx.AsyncClient(
                    transport=self._transport,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=httpx.Timeout(
                        min(5.0, deadline),
                        connect=min(2.0, deadline),
                    ),
                ) as client,
                client.stream(
                    "POST",
                    URL,
                    headers=headers,
                    json={
                        "model": model_id,
                        "messages": list(messages),
                        "max_tokens": max_tokens,
                        "stream": False,
                    },
                ) as response,
            ):
                provider_http_status = response.status_code
                if response.status_code != 200:
                    return StubResult(
                        "http",
                        status=response.status_code,
                        provider_http_status=provider_http_status,
                        failure_reason="upstream_http_error",
                    )
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(chunk) > self.config.max_response_bytes - len(data):
                        return StubResult(
                            "http",
                            status=502,
                            provider_http_status=provider_http_status,
                            failure_reason="response_too_large",
                        )
                    data.extend(chunk)
                payload = json.loads(data)
                usage = payload.get("usage", {})
                prompt = usage.get("prompt_tokens")
                completion = usage.get("completion_tokens")
                if any(
                    isinstance(v, bool) or not isinstance(v, int) or v < 0
                    for v in (prompt, completion)
                ):
                    prompt = completion = None
                choice = payload["choices"][0]
                content = choice["message"]["content"]
                if not isinstance(content, str) or not content:
                    return StubResult(
                        "http",
                        status=502,
                        provider_http_status=provider_http_status,
                        failure_reason=(
                            "no_text_token_limit"
                            if choice.get("finish_reason") == "length"
                            else "no_text_content"
                        ),
                    )
                return StubResult(
                    "ok",
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    content=content,
                    provider_http_status=provider_http_status,
                )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return StubResult("presend_fail")
        except httpx.TimeoutException:
            return StubResult("timeout")
        except TimeoutError:
            return StubResult("timeout")
        except httpx.TransportError:
            return StubResult("reset_after_send")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            return StubResult(
                "http",
                status=502,
                provider_http_status=provider_http_status,
                failure_reason="invalid_response",
            )
