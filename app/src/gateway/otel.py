"""OTLP traces/metrics with explicit endpoint and bounded redacted logs."""

import hashlib
import json
import logging
import os
from urllib.parse import urlsplit

import requests
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Observation
from opentelemetry.propagate import extract
from opentelemetry.sdk.metrics import (
    Counter,
    Histogram,
    MeterProvider,
    ObservableCounter,
)
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind

from .telemetry import Telemetry


class ExportedTelemetry(Telemetry):
    def __init__(self, endpoint: str, span_exporter=None, metric_reader=None):
        super().__init__()
        parsed = urlsplit(endpoint)
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not (
                parsed.scheme == "https"
                or (
                    parsed.scheme == "http"
                    and parsed.hostname in ("127.0.0.1", "localhost")
                )
            )
        ):
            raise ValueError("invalid telemetry endpoint")
        replica_name = os.environ.get("CONTAINER_APP_REPLICA_NAME")
        resource_attributes = {"service.name": "odyn-gateway"}
        if replica_name:
            # ACA provides a unique replica name. This lets traces and metrics
            # attribute a distributed acceptance run to actual replicas.
            resource_attributes["service.instance.id"] = replica_name
        resource = Resource.create(resource_attributes)
        session = requests.Session()
        session.trust_env = False
        self._session = session
        self.traces = TracerProvider(resource=resource)
        exporter = span_exporter or OTLPSpanExporter(
            endpoint=endpoint.rstrip("/") + "/v1/traces", timeout=2, session=session
        )
        self.traces.add_span_processor(
            BatchSpanProcessor(exporter, max_queue_size=256, max_export_batch_size=128)
        )
        reader = metric_reader or PeriodicExportingMetricReader(
            OTLPMetricExporter(
                endpoint=endpoint.rstrip("/") + "/v1/metrics",
                timeout=2,
                session=session,
                # Azure queries sum intervals; cumulative snapshots double count.
                preferred_temporality={
                    Counter: AggregationTemporality.DELTA,
                    Histogram: AggregationTemporality.DELTA,
                    ObservableCounter: AggregationTemporality.DELTA,
                },
            ),
            export_interval_millis=10000,
        )
        self.meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        self.meter = self.meter_provider.get_meter("odyn-gateway")
        self.requests = self.meter.create_counter("gateway.requests")
        self.duration = self.meter.create_histogram("gateway.duration", unit="s")
        self.upstream_duration = self.meter.create_histogram(
            "gateway.upstream.duration", unit="s"
        )
        self.upstream_requests = self.meter.create_counter("gateway.upstream.requests")
        self.tokens = self.meter.create_counter("gateway.tokens", unit="{token}")
        self.meter.create_observable_counter(
            "gateway.events",
            callbacks=[
                lambda _: [
                    Observation(value, {"event": name})
                    for name, value in list(self.metrics.items())
                ]
            ],
        )
        self.tracer = self.traces.get_tracer("odyn-gateway")

    def span(self, name: str, headers: dict | None = None):
        return self.tracer.start_as_current_span(
            name,
            context=extract(
                {k.lower(): v for k, v in headers.items() if k.lower() == "traceparent"}
            )
            if headers is not None
            else None,
            kind=SpanKind.SERVER if name == "gateway.chat" else SpanKind.CLIENT,
            record_exception=False,
            set_status_on_exception=False,
        )

    def log(self, **fields: object) -> None:
        fields = dict(fields)
        replica_name = os.environ.get("CONTAINER_APP_REPLICA_NAME")
        if replica_name:
            fields.setdefault("replica_name", replica_name)
        if "principal" in fields:
            fields["principal"] = hashlib.sha256(
                str(fields["principal"]).encode()
            ).hexdigest()[:16]
        super().log(**fields)
        logging.getLogger("odyn.telemetry").info(json.dumps(self.events[-1]))

    def record_response(self, status: int, duration: float) -> None:
        labels: dict[str, str | int] = {"status": status, "route": "/v1/chat"}
        self.requests.add(1, labels)
        self.duration.record(duration, labels)

    def record_upstream(
        self, kind: str, status: int, duration: float, model: str
    ) -> None:
        labels: dict[str, str | int] = {
            "route": "/v1/chat",
            "model": model,
            "outcome": kind,
            "status": status,
        }
        self.upstream_requests.add(1, labels)
        self.upstream_duration.record(duration, labels)

    def record_usage(self, prompt: int, completion: int, model: str) -> None:
        for direction, count in (("input", prompt), ("output", completion)):
            self.tokens.add(
                count, {"model": model, "direction": direction, "route": "/v1/chat"}
            )

    def close(self) -> None:
        self.traces.shutdown()
        self.meter_provider.shutdown()
        self._session.close()
