"""PromQL query — read-only Prometheus metrics queries.

Strictly allow-listed: only /api/v1/query, /api/v1/query_range, and
related read-only endpoints.  Any write/delete operation is blocked.
"""

from __future__ import annotations

from langchain_core.tools import tool

from sentinel_agents.tools.base import DisallowedQueryError, register
from sentinel_agents.tools.base import validate_promql as _validate


@tool
def promql_query(
    query: str,
    operation: str = "instant",
    start: str = "",
    end: str = "",
    step: str = "60s",
) -> str:
    """Run a PromQL query against Prometheus and return the result.

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
        The Prometheus API response (T2.2 stub) or actual metrics (T2.3+).
    """
    # ── Allow-list enforcement ──
    try:
        _validate(operation, query)
    except DisallowedQueryError as exc:
        return f"❌ BLOCKED: {exc}"

    op = operation.lower().strip()
    endpoint = (
        "/api/v1/query" if op == "instant"
        else "/api/v1/query_range" if op == "range"
        else f"/api/v1/{op}"
    )

    # T2.2 stub — return what *would* be queried.
    return (
        f"[T2.2 STUB] Would query Prometheus:\n"
        f"  Endpoint: {endpoint}\n"
        f"  Query:    {query}\n"
        f"  Operation: {operation}\n"
        f"(Live execution will be wired in T2.3 — real metrics will appear here.)"
    )


register(promql_query, category="prometheus")
