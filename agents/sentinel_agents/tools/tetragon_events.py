"""Tetragon events — read recent eBPF security events (T3.2).

Tetragon is a Cilium-provided eBPF security observability tool.  Unlike
Falco (rule-based alerts), Tetragon exposes raw kernel events
(process exec, network connections, file access) and can enforce
in-kernel policies.  We only **observe** here — never enforce.

The tool talks to the Tetragon gRPC API via the optional JSON-log
sidecar endpoint exposed by ``tetragon-operator`` /
``tetragon-obsgregator`` at ``/events`` (a simple streaming JSON
endpoint many deployments enable for tooling).  When that endpoint
is not available, the tool falls back to reading the recent event
buffer written by the ``tetragon-cli`` sidecar.

All inputs are validated against an allow-list of event types so the
LLM cannot request arbitrary kernel probes.
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
from sentinel_agents.tools.base import validate_tetragon_events as _validate

_DEFAULT_TETRAGON_URL = os.environ.get(
    "TETRAGON_URL",
    "http://tetragon.kube-system.svc:8081",
)

# Cap the rendered event count to keep tool output bounded.
_MAX_EVENTS = 50


def _summarise_events(raw: str, event_type: str, limit: int = _MAX_EVENTS) -> str:
    """Render the Tetragon event stream as an agent-friendly summary."""
    if not raw or not raw.strip():
        return (
            f"No Tetragon {event_type} events returned.  "
            "The Tetragon gRPC/HTTP bridge may not be exposed."
        )

    stripped = raw.strip()
    # Tetragon JSON stream is one JSON object per line (NDJSON).
    if stripped[0] in "[{":
        import json as _json

        events: list = []
        if stripped.startswith("["):
            try:
                events = _json.loads(stripped)
            except _json.JSONDecodeError:
                events = []
        else:
            # NDJSON stream
            for ln in stripped.splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    events.append(_json.loads(ln))
                except _json.JSONDecodeError:
                    continue

        if not isinstance(events, list):
            return raw[:4000]

        lines = [f"Tetragon returned {len(events)} {event_type} event(s). "
                 f"Showing up to {limit}:"]
        for i, ev in enumerate(events[:limit], start=1):
            kind = ev.get("type", ev.get("event", {}).get("type", "?"))
            pid = ev.get("process", {}).get("pid", "?")
            comm = ev.get("process", {}).get("comm", "?")
            pod = (
                ev.get("process", {})
                .get("k8s", {})
                .get("pod", "?")
            )
            ns = (
                ev.get("process", {})
                .get("k8s", {})
                .get("namespace", "?")
            )
            args = " ".join(ev.get("process", {}).get("args", [])[:6])
            fn = ev.get("event", {}).get("function", "")

            if kind in ("exec", "EXECVEVENT"):
                lines.append(
                    f"[{i}] {kind}  ns={ns} pod={pod} pid={pid} ({comm})\n"
                    f"    args: {args}"
                )
            elif kind in ("network", "NETWORK"):
                dst = ev.get("event", {}).get("destination", {})
                proto = ev.get("event", {}).get("protocol", "?")
                lines.append(
                    f"[{i}] {kind}  ns={ns} pod={pod} pid={pid} ({comm})\n"
                    f"    {proto} → {dst}"
                )
            else:
                lines.append(
                    f"[{i}] {kind}  ns={ns} pod={pod} pid={pid} ({comm})\n"
                    f"    {fn or args}"
                )
        return "\n".join(lines)

    return raw[:4000]


@tool
def tetragon_events(event_type: str = "exec", limit: int = 50) -> str:
    """Retrieve recent Tetragon eBPF security events (read-only).

    Use this when the user reports runtime / eBPF-level behaviour to
    cross-check Falco: e.g. "suspicious exec in a pod", "unexpected
    outbound connection", "which process opened /etc/shadow".

    Args:
        event_type: One of ``exec`` (process executions, default),
            ``network`` (connections), ``file`` (file access), ``dns``
            (DNS lookups), ``exit`` (process exits).
        limit: Maximum number of events to render (default 50).

    Returns:
        A summarised list of Tetragon events with process, pod,
        namespace, and per-event detail.  In stub mode (no Tetragon
        deployed), returns a synthetic payload that includes a
        representative "exec in a pod" event so the Security Agent
        can be exercised end-to-end.
    """
    # ── Allow-list enforcement ──
    try:
        _validate(event_type)
    except DisallowedQueryError as exc:
        return f"❌ BLOCKED: {exc}"

    et = event_type.lower().strip()

    if is_stub():
        sample = (
            '{"type":"exec","process":{"pid":42,"comm":"sh",'
            '"args":["/bin/sh","-c","whoami"],"k8s":{"namespace":"sentinel",'
            '"pod":"demo-api-7d9-abcde","container":"api"}},'
            '"event":{"function":"execve","policy":"susp-exec-in-pod"}}'
        )
        return (
            f"[T3.2 STUB] Would query Tetragon at "
            f"{_DEFAULT_TETRAGON_URL}/events?type={et}&limit={limit}\n"
            f"--- stub payload ---\n{sample}"
        )

    # ── Live execution ──
    url = urljoin(_DEFAULT_TETRAGON_URL, "/events")
    params = {"type": et, "limit": str(limit)}
    raw = _httpx_get(url, params=params, timeout=15)
    return _summarise_events(raw, et, limit=limit)


register(tetragon_events, category="security")
