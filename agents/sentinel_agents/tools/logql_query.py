"""LogQL query — read-only Loki log queries (live in T2.3).

Allow-list enforced before any HTTP call.

Environment variables
--------------------
LOKI_URL : str
    Base URL of the Loki HTTP API.
    Default for dev (port-forward): ``http://localhost:3100``
    Default for in-cluster: ``http://loki.observability:3100``
"""

from __future__ import annotations

import os
from urllib.parse import urljoin

from langchain_core.tools import tool

from sentinel_agents.tools.base import (
    DisallowedQueryError,
    _httpx_get,
    is_stub,
    register,
)
from sentinel_agents.tools.base import validate_logql as _validate

# Default for local dev (port-forward) or in-cluster.
_DEFAULT_LOKI_URL = os.environ.get(
    "LOKI_URL",
    "http://localhost:3100",
)


@tool
def logql_query(
    query: str,
    operation: str = "query",
    start: str = "",
    end: str = "",
    limit: int = 100,
    direction: str = "backward",
) -> str:
    """Run a LogQL query against Loki and return live log lines.

    Use this to search application and system logs: error messages,
    slow requests, crash traces, access logs, etc.

    Args:
        query: A valid LogQL expression (e.g.
            ``{app="demo-api"} |= "error"``).
        operation: One of ``query`` (instant), ``query_range``
            (over a time window), ``labels``, ``label_values``,
            ``series``, ``tail``.
        start: RFC 3339 start time (for ``query_range``).
        end: RFC 3339 end time (for ``query_range``).
        limit: Maximum number of log lines to return (default 100).
        direction: ``forward`` or ``backward`` (default).

    Returns:
        Live Loki JSON response (T2.3) or a stub preview
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
            "/loki/api/v1/query_range" if op == "query_range"
            else f"/loki/api/v1/{op}"
        )
        return (
            f"[T2.3 STUB] Would query Loki:\n"
            f"  URL:       {_DEFAULT_LOKI_URL}{endpoint}\n"
            f"  Query:     {query}\n"
            f"  Limit:     {limit}\n"
            f"  Direction: {direction}"
        )

    # ── Live execution ──
    if op == "query_range":
        endpoint = "/loki/api/v1/query_range"
        params: dict[str, str] = {
            "query": query,
            "start": start,
            "end": end,
            "limit": str(limit),
            "direction": direction,
        }
    elif op == "query":
        endpoint = "/loki/api/v1/query"
        params = {
            "query": query,
            "limit": str(limit),
            "direction": direction,
        }
    elif op == "labels":
        endpoint = "/loki/api/v1/labels"
        params = {}
    elif op == "label_values":
        endpoint = "/loki/api/v1/label"
        params = {}
    elif op == "series":
        endpoint = "/loki/api/v1/series"
        params = {"match[]": query} if query else {}
    elif op == "tail":
        endpoint = "/loki/api/v1/tail"
        params = {"query": query, "limit": str(limit)}
    else:
        return f"❌ Unknown operation: {operation}"

    url = urljoin(_DEFAULT_LOKI_URL, endpoint)
    return _httpx_get(url, params=params)


register(logql_query, category="loki")
