"""Demo defaults (user decision section 2). Configurable, not production sizing.

- per-principal: 5 req/s sustained, burst 10 (token bucket capacity 10)
- global: 20 req/s sustained, burst 40
- max body 32 KiB; max output tokens 1024; daily per-principal 100k tokens
- one live model alias + multi-entry allowlist structure
- spend enforced alongside tokens via versioned price table (per 1k tokens)
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelEntry:
    provider_model_id: str
    input_usd_per_1k: float
    output_usd_per_1k: float


@dataclass(frozen=True)
class GatewayConfig:
    per_principal_rps: float = 5.0
    per_principal_burst: int = 10
    global_rps: float = 20.0
    global_burst: int = 40
    max_body_bytes: int = 32 * 1024
    max_tokens_ceiling: int = 1024
    daily_token_limit: int = 100_000
    daily_spend_usd_limit: float = 5.0
    price_table_version: str = "2026-10-09-demo"
    models: dict[str, ModelEntry] = field(
        default_factory=lambda: {
            # One live alias; more entries only with sourced pricing + tested behavior.
            "default": ModelEntry("openrouter/auto", 0.001, 0.002),
        }
    )
    allowed_algorithms: tuple = ("RS256",)
    issuer: str = "https://login.microsoftonline.com/<tenant-id>/v2.0"
    audience: str = "<api-app-id>"
    upstream_mode: str = "stub"  # stub default; prod config must reject stub
    network_enforced: bool = False  # false until private-origin proof exists
    runtime_environment: str = "local"
    table_endpoint: str = ""
    table_name: str = "ledger"
    managed_identity_client_id: str = ""
    jwks_url: str = ""
    delegated_scope: str = "chat.invoke"
    machine_app_role: str = "Chat.Invoke.Application"
    cors_origins: tuple[str, ...] = ()
    telemetry_endpoint: str = ""
    live_enabled: bool = False
    max_response_bytes: int = 256 * 1024
    deadline_seconds: float = 15.0

    def __post_init__(self) -> None:
        for name in (
            "per_principal_rps",
            "per_principal_burst",
            "global_rps",
            "global_burst",
            "max_body_bytes",
            "max_tokens_ceiling",
            "daily_token_limit",
            "daily_spend_usd_limit",
            "max_response_bytes",
            "deadline_seconds",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"invalid {name}")
        if self.upstream_mode not in ("stub", "live"):
            raise ValueError("invalid upstream_mode")
        if self.runtime_environment not in ("local", "azure"):
            raise ValueError("invalid runtime_environment")
        if self.allowed_algorithms != ("RS256",):
            raise ValueError("invalid allowed_algorithms")
        if not self.models or any(
            not alias
            or not entry.provider_model_id
            or not math.isfinite(entry.input_usd_per_1k)
            or not math.isfinite(entry.output_usd_per_1k)
            or entry.input_usd_per_1k < 0
            or entry.output_usd_per_1k < 0
            for alias, entry in self.models.items()
        ):
            raise ValueError("invalid model_allowlist")
        if any(origin == "*" for origin in self.cors_origins):
            raise ValueError("invalid cors_origins")
        if self.runtime_environment == "azure":
            match = re.fullmatch(
                r"https://login\.microsoftonline\.com/([0-9a-fA-F-]{36})/v2\.0",
                self.issuer,
            )
            if not match or not self.audience or "<" in self.audience:
                raise ValueError("invalid identity configuration")
            expected = (
                f"https://login.microsoftonline.com/{match[1]}/discovery/v2.0/keys"
            )
            if self.jwks_url != expected:
                raise ValueError("invalid jwks_url")
            if not re.fullmatch(
                r"https://[a-z0-9]+\.table\.core\.windows\.net/?", self.table_endpoint
            ):
                raise ValueError("invalid table_endpoint")
            object.__setattr__(
                self, "table_endpoint", self.table_endpoint.removesuffix("/")
            )
            if not self.managed_identity_client_id:
                raise ValueError("missing managed_identity_client_id")
        if self.upstream_mode == "live" and (
            not self.live_enabled
            or not self.price_table_version.strip()
            or self.price_table_version.endswith("demo")
        ):
            raise ValueError("live mode requires approval and sourced prices")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> GatewayConfig:
        values = os.environ if env is None else env
        if values.get("UPSTREAM_MODE") == "live" and "MODEL_PRICES_JSON" not in values:
            raise ValueError("live mode requires explicit sourced model prices")
        kwargs: dict = {}
        names = {
            "UPSTREAM_MODE": "upstream_mode",
            "IDP_ISSUER_URL": "issuer",
            "IDP_AUDIENCE": "audience",
            "JWKS_URL": "jwks_url",
            "GATEWAY_ENVIRONMENT": "runtime_environment",
            "TABLE_ENDPOINT": "table_endpoint",
            "TABLE_NAME": "table_name",
            "AZURE_CLIENT_ID": "managed_identity_client_id",
            "DELEGATED_SCOPE": "delegated_scope",
            "MACHINE_APP_ROLE": "machine_app_role",
            "PRICE_TABLE_VERSION": "price_table_version",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "telemetry_endpoint",
        }
        for key, field_name in names.items():
            if key in values:
                kwargs[field_name] = values[key]
        for key, field_name in (
            ("MAX_BODY_BYTES", "max_body_bytes"),
            ("MAX_TOKENS", "max_tokens_ceiling"),
            ("DAILY_TOKEN_LIMIT", "daily_token_limit"),
            ("PER_PRINCIPAL_BURST", "per_principal_burst"),
            ("GLOBAL_BURST", "global_burst"),
            ("MAX_RESPONSE_BYTES", "max_response_bytes"),
        ):
            if key in values:
                try:
                    kwargs[field_name] = int(values[key])
                except ValueError:
                    raise ValueError(f"invalid {field_name}") from None
        for key, field_name in (
            ("PER_PRINCIPAL_RPS", "per_principal_rps"),
            ("GLOBAL_RPS", "global_rps"),
            ("DAILY_SPEND_USD", "daily_spend_usd_limit"),
            ("DEADLINE_SECONDS", "deadline_seconds"),
        ):
            if key in values:
                try:
                    kwargs[field_name] = float(values[key])
                except ValueError:
                    raise ValueError(f"invalid {field_name}") from None
        kwargs["live_enabled"] = values.get("ODYN_LIVE_UPSTREAM_APPROVED") == "1"
        kwargs["cors_origins"] = tuple(
            filter(None, values.get("CORS_ORIGINS", "").split(","))
        )
        if "MODEL_PRICES_JSON" in values:
            try:
                data = json.loads(values["MODEL_PRICES_JSON"])
                kwargs["models"] = {
                    alias: ModelEntry(**entry) for alias, entry in data.items()
                }
            except (ValueError, TypeError, AttributeError):
                raise ValueError("invalid model_allowlist") from None
        return cls(**kwargs)


DEFAULTS = GatewayConfig()
