"""LogQL query — read-only Loki log queries.

Strictly allow-listed: only /loki/api/v1/query, /loki/api/v1/query_range,
and related read-only endpoints.  Any write/push operation is blocked.
"""

from __future__ import annotations

from langchain_core.tools import tool

from sentinel_agents.tools.base import DisallowedQueryError, register
from sentinel_agents.tools.base import validate_logql as _validate


@tool
def logql_query(
    query: str,
    operation: str = "query",
    start: str = "",
    end: str = "",
    limit: int = 100,
    direction: str = "backward",
) -> str:
    """Run a LogQL query against Loki and return matching log lines.

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
        The Loki API response (T2.2 stub) or actual log lines (T2.3+).
    """
    # ── Allow-list enforcement ──
    try:
        _validate(operation, query)
    except DisallowedQueryError as exc:
        return f"❌ BLOCKED: {exc}"

    op = operation.lower().strip()
    endpoint = (
        "/loki/api/v1/query_range" if op == "query_range"
        else f"/loki/api/v1/{op}"
    )

    # T2.2 stub — return what *would* be queried.
    return (
        f"[T2.2 STUB] Would query Loki:\n"
        f"  Endpoint:  {endpoint}\n"
        f"  Query:     {query}\n"
        f"  Limit:     {limit}\n"
        f"  Direction: {direction}\n"
        f"(Live execution will be wired in T2.3 — real logs will appear here.)"
    )


register(logql_query, category="loki")
