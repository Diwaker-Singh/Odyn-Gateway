"""FastAPI/ASGI adapter (M1). Thin over the tested stdlib core.

No policy duplicated here: auth, schema, limits, budget, retry, telemetry
all live in :class:`Gateway`. This layer only maps HTTP <-> pipeline and
enforces the raw body cap while receiving, BEFORE parsing/full buffering.
Stub upstream is selected by default; deployment mode is configured explicitly.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import ClientDisconnect

from .api import Gateway, new_request_id
from .config import GatewayConfig
from .errors import BAD_REQUEST, TOO_LARGE, ApiError
from .runtime import build_gateway


def create_app(
    gateway: Gateway | None = None, config: GatewayConfig | None = None
) -> FastAPI:
    gw = gateway or build_gateway(config or GatewayConfig.from_env())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            for resource in gw.resources:
                await run_in_threadpool(resource.close)

    app = FastAPI(
        title="odyn-gateway",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    if gw.config.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(gw.config.cors_origins),
            allow_methods=["POST", "GET"],
            allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
            expose_headers=["X-Request-Id", "Retry-After"],
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    app.state.gateway = gw

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        status, body = gw.healthz()
        return JSONResponse(
            status_code=status,
            content=body,
            headers={"X-Request-Id": body["request_id"]},
        )

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        status, body = gw.readyz()
        rid = body.get("request_id", "unknown")
        return JSONResponse(
            status_code=status, content=body, headers={"X-Request-Id": rid}
        )

    @app.post("/v1/chat")
    async def chat(request: Request) -> JSONResponse:
        started_at = time.monotonic()
        deadline = started_at + gw.config.deadline_seconds
        request_id = new_request_id(request.headers.get("x-request-id"))

        def reject(error: tuple[str, int, str]) -> JSONResponse:
            code, status, message = error
            duration = max(0.0, time.monotonic() - started_at)
            gw.telemetry.record_response(status, duration)
            gw.telemetry.log(
                request_id=request_id,
                route="/v1/chat",
                status=status,
                latency_ms=round(duration * 1000, 3),
            )
            return JSONResponse(
                status_code=status,
                content=ApiError(code, message, status, request_id).envelope(),
                headers={"X-Request-Id": request_id},
            )

        # Content-Length is only a hint; count streamed bytes even if absent or false.
        length = request.headers.get("content-length")
        if length is not None:
            if not length.isascii() or not length.isdecimal():
                return reject(BAD_REQUEST)
            # Avoid converting an arbitrarily long decimal header to an integer.
            normalized = length.lstrip("0") or "0"
            ceiling = str(gw.config.max_body_bytes)
            if len(normalized) > len(ceiling) or (
                len(normalized) == len(ceiling) and normalized > ceiling
            ):
                return reject(TOO_LARGE)
        raw = bytearray()
        try:
            async with asyncio.timeout_at(deadline):
                async for chunk in request.stream():
                    if len(chunk) > gw.config.max_body_bytes - len(raw):
                        return reject(TOO_LARGE)
                    raw.extend(chunk)
        except ClientDisconnect:
            return reject(BAD_REQUEST)
        except TimeoutError:
            gw.telemetry.count("request_deadline_exceeded")
            return reject(("upstream_timeout", 504, "Request deadline exceeded."))
        headers = {
            "Authorization": request.headers.get("authorization", ""),
            "Content-Type": request.headers.get("content-type", ""),
            "X-Request-Id": request_id,
            "traceparent": request.headers.get("traceparent", ""),
            "tracestate": request.headers.get("tracestate", ""),
        }
        # The synchronous pipeline may perform blocking store/provider I/O.
        status, body, out = await run_in_threadpool(
            lambda: gw.chat(
                headers,
                bytes(raw),
                started_at=started_at,
                deadline=deadline,
            )
        )
        return JSONResponse(status_code=status, content=body, headers=out)

    return app


def run_local() -> None:  # pragma: no cover - manual stub serving only
    import uvicorn

    if GatewayConfig().upstream_mode != "stub":
        raise RuntimeError("refusing non-stub local serve without approval")
    uvicorn.run(create_app(), host="127.0.0.1", port=8000)
