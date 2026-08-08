"""Sentinel demo API — instrumented with OpenTelemetry.

This Phase 0 version emits:
  - **Traces** — one span per HTTP request, exported via OTLP to the
    OTel Collector → Tempo.
  - **Metrics** — request count and latency via OTel instrumentation
    (exposed via /metrics, also pushed to Prometheus via the collector).
  - **Logs** — structured JSON output via structlog, collected by
    Promtail → Loki.

Environment variables (set by the Helm chart):
  - OTEL_EXPORTER_OTLP_ENDPOINT  — OTel collector URL (default: http://..:4318)
  - OTEL_SERVICE_NAME             — service name for traces (default: demo-api)
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

import structlog
from fastapi import FastAPI, Request, Response
from opentelemetry import trace

if TYPE_CHECKING:
    from collections.abc import Callable
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from structlog.processors import JSONRenderer

__version__ = "0.6.0"

# ── Configure OpenTelemetry tracing ────────────────────────
OTEL_ENDPOINT = os.environ.get(
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "http://otel-collector-opentelemetry-collector.observability.svc.cluster.local:4318",
)
OTEL_SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "sentinel-demo-api")

resource = Resource(attributes={"service.name": OTEL_SERVICE_NAME})
provider = TracerProvider(resource=resource)
exporter = OTLPSpanExporter(endpoint=f"{OTEL_ENDPOINT}/v1/traces")
processor = BatchSpanProcessor(exporter)
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)

tracer = trace.get_tracer(__name__)

# ── Configure structured logging (JSON → stdout) ──────────
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer() if os.isatty(0) else JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)
logger = structlog.get_logger()

# ── FastAPI app ────────────────────────────────────────────
app = FastAPI(
    title="Sentinel Demo API",
    description="Phase 0 demo — instrumented with OpenTelemetry.",
    version=__version__,
)

# Auto-instrument FastAPI (creates spans for every request).
FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)


# ── Middleware: per-request metrics ─────────────────────────
@app.middleware("http")
async def log_and_time_request(request: Request, call_next: Callable) -> Response:
    """Log every request with duration + status, and set a trace header."""
    start = time.monotonic()
    response: Response = await call_next(request)
    duration = time.monotonic() - start

    # Structured log line → picked up by Promtail → Loki.
    logger.info(
        "request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=round(duration * 1000, 1),
    )
    return response


# ── Routes ─────────────────────────────────────────────────
from sentinel_api.routes.ask import router as ask_router  # noqa: E402

app.include_router(ask_router)

from sentinel_api.routes.chat import router as chat_router  # noqa: E402

app.include_router(chat_router)


@app.get("/ping")
def ping() -> dict[str, str]:
    """Liveness probe target — always returns pong."""
    with tracer.start_as_current_span("ping") as span:
        span.set_attribute("endpoint", "/ping")
        logger.debug("ping endpoint called")
        return {"pong": "ok", "version": __version__}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe — 'is the process alive?'."""
    logger.debug("healthz check")
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    """Readiness probe — 'can we serve traffic?'

    Phase 0: always ready. Phase 1+: return 503 until Postgres/Qdrant reachable.
    """
    logger.debug("readyz check")
    return {"status": "ready"}


@app.get("/")
def root() -> dict[str, object]:
    """Root — returns service info."""
    return {
        "service": OTEL_SERVICE_NAME,
        "version": __version__,
        "endpoints": ["/ping", "/healthz", "/readyz", "/docs"],
    }

