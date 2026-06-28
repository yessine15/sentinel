"""Sentinel demo API — a tiny FastAPI service.

This is the Phase 0 placeholder version. It exists so the container build
pipeline (T0.9) has something real to ship, ArgoCD (T0.7) has an image it
can pull, and we have endpoints (`/ping`, `/healthz`, `/readyz`) to test
the observability stack against in T0.10–T0.13.

The real thing (metrics + logs + traces via OpenTelemetry) lands in T0.13.
"""

from __future__ import annotations

from fastapi import FastAPI

__version__ = "0.1.0"

app = FastAPI(
    title="Sentinel Demo API",
    description="Phase 0 placeholder — real instrumentation lands in T0.13.",
    version=__version__,
)


@app.get("/ping")
def ping() -> dict[str, str]:
    """Liveness probe target — always returns pong."""
    return {"pong": "ok", "version": __version__}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe — 'is the process alive?'."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    """Readiness probe — 'can we serve traffic?'

    Phase 0: always ready. Phase 1+: return 503 until Postgres/Qdrant are
    reachable.
    """
    return {"status": "ready"}


@app.get("/")
def root() -> dict[str, object]:
    """Root — redirects humans to /docs in a real UI but returns JSON here."""
    return {
        "service": "sentinel-demo-api",
        "version": __version__,
        "endpoints": ["/ping", "/healthz", "/readyz", "/docs"],
    }
