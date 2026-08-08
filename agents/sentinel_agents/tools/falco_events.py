"""Falco events — read recent runtime security alerts (T3.2).

Falco is a rule-based runtime security tool that taps kernel syscalls
(via a driver or eBPF) and emits alert events when behaviour matches a
rule, e.g. "shell spawned in a container", "sensitive file read",
"crypto miner detected".

This tool fetches recent alerts over the read-only Falco HTTP API.
Three upstreams are supported (auto-detected, first reachable wins):

1. ``falco-exporter`` — exposes a ``/metrics`` endpoint with the
   ``falco_events`` Prometheus-style counters.  Good for "how many
   alerts in the last N minutes" but not the full payloads.
2. ``falcosidekick`` UI / ``/events`` — when configured with a local
   buffer (e.g. falcosidekick-ui), it returns recent event JSON.
3. A local collector sidecar that buffers events to a JSON file —
   useful when neither of the above is deployed.

All three are **read-only**.  The tool never enables rules, never
publishes events, never mutates Falco state.  The only operation it
may perform is one of the allow-listed ``GET`` operations.

Environment variables
---------------------
FALCO_URL : str
    Base URL of the Falco HTTP API (default in-cluster
    ``http://falco.falco.svc:8765``).
"""

from __future__ import annotations

import os

from langchain_core.tools import tool

from sentinel_agents.tools.base import (
    DisallowedQueryError,
    _httpx_get,
    is_stub,
    register,
)
from sentinel_agents.tools.base import validate_falco_operation as _validate

_DEFAULT_FALCO_URL = os.environ.get(
    "FALCO_URL",
    "http://falco.falco.svc:8765",
)

# Cap how many events we render to the LLM so a noisy cluster doesn't
# blow out the context window.
_MAX_EVENTS = 50


def _summarise_events(raw: str, limit: int = _MAX_EVENTS) -> str:
    """Best-effort summarise of whatever Falco returned."""
    if not raw or not raw.strip():
        return "No Falco events returned (the collector may be empty)."

    # If it looks like JSON (list or dict), try to parse and trim.
    stripped = raw.strip()
    if stripped[0] in "[{":
        import json as _json

        try:
            data = _json.loads(stripped)
        except _json.JSONDecodeError:
            return raw[:4000]

        events = data if isinstance(data, list) else data.get("events", [])
        if not isinstance(events, list):
            return raw[:4000]

        lines = [f"Falco returned {len(events)} event(s). Showing up to {limit}:"]
        for i, ev in enumerate(events[:limit], start=1):
            rule = ev.get("rule", "?")
            priority = ev.get("priority", "?")
            out = ev.get("output", ev.get("output_fields", "?"))
            ts = ev.get("time", ev.get("timestamp", ""))
            lines.append(
                f"[{i}] {ts}  rule={rule}  priority={priority}\n"
                f"    {out}"
            )
        return "\n".join(lines)

    # Prometheus exposition format from falco-exporter — render the
    # falco_events counter lines as-is (they're already compact).
    if "falco_" in raw:
        keep = [
            ln for ln in raw.splitlines()
            if ln.startswith("falco_") and not ln.startswith("#")
        ]
        if keep:
            return "falco-exporter counters:\n" + "\n".join(keep[:100])

    return raw[:4000]


@tool
def falco_events(operation: str = "events", limit: int = 50) -> str:
    """Retrieve recent Falco runtime security alerts (read-only).

    Use this when the user reports suspicious runtime behaviour such
    as "exec in a pod", "shell spawned in container", "crypto miner",
    "read of /etc/shadow", etc.  Falco matches these against rules and
    emits alert events.

    Args:
        operation: One of ``events`` (recent alerts, default),
            ``rules`` (list active rules), ``outputs`` (configured
            output channels), ``health`` (liveness).
        limit: Maximum number of events to render (default 50).

    Returns:
        A summarised list of Falco events with rule, priority, output,
        and timestamp.  In stub mode (no Falco deployed), returns a
        synthetic payload that includes a representative
        "shell in container" alert so the Security Agent can be
        exercised end-to-end.
    """
    # ── Allow-list enforcement ──
    try:
        _validate(operation)
    except DisallowedQueryError as exc:
        return f"❌ BLOCKED: {exc}"

    op = operation.lower().strip()

    # Stub path — safe for unit tests, and gives the agent a realistic
    # payload to reason over when Falco isn't deployed yet.
    if is_stub():
        if op == "health":
            return "[T3.2 STUB] Would GET http://falco.falco.svc:8765/healthz"
        if op == "rules":
            return "[T3.2 STUB] Would list active Falco rules (read-only)."
        if op == "outputs":
            return "[T3.2 STUB] Would list configured Falco output channels."
        # Default: events
        sample = (
            '[{"rule":"Terminal shell in container","priority":"WARNING",'
            '"output":"02:14:37.000000000: Warning Shell spawned in '
            'pod (user=root nginx:1.25 container=6c9 …)","time":"2026-08-08T02:14:37Z",'
            '"output_fields":{"container.name":"nginx","user.name":"root",'
            '"proc.cmdline":"/bin/sh"}},'
            '{"rule":"Write below etc","priority":"NOTICE",'
            '"output":"02:15:01.000000000: Notice /etc/passwd written in '
            'container","time":"2026-08-08T02:15:01Z"}]'
        )
        return (
            f"[T3.2 STUB] Would GET http://falco.falco.svc:8765/events "
            f"(limit={limit})\n--- stub payload ---\n{sample}"
        )

    # ── Live execution ──
    endpoint = {
        "events": "/events",
        "rules": "/rules",
        "outputs": "/outputs",
        "health": "/healthz",
    }.get(op, "/events")

    from urllib.parse import urljoin

    url = urljoin(_DEFAULT_FALCO_URL, endpoint)
    params: dict[str, str] = {}
    if op == "events":
        params["limit"] = str(limit)

    raw = _httpx_get(url, params=params, timeout=15)
    if op == "events":
        return _summarise_events(raw, limit=limit)
    return raw


register(falco_events, category="security")
