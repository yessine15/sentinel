"""Tetragon events — read recent eBPF security events (T3.2, live-wired T4.2).

Tetragon is a Cilium-provided eBPF security observability tool.  Unlike
Falco (rule-based alerts), Tetragon exposes raw kernel events
(process exec, network connections, file access) and can enforce
in-kernel policies.  We only **observe** here — never enforce.

Two live sources (T4.2):

1. The optional JSON HTTP bridge (``TETRAGON_URL``, e.g. a deployment
   exposing the export stream at ``/events``) — used when present.
2. The cluster-wide event stream: ``kubectl logs ds/tetragon -c
   export-stdout`` — the hubble-export-stdout sidecar tails the
   agent's NDJSON export log to stdout.  This is the default T4.2
   wiring (no extra components) and is race-free: the DaemonSet log
   aggregates events from ALL nodes.

Tetragon's JSON export format is *nested* — one NDJSON object per line
like ``{"process_exec": {"process": {...}, ...}}`` — while the older
flat format (``{"type": "exec", "process": {...}}``) is also accepted.

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
    run_subprocess,
)
from sentinel_agents.tools.base import validate_tetragon_events as _validate

_DEFAULT_TETRAGON_URL = os.environ.get(
    "TETRAGON_URL",
    "http://tetragon.kube-system.svc:8081",
)

# Cap the rendered event count to keep tool output bounded.
_MAX_EVENTS = 50

# How many recent lines to pull from the DaemonSet log stream.
_KUBECTL_TAIL = 500

# Shell binaries the Security Agent cares about (suspicious exec).
_SHELL_BINARIES = (
    "/bin/bash", "/usr/bin/bash", "/bin/sh", "/usr/bin/sh",
    "/bin/dash", "/bin/ash",
)

# Map the tool's event_type → the Tetragon export event keys.
_EXPORT_KEYS = {
    "exec": ("process_exec", "process_kprobe"),
    "exit": ("process_exit",),
    "network": ("process_network", "process_dns"),
    "file": ("process_file", "process_lsm"),
    "dns": ("process_dns", "process_network"),
}


def _unwrap_event(ev: dict) -> dict:
    """Normalise a Tetragon export event into ``{type, process, event}``.

    The export stream uses the NESTED format::

        {"process_exec": {"process": {...}, "parent": {...}}}

    while the older flat format is::

        {"type": "exec", "process": {...}, "event": {...}}

    Returns a flat dict with ``type`` / ``process`` / ``event`` keys
    (empty dict when the event is not recognised).
    """
    for key in (
        "process_exec", "process_exit", "process_kprobe",
        "process_tracepoint", "process_lsm", "process_network",
        "process_dns", "process_file", "process_loader",
    ):
        if key in ev and isinstance(ev[key], dict):
            inner = dict(ev[key])
            return {
                "type": key,
                "process": inner.pop("process", {}),
                "event": inner,
            }
    if "type" in ev:
        return {"type": ev["type"], "process": ev.get("process", {}), "event": ev.get("event", {})}
    return {}


def _summarise_events(raw: str, event_type: str, limit: int = _MAX_EVENTS) -> str:
    """Render the Tetragon event stream as an agent-friendly summary.

    Accepts both the nested export format (NDJSON / JSON array) and the
    older flat format.  For ``exec`` queries the summary highlights
    shell binaries first (suspicious exec signal).
    """
    if not raw or not raw.strip():
        return (
            f"No Tetragon {event_type} events returned.  "
            "The Tetragon gRPC/HTTP bridge may not be exposed."
        )

    stripped = raw.strip()
    # Strip kubectl --prefix noise ("[pod/x/export-stdout] ...") BEFORE
    # format detection — a bracketed prefix would otherwise be mistaken
    # for a JSON array.
    if stripped.startswith("[pod/") and "] " in stripped:
        stripped = stripped.split("] ", 1)[1].strip()

    events: list[dict] = []
    if stripped.startswith("["):
        import json as _json

        try:
            events = _json.loads(stripped)
        except _json.JSONDecodeError:
            events = []
    else:
        import json as _json

        for ln in stripped.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            # Strip per-line kubectl --prefix noise ("[pod/x/export-stdout] ").
            if ln.startswith("[pod/") and "] " in ln:
                ln = ln.split("] ", 1)[1]
            try:
                events.append(_json.loads(ln))
            except _json.JSONDecodeError:
                continue

    if not isinstance(events, list):
        return raw[:4000]

    # Normalise + filter by event type.
    normalised: list[dict] = []
    for ev in events:
        flat = _unwrap_event(ev)
        if not flat:
            continue
        kind = str(flat.get("type", ""))
        keys = _EXPORT_KEYS.get(event_type.lower().strip(), ())
        if not keys or any(k in kind for k in keys):
            normalised.append(flat)

    if not normalised:
        return (
            f"No Tetragon {event_type} events in the recent stream "
            f"({len(events)} event(s) total, none matching)."
        )

    # Exec queries: surface shell binaries first (suspicious signal).
    if event_type.lower().strip() == "exec":
        def _is_shell(ev: dict) -> bool:
            binary = str(ev.get("process", {}).get("binary", ""))
            return any(binary == b or binary.endswith("/" + b.lstrip("/"))
                       for b in _SHELL_BINARIES)

        normalised.sort(key=lambda ev: (0 if _is_shell(ev) else 1, 0))

    lines = [
        f"Tetragon returned {len(normalised)} {event_type} event(s). "
        f"Showing up to {limit}:"
    ]
    for i, ev in enumerate(normalised[:limit], start=1):
        kind = str(ev.get("type", "?"))
        proc = ev.get("process", {}) or {}
        pid = proc.get("pid", "?")
        comm = proc.get("binary", proc.get("comm", "?"))
        pod = (proc.get("pod") or {}).get("name", "?")
        ns = (proc.get("pod") or {}).get("namespace", "?")
        args = " ".join(proc.get("arguments", "").split()[:6])
        event = ev.get("event", {}) or {}

        if "process_exec" in kind:
            lines.append(
                f"[{i}] exec  ns={ns} pod={pod} pid={pid} binary={comm}\n"
                f"    args: {args}"
            )
        elif "process_exit" in kind:
            code = event.get("status", "?")
            lines.append(
                f"[{i}] exit  ns={ns} pod={pod} pid={pid} binary={comm} "
                f"code={code}"
            )
        elif "process_kprobe" in kind:
            fn = event.get("function_name", "?")
            lines.append(
                f"[{i}] kprobe({fn})  ns={ns} pod={pod} pid={pid} "
                f"binary={comm}"
            )
        elif "process_tracepoint" in kind:
            fn = event.get("function_name", event.get("event", "?"))
            lines.append(
                f"[{i}] tracepoint({fn})  ns={ns} pod={pod} pid={pid} "
                f"binary={comm}"
            )
        elif "process_network" in kind or "process_dns" in kind:
            dst = event.get("destination", {})
            proto = event.get("protocol", "?")
            lines.append(
                f"[{i}] {kind}  ns={ns} pod={pod} pid={pid} binary={comm}\n"
                f"    {proto} → {dst}"
            )
        else:
            lines.append(
                f"[{i}] {kind}  ns={ns} pod={pod} pid={pid} binary={comm}\n"
                f"    {args}"
            )
    return "\n".join(lines)


def _kubectl_events(event_type: str, limit: int) -> str:
    """Read the recent cluster-wide Tetragon event stream via kubectl.

    The hubble-export-stdout sidecar in each tetragon agent pod tails
    the node's NDJSON export log to stdout, so the per-pod container
    logs together form the cluster-wide event stream.

    NOTE: ``kubectl logs ds/tetragon`` aggregation is unreliable
    (``--tail``/``--since`` only return one pod's lines on kind +
    containerd), so we enumerate the agent pods and read each one's
    log individually — deterministic and race-free: the exec event is
    visible regardless of which node it happened on.
    """
    pods_out = run_subprocess(
        [
            "kubectl", "get", "pods", "-n", "kube-system",
            "-l", "app.kubernetes.io/name=tetragon", "-o", "name",
        ],
        timeout=15,
    )
    names = [ln.strip().split("/")[-1] for ln in pods_out.splitlines() if "/" in ln]
    if not names:
        return "No Tetragon agent pods found — is Tetragon deployed?"

    chunks: list[str] = []
    for name in names:
        raw = run_subprocess(
            [
                "kubectl", "logs", name, "-c", "export-stdout",
                "-n", "kube-system", "--tail=200",
            ],
            timeout=30,
        )
        if raw.startswith("❌") or raw.startswith("command exited"):
            continue
        chunks.append(f"[pod/{name}/export-stdout] {raw}")

    if not chunks:
        return "Tetragon pods found but no export-stdout logs were readable."
    return _summarise_events("\n".join(chunks), event_type, limit=limit)


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

    # ── Live execution: HTTP bridge first, kubectl stream fallback ──
    try:
        url = urljoin(_DEFAULT_TETRAGON_URL, "/events")
        params = {"type": et, "limit": str(limit)}
        raw = _httpx_get(url, params=params, timeout=10)
        # _httpx_get never raises — on failure it returns a small
        # {"error": ...} JSON object.  Detect that and fall back to
        # the cluster-wide kubectl stream instead of "summarising" the
        # error as if it were a Tetragon event.
        if not raw.lstrip().startswith('{"error"'):
            return _summarise_events(raw, et, limit=limit)
    except Exception:
        pass
    return _kubectl_events(et, limit)


register(tetragon_events, category="security")
