"""Request pipeline for injected stub or approved live providers.

auth (401/403) -> schema+policy (400/413/415/422) -> per-principal + global
rate limit (429+Retry-After) -> breaker check (503) -> budget reserve
(429 exhausted / 503 store down) -> dispatch (presend-only retry, app
deadline) -> settle exactly once -> safe status mapping.

Every response: shared error envelope + X-Request-Id. Caller request IDs
are validated/bounded, never trusted as identity.
"""

from __future__ import annotations

import random
import time
import uuid
from collections.abc import Callable
from typing import Any

from opentelemetry.trace import Status, StatusCode

from .admission import AtomicAdmission, AzureRows, RateRejected
from .auth import JwtValidator, Rs256Validator
from .budget import BudgetLedger, estimate_tokens
from .config import GatewayConfig
from .errors import (
    AUTH_REQUIRED,
    FORBIDDEN,
    RATE_LIMITED,
    UPSTREAM_BAD,
    UPSTREAM_TIMEOUT,
    UPSTREAM_UNAVAILABLE,
    ApiError,
)
from .limiter import (
    AdmissionDeadlineExceeded,
    BudgetExceeded,
    MemoryStore,
    RateLimiter,
    StoreUnavailable,
)
from .provider import OpenRouter
from .resilience import Breaker, is_presend_retryable
from .schema import validate_chat
from .telemetry import Telemetry
from .upstream import StubResult, StubUpstream


def new_request_id(provided: str | None) -> str:
    if (
        isinstance(provided, str)
        and 1 <= len(provided) <= 64
        and provided.replace("-", "").replace("_", "").isalnum()
    ):
        return provided
    return "req-" + uuid.uuid4().hex[:12]


def _err(code_msg: tuple, request_id: str) -> ApiError:
    code, status, message = code_msg
    return ApiError(code=code, message=message, status=status, request_id=request_id)


class Gateway:
    def __init__(
        self,
        config: GatewayConfig | None = None,
        now: float | None = None,
        *,
        validator: JwtValidator | Rs256Validator | None = None,
        store: MemoryStore | None = None,
        telemetry: Telemetry | None = None,
        upstream: StubUpstream | OpenRouter | None = None,
        admission: AtomicAdmission | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        retry_jitter: Callable[[], float] | None = None,
    ) -> None:
        self.config = config or GatewayConfig()
        self.store = store if store is not None else MemoryStore()
        self.telemetry = telemetry if telemetry is not None else Telemetry()
        self.breaker = Breaker()
        self.per_client = RateLimiter(
            self.config.per_principal_rps, self.config.per_principal_burst
        )
        self.global_lim = RateLimiter(self.config.global_rps, self.config.global_burst)
        self.admission = admission
        self.ledger: BudgetLedger | AtomicAdmission = admission or BudgetLedger(
            self.store, self.config
        )
        # Provider stub mode must not implicitly enable fixture authentication.
        self.validator = (
            validator
            if validator is not None
            else Rs256Validator(
                issuer=self.config.issuer,
                audience=self.config.audience,
            )
        )
        self.upstream = upstream if upstream is not None else StubUpstream()
        self._clock = clock if now is None else lambda: now
        self._wall_clock = wall_clock
        self._sleep = sleep
        self._retry_jitter = retry_jitter or (lambda: random.uniform(0.025, 0.1))
        if self.admission is not None:
            self.admission.deadline_clock = lambda: self.now
            if isinstance(self.admission.rows, AzureRows):
                self.admission.rows.deadline_clock = lambda: self.now
        elif isinstance(self.ledger, BudgetLedger):
            self.ledger.deadline_clock = lambda: self.now
        self.resources: list[Any] = []

    @property
    def now(self) -> float:
        """Elapsed-time rules use monotonic time; JWTs use wall time separately."""
        return self._clock()

    def _require_remaining(self, deadline: float) -> None:
        if self.now >= deadline:
            raise AdmissionDeadlineExceeded()

    # -- health ---------------------------------------------------------
    def healthz(self) -> tuple[int, dict]:
        rid = new_request_id(None)
        return 200, {"status": "ok", "request_id": rid}

    def readyz(self) -> tuple[int, dict]:
        rid = new_request_id(None)
        if self.store.down or (
            self.admission is not None and not self.admission.probe()
        ):
            err = _err(UPSTREAM_UNAVAILABLE, rid)
            return err.status, err.envelope()
        return 200, {
            "status": "ready",
            "request_id": rid,
            "upstream_mode": self.upstream_mode(),
        }

    def upstream_mode(self) -> str:
        return self.config.upstream_mode

    # -- chat -----------------------------------------------------------
    def chat(
        self,
        headers: dict,
        raw_body: bytes,
        *,
        started_at: float | None = None,
        deadline: float | None = None,
    ) -> tuple[int, dict, dict]:
        """Returns (status, body, response_headers). Never raises."""
        headers = {
            **headers,
            "X-Request-Id": new_request_id(headers.get("X-Request-Id")),
        }
        start = self.now if started_at is None else started_at
        deadline = (
            deadline if deadline is not None else start + self.config.deadline_seconds
        )
        try:
            with self.telemetry.span("gateway.chat", headers=headers) as span:
                response = self._chat(headers, raw_body, deadline)
                if span is not None:
                    span.set_attribute("http.route", "/v1/chat")
                    span.set_attribute("http.status_code", response[0])
                    span.set_attribute("http.response.status_code", response[0])
                    span.set_attribute("request_id", headers["X-Request-Id"])
                duration = max(0, self.now - start)
                self.telemetry.record_response(response[0], duration)
                self.telemetry.log(
                    request_id=headers["X-Request-Id"],
                    route="/v1/chat",
                    status=response[0],
                    latency_ms=round(duration * 1000, 3),
                )
                return response
        except AdmissionDeadlineExceeded:
            rid = headers["X-Request-Id"]
            self.telemetry.count("request_deadline_exceeded")
            duration = max(0, self.now - start)
            self.telemetry.log(
                request_id=rid,
                route="/v1/chat",
                status=504,
                latency_ms=round(duration * 1000, 3),
            )
            self.telemetry.record_response(504, duration)
            err = _err(UPSTREAM_TIMEOUT, rid)
            return err.status, err.envelope(), {"X-Request-Id": rid}
        except StoreUnavailable:
            rid = headers["X-Request-Id"]
            self.telemetry.count("store_unavailable")
            duration = max(0, self.now - start)
            self.telemetry.log(
                request_id=rid,
                route="/v1/chat",
                status=503,
                latency_ms=round(duration * 1000, 3),
            )
            self.telemetry.record_response(503, duration)
            err = _err(UPSTREAM_UNAVAILABLE, rid)
            return err.status, err.envelope(), {"X-Request-Id": rid}
        finally:
            self.breaker.finish_probe()

    def _chat(
        self, headers: dict, raw_body: bytes, deadline: float
    ) -> tuple[int, dict, dict]:
        self._require_remaining(deadline)
        rid = new_request_id(headers.get("X-Request-Id"))
        out_headers = {"X-Request-Id": rid}
        authorization = headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        claims, reason = self.validator.validate(
            token.strip() if scheme.lower() == "bearer" else None,
            now=self._wall_clock(),
        )
        if claims is None:
            err = _fail_auth(reason, rid)
            self.telemetry.count("auth_401" if err.status == 401 else "auth_403")
            return err.status, err.envelope(), out_headers
        self._require_remaining(deadline)
        principal = claims.principal

        req, verr = validate_chat(
            headers.get("Content-Type"), raw_body, self.config, rid
        )
        if verr is not None:
            return verr.status, verr.envelope(), out_headers
        assert req is not None  # narrowing: verr None implies valid request
        self._require_remaining(deadline)

        try:
            ok, wait = (
                (True, 0.0)
                if self.admission is not None
                else self.per_client.allow(self.store, f"rl:{principal}", self.now)
            )
        except StoreUnavailable:
            err = _err(UPSTREAM_UNAVAILABLE, rid)
            return err.status, err.envelope(), out_headers
        if not ok:
            out_headers["Retry-After"] = str(max(1, int(wait + 0.999)))
            err = _err(RATE_LIMITED, rid)
            self.telemetry.count("rate_429")
            return err.status, err.envelope(), out_headers
        try:
            ok, wait = (
                (True, 0.0)
                if self.admission is not None
                else self.global_lim.allow(self.store, "rl:global", self.now)
            )
        except StoreUnavailable:
            err = _err(UPSTREAM_UNAVAILABLE, rid)
            return err.status, err.envelope(), out_headers
        if not ok:
            out_headers["Retry-After"] = str(max(1, int(wait + 0.999)))
            err = _err(RATE_LIMITED, rid)
            self.telemetry.count("rate_429")
            return err.status, err.envelope(), out_headers

        if self.breaker.is_open(self.now):
            err = _err(UPSTREAM_UNAVAILABLE, rid)
            return err.status, err.envelope(), out_headers

        entry = self.config.models[req.model_alias]
        est_tokens = estimate_tokens(req.messages, req.max_tokens)
        # Use the same conservative framing bound for token and spend admission.
        est_usd = (
            (est_tokens - req.max_tokens) * entry.input_usd_per_1k
            + req.max_tokens * entry.output_usd_per_1k
        ) / 1000.0
        try:
            res = self.ledger.reserve(principal, est_tokens, est_usd, deadline=deadline)
        except AdmissionDeadlineExceeded:
            raise
        except RateRejected as ex:
            out_headers["Retry-After"] = str(max(1, int(ex.wait + 0.999)))
            self.telemetry.count("rate_429")
            err = _err(RATE_LIMITED, rid)
            return err.status, err.envelope(), out_headers
        except BudgetExceeded:
            out_headers["Retry-After"] = "60"
            err = _err(RATE_LIMITED, rid)
            self.telemetry.count("budget_429")
            return err.status, err.envelope(), out_headers
        except StoreUnavailable:
            err = _err(UPSTREAM_UNAVAILABLE, rid)
            return err.status, err.envelope(), out_headers

        # dispatch: at most one presend retry, bounded by app deadline
        attempts = 0
        result = None
        while True:
            attempts += 1
            attempt_start = self.now
            with self.telemetry.span("provider.chat") as provider_span:
                remaining = deadline - self.now
                if hasattr(self.upstream, "send_with_deadline"):
                    result = self.upstream.send_with_deadline(
                        entry.provider_model_id,
                        req.messages,
                        req.max_tokens,
                        rid,
                        remaining,
                    )
                else:
                    if remaining <= 0:
                        result = StubResult("timeout")
                    else:
                        result = self.upstream.send(
                            entry.provider_model_id, req.messages, req.max_tokens, rid
                        )
                if provider_span is not None:
                    provider_span.set_attribute("request_id", rid)
                    provider_span.set_attribute("server.address", "openrouter.ai")
                    provider_span.set_attribute("http.request.method", "POST")
                    provider_span.set_attribute("provider.outcome", result.kind)
                    if result.provider_http_status is not None:
                        provider_span.set_attribute(
                            "provider.http_status", result.provider_http_status
                        )
                    if result.failure_reason is not None:
                        provider_span.set_attribute(
                            "provider.failure_reason", result.failure_reason
                        )
                    if result.kind in ("ok", "http"):
                        http_status = (
                            result.provider_http_status
                            if result.provider_http_status is not None
                            else result.status
                        )
                        provider_span.set_attribute(
                            "http.response.status_code", http_status
                        )
                        provider_span.set_attribute("http.status_code", http_status)
                    if result.kind != "ok":
                        provider_span.set_status(Status(StatusCode.ERROR))
            self.telemetry.record_upstream(
                result.kind,
                result.status,
                max(0, self.now - attempt_start),
                req.model_alias,
            )
            if (
                result.kind == "presend_fail"
                and attempts < 2
                and is_presend_retryable("refused")
            ):
                # Jitter desynchronizes replicas and consumes the shared deadline.
                remaining = deadline - self.now
                if remaining > 0:
                    self._sleep(min(max(0.0, self._retry_jitter()), 0.1, remaining))
                    continue
            break

        metrics = self.telemetry.metrics
        if result.kind == "presend_fail":
            self.ledger.release(res, deadline=deadline)  # provably unsent
            self.breaker.record("conn_fail", self.now)
            self.telemetry.log(
                request_id=rid,
                route="/v1/chat",
                principal=principal,
                status=502,
                model=req.model_alias,
                retries=attempts - 1,
            )
            err = _err(UPSTREAM_BAD, rid)
            return err.status, err.envelope(), out_headers
        if result.kind == "timeout":
            self.ledger.settle(res, None, None, metrics, deadline=deadline)
            self.breaker.record("timeout", self.now)
            self.telemetry.log(
                request_id=rid,
                route="/v1/chat",
                principal=principal,
                status=504,
                model=req.model_alias,
                retries=attempts - 1,
            )
            err = _err(UPSTREAM_TIMEOUT, rid)
            return err.status, err.envelope(), out_headers
        if result.kind == "reset_after_send":
            self.ledger.settle(res, None, None, metrics, deadline=deadline)
            self.breaker.record("reset", self.now)
            self.telemetry.log(
                request_id=rid,
                route="/v1/chat",
                principal=principal,
                status=502,
                model=req.model_alias,
                retries=attempts - 1,
            )
            err = _err(UPSTREAM_BAD, rid)
            return err.status, err.envelope(), out_headers
        if result.kind == "http":
            self.ledger.settle(res, None, None, metrics, deadline=deadline)
            if result.status == 429:
                self.breaker.record("upstream_429", self.now)
                out_headers["Retry-After"] = "5"
            elif result.status == 500:
                self.breaker.record("upstream_5xx", self.now)
            elif result.status in (401, 402):
                # credential/billing fault: NEVER trips breaker; separate alert
                self.telemetry.count("upstream_credential_fault")
            else:
                self.breaker.record("upstream_5xx", self.now)
            err = _err(
                UPSTREAM_UNAVAILABLE
                if result.status in (401, 402, 429)
                else UPSTREAM_BAD,
                rid,
            )
            if result.status == 429 and "Retry-After" not in out_headers:
                out_headers["Retry-After"] = "5"
            self.telemetry.log(
                request_id=rid,
                route="/v1/chat",
                principal=principal,
                status=err.status,
                model=req.model_alias,
                retries=attempts - 1,
                provider_http_status=result.provider_http_status,
                failure_reason=result.failure_reason,
            )
            return err.status, err.envelope(), out_headers
        # A successful response ends the current consecutive-failure streak.
        self.breaker.success(self.now)
        # ok
        actual: dict[str, object]
        if result.prompt_tokens is None or result.completion_tokens is None:
            self.ledger.settle(
                res, None, None, metrics, deadline=deadline
            )  # missing usage: retain
            actual = {"usage": "unknown"}
        else:
            actual_t = result.prompt_tokens + result.completion_tokens
            actual_u = (
                result.prompt_tokens / 1000.0 * entry.input_usd_per_1k
                + result.completion_tokens / 1000.0 * entry.output_usd_per_1k
            )
            self.ledger.settle(res, actual_t, actual_u, metrics, deadline=deadline)
            self.telemetry.record_usage(
                result.prompt_tokens, result.completion_tokens, req.model_alias
            )
            actual = {
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "usd": round(actual_u, 6),
            }
        self.telemetry.log(
            request_id=rid,
            route="/v1/chat",
            principal=principal,
            status=200,
            model=req.model_alias,
            retries=attempts - 1,
            **{
                k: v
                for k, v in actual.items()
                if k in ("prompt_tokens", "completion_tokens", "usd_estimate")
            },
        )
        return (
            200,
            {
                "data": {"model": req.model_alias, "content": result.content, **actual},
                "request_id": rid,
            },
            out_headers,
        )


def _fail_auth(reason: str | None, rid: str) -> ApiError:
    # 401 for any authentication failure; 403 reserved for valid identity
    # lacking scope/role (validator reports those as auth failures with
    # distinct reasons mapped here).
    if reason in ("missing_scope", "missing_role"):
        code, status, message = FORBIDDEN
        return ApiError(code=code, message=message, status=status, request_id=rid)
    code, status, message = AUTH_REQUIRED
    return ApiError(code=code, message=message, status=status, request_id=rid)
