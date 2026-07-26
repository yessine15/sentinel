"""PromQL query — read-only Prometheus metrics queries (live in T2.3).

Allow-list enforced before any HTTP call.

Environment variables
--------------------
PROMETHEUS_URL : str
    Base URL of the Prometheus HTTP API.
    Default for dev (port-forward): ``http://localhost:9090``
    Default for in-cluster: ``http://kube-prometheus-stack-prometheus.observability:9090``
"""

from __future__ import annotations

import os
from urllib.parse import urlencode, urljoin

from langchain_core.tools import tool

from sentinel_agents.tools.base import (
    DisallowedQueryError,
    _httpx_get,
    is_stub,
    register,
)
from sentinel_agents.tools.base import validate_promql as _validate

# Default for local dev (port-forward) or in-cluster.
_DEFAULT_PROMETHEUS_URL = os.environ.get(
    "PROMETHEUS_URL",
    "http://localhost:9090",
)


@tool
def promql_query(
    query: str,
    operation: str = "instant",
    start: str = "",
    end: str = "",
    step: str = "60s",
) -> str:
    """Run a PromQL query against Prometheus and return live metrics.

    Use this to retrieve time-series metrics: CPU/memory usage, request
    rates, error counts, pod restarts, node health, etc.

    Args:
        query: A valid PromQL expression (e.g.
            ``rate(http_requests_total[5m])``).
        operation: One of ``instant`` (current value), ``range``
            (values over a time window), ``labels``, ``label_values``,
            ``series``, ``targets``, ``rules``, ``alerts``, ``status``.
        start: RFC 3339 start time (required for ``range``).
        end: RFC 3339 end time (required for ``range``).
        step: Query resolution step width (default ``60s``).

    Returns:
        Live Prometheus JSON response (T2.3) or a stub preview
        (``RUN_MODE=stub``).
    """
    # ── Allow-list enforcement ──
    try:
        _validate(operation, query)
    except DisallowedQueryError as exc:
        return f"❌ BLOCKED: {exc}"

    op = operation.lower().strip()

    # Stub path — safe for unit tests
    if is_stub():
        endpoint = (
            "/api/v1/query" if op == "instant"
            else "/api/v1/query_range" if op == "range"
            else f"/api/v1/{op}"
        )
        return (
            f"[T2.3 STUB] Would query Prometheus:\n"
            f"  URL:       {_DEFAULT_PROMETHEUS_URL}{endpoint}\n"
            f"  Query:     {query}\n"
            f"  Operation: {operation}"
        )

    # ── Live execution ──
    if op == "instant":
        endpoint = "/api/v1/query"
        params = {"query": query}
    elif op == "range":
        endpoint = "/api/v1/query_range"
        params = {"query": query, "start": start, "end": end, "step": step}
    elif op == "labels":
        endpoint = "/api/v1/labels"
        params = {}
    elif op == "label_values":
        endpoint = "/api/v1/labels"
        params = {}
    elif op == "series":
        endpoint = "/api/v1/series"
        params = {"match[]": query} if query else {}
    elif op == "targets":
        endpoint = "/api/v1/targets"
        params = {}
    elif op == "rules":
        endpoint = "/api/v1/rules"
        params = {}
    elif op == "alerts":
        endpoint = "/api/v1/alerts"
        params = {}
    elif op == "status":
        endpoint = "/api/v1/status/config"
        params = {}
    else:
        return f"❌ Unknown operation: {operation}"

    url = urljoin(_DEFAULT_PROMETHEUS_URL, endpoint)
    response = _httpx_get(url, params=params)

    # If the response starts with '{' it's JSON from the API; if it
    # starts with '{' and contains "error" it's our own error wrapper.
    return response


register(promql_query, category="prometheus")
