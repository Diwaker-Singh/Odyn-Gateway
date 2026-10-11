"""Deployment factory. Managed identity only; no implicit developer login."""

from __future__ import annotations

import logging
import os
import threading

from .admission import AtomicAdmission, AzureRows, LocalRows
from .api import Gateway
from .auth import Rs256Validator
from .config import GatewayConfig
from .jwks import TrustedJwks
from .provider import OpenRouter
from .telemetry import Telemetry
from .upstream import StubUpstream


def build_gateway(config: GatewayConfig) -> Gateway:
    telemetry = Telemetry()
    if config.telemetry_endpoint:
        from .otel import ExportedTelemetry

        telemetry = ExportedTelemetry(config.telemetry_endpoint)
        logger = logging.getLogger("odyn.telemetry")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
    upstream = (
        OpenRouter(config, os.environ.get("OPENROUTER_API_KEY", ""))
        if config.upstream_mode == "live"
        else StubUpstream()
    )
    admission = AtomicAdmission(LocalRows(), config)
    keys = TrustedJwks(config.jwks_url) if config.jwks_url else None
    if config.runtime_environment == "azure":
        from azure.data.tables import TableClient
        from azure.identity import ManagedIdentityCredential

        credential = ManagedIdentityCredential(
            client_id=config.managed_identity_client_id
        )
        client = TableClient(
            endpoint=config.table_endpoint,
            table_name=config.table_name,
            credential=credential,
            retry_total=0,
            connection_timeout=2,
            read_timeout=3,
        )
        admission = AtomicAdmission(
            AzureRows(client),
            config,
            process_lock=threading.Lock(),
            metrics=telemetry.metrics,
        )
    validator = Rs256Validator(
        issuer=config.issuer,
        audience=config.audience,
        required_scope=config.delegated_scope,
        required_role=config.machine_app_role,
    )
    if keys is not None:
        validator.jwks = keys
    gw = Gateway(
        config,
        validator=validator,
        upstream=upstream,
        admission=admission,
        telemetry=telemetry,
    )
    gw.resources = [r for r in (keys, upstream) if r is not None]
    if config.runtime_environment == "azure":
        gw.resources.extend([client, credential])
    gw.resources.append(telemetry)
    return gw
