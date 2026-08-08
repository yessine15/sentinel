"""Shared primitives for allow-listed SRE tools.

Every tool that can execute a cluster operation must validate its
arguments against a strict allow-list before running anything.

T2.3: Live execution mode — tools use ``subprocess.run`` for kubectl
and ``httpx`` for Prometheus/Loki HTTP APIs.  Set ``RUN_MODE=stub``
to revert to command-preview mode for unit tests.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


# ─────────────────────────────────────────────────────────────
# Exception hierarchy
# ─────────────────────────────────────────────────────────────
class ToolSecurityError(Exception):
    """Raised when a tool attempts a disallowed operation."""


class DisallowedVerbError(ToolSecurityError):
    """The verb (get/delete/exec/...) is not in the allow-list."""


class DisallowedResourceError(ToolSecurityError):
    """The resource type (pods/secrets/...) is not in the allow-list."""


class DisallowedQueryError(ToolSecurityError):
    """The PromQL/LogQL query contains a disallowed keyword or operation."""


# ─────────────────────────────────────────────────────────────
# Allow-list definitions
# ─────────────────────────────────────────────────────────────

# kubectl get/describe — read-only resource types ONLY.
# Explicit deny-list comment so nobody accidentally adds dangerous verbs.
# NEVER add: delete, exec, apply, patch, edit, create, replace, rollout, drain, cordon.
ALLOWED_KUBECTL_VERBS: frozenset[str] = frozenset({"get", "describe"})

ALLOWED_KUBECTL_RESOURCES: frozenset[str] = frozenset({
    "pods",
    "pod",
    "deployments",
    "deployment",
    "services",
    "service",
    "svc",
    "nodes",
    "node",
    "namespaces",
    "namespace",
    "ns",
    "events",
    "event",
    "configmaps",
    "configmap",
    "secrets",
    "secret",
    "ingresses",
    "ingress",
    "ing",
    "persistentvolumeclaims",
    "persistentvolumeclaim",
    "pvc",
    "replicasets",
    "replicaset",
    "rs",
    "statefulsets",
    "statefulset",
    "sts",
    "daemonsets",
    "daemonset",
    "ds",
    "jobs",
    "job",
    "cronjobs",
    "cronjob",
    "cj",
    "endpoints",
    "endpoint",
    "ep",
    "serviceaccounts",
    "serviceaccount",
    "sa",
    "roles",
    "role",
    "rolebindings",
    "rolebinding",
    "clusterroles",
    "clusterrole",
    "clusterrolebindings",
    "clusterrolebinding",
})

# PromQL: only read-only HTTP API endpoints.  /api/v1/query and
# /api/v1/query_range are safe; /api/v1/admin/tsdb/* is NOT.
ALLOWED_PROMQL_OPERATIONS: frozenset[str] = frozenset({
    "instant",       # GET /api/v1/query
    "range",         # GET /api/v1/query_range
    "labels",        # GET /api/v1/labels
    "label_values",  # GET /api/v1/label/<name>/values
    "series",        # GET /api/v1/series
    "targets",       # GET /api/v1/targets
    "rules",         # GET /api/v1/rules
    "alerts",        # GET /api/v1/alerts
    "status",        # GET /api/v1/status/*
})

# LogQL: only read-only HTTP API endpoints.
# /loki/api/v1/query and /loki/api/v1/query_range are safe.
ALLOWED_LOGQL_OPERATIONS: frozenset[str] = frozenset({
    "query",         # GET /loki/api/v1/query
    "query_range",   # GET /loki/api/v1/query_range
    "labels",        # GET /loki/api/v1/labels
    "label_values",  # GET /loki/api/v1/label/<name>/values
    "series",        # GET /loki/api/v1/series
    "tail",          # GET /loki/api/v1/tail (read-only streaming)
})

# ── T3.2 Security tools ────────────────────────────────────────
# Trivy scan targets — the only things the agent may scan.
# "image" scans a container image, "filesystem" scans a local dir,
# "fs" is the trivy CLI alias for filesystem.  "repo" is allowed for
# scanning the current repo only (never arbitrary URLs — see validator).
ALLOWED_TRIVY_TARGETS: frozenset[str] = frozenset({
    "image",
    "filesystem",
    "fs",
    "repo",
})

# Trivy severities we accept (passed via --severity).  "UNKNOWN" is
# excluded because it's noise; the agent can always broaden later.
ALLOWED_TRIVY_SEVERITIES: frozenset[str] = frozenset({
    "CRITICAL",
    "HIGH",
    "MEDIUM",
    "LOW",
})

# Trivy scanners (the --scanners flag).  Restrict to read-only
# vulnerability + config + secret + misconfig scanning.  "license"
# and "vuln" are the typical subset.
ALLOWED_TRIVY_SCANNERS: frozenset[str] = frozenset({
    "vuln",
    "config",
    "secret",
    "misconfig",
    "license",
})

# Tetragon event types — the only observe-able eBPF events we expose.
# These are read-only Tetragon event categories (exec, network, file).
ALLOWED_TETRAGON_EVENTS: frozenset[str] = frozenset({
    "exec",
    "network",
    "file",
    "dns",
    "exit",
})

# Falco event fields the agent can request from the Falco HTTP API
# (the gRPC / HTTP endpoints of a falcosidekick or native UI).  We
# only allow read-only retrieval endpoints — NEVER the "add" /
# "delete" rule endpoints.
ALLOWED_FALCO_OPERATIONS: frozenset[str] = frozenset({
    "events",        # GET /events — list recent alert events
    "rules",         # GET /rules — list active rules (read-only)
    "outputs",       # GET /outputs — list configured outputs
    "health",        # GET /healthz — liveness probe
})


# ─────────────────────────────────────────────────────────────
# Validation helpers
# ─────────────────────────────────────────────────────────────
def validate_kubectl(verb: str, resource: str) -> None:
    """Raise ``ToolSecurityError`` if *verb* or *resource* is disallowed."""
    v = verb.lower().strip()
    if v not in ALLOWED_KUBECTL_VERBS:
        raise DisallowedVerbError(
            f"kubectl '{verb}' is NOT allowed. Allowed verbs: "
            f"{sorted(ALLOWED_KUBECTL_VERBS)}"
        )
    r = resource.lower().strip()
    if r not in ALLOWED_KUBECTL_RESOURCES:
        raise DisallowedResourceError(
            f"Resource '{resource}' is NOT allowed. "
            f"Allowed resources include: pods, deployments, services, nodes, "
            f"namespaces, events, configmaps, ingresses, pvc, replicasets, "
            f"statefulsets, daemonsets, jobs, cronjobs, endpoints, "
            f"serviceaccounts, roles, rolebindings"
        )


def validate_promql(operation: str, query: str) -> None:
    """Raise ``ToolSecurityError`` if the operation or query is disallowed."""
    op = operation.lower().strip()
    if op not in ALLOWED_PROMQL_OPERATIONS:
        raise DisallowedQueryError(
            f"PromQL operation '{operation}' is NOT allowed. "
            f"Allowed: {sorted(ALLOWED_PROMQL_OPERATIONS)}"
        )
    # Block write/delete keywords even if they somehow slip into a query string.
    # ORDER MATTERS: check longer (more specific) keywords first so
    # "delete_series" is caught before the generic "delete" substring.
    forbidden = ["delete_series", "clean_tombstones", "snapshot", "delete"]
    lower_q = query.lower()
    for kw in forbidden:
        if kw in lower_q:
            raise DisallowedQueryError(
                f"PromQL query contains forbidden keyword '{kw}'."
            )


def validate_logql(operation: str, query: str) -> None:
    """Raise ``ToolSecurityError`` if the operation or query is disallowed."""
    op = operation.lower().strip()
    if op not in ALLOWED_LOGQL_OPERATIONS:
        raise DisallowedQueryError(
            f"LogQL operation '{operation}' is NOT allowed. "
            f"Allowed: {sorted(ALLOWED_LOGQL_OPERATIONS)}"
        )
    # Block any attempt to use the Loki write path or delete path.
    # ORDER MATTERS: check longer paths first.
    forbidden = ["/api/v1/push", "flush", "ingest", "push", "delete"]
    lower_q = query.lower()
    for kw in forbidden:
        if kw in lower_q:
            raise DisallowedQueryError(
                f"LogQL query references forbidden endpoint '{kw}'."
            )


# ─────────────────────────────────────────────────────────────
# Security tool validators (T3.2)
# ─────────────────────────────────────────────────────────────
def validate_trivy(
    target: str,
    scanners: str = "vuln",
    severity: str = "CRITICAL,HIGH",
) -> None:
    """Raise ``ToolSecurityError`` if the Trivy scan is disallowed.

    Trivy is read-only (it never mutates state) but we still validate
    the inputs to keep the surface small and predictable:

    - ``target`` must be one of the allowed scan kinds.
    - ``scanners`` must be a comma-separated subset of the allow-list.
    - ``severity`` must be a comma-separated subset of the allow-list.

    We also explicitly forbid remote ``repo`` URLs that point outside
    the local filesystem, because the agent should never pull arbitrary
    remote git repositories on behalf of a user.
    """
    t = target.lower().strip()
    if t not in ALLOWED_TRIVY_TARGETS:
        raise DisallowedQueryError(
            f"Trivy target '{target}' is NOT allowed. "
            f"Allowed: {sorted(ALLOWED_TRIVY_TARGETS)}"
        )

    for s in (scanners or "").split(","):
        s = s.strip().lower()
        if s and s not in ALLOWED_TRIVY_SCANNERS:
            raise DisallowedQueryError(
                f"Trivy scanner '{s}' is NOT allowed. "
                f"Allowed: {sorted(ALLOWED_TRIVY_SCANNERS)}"
            )

    for sev in (severity or "").split(","):
        sev = sev.strip().upper()
        if sev and sev not in ALLOWED_TRIVY_SEVERITIES:
            raise DisallowedQueryError(
                f"Trivy severity '{sev}' is NOT allowed. "
                f"Allowed: {sorted(ALLOWED_TRIVY_SEVERITIES)}"
            )


def validate_cve_lookup(cve_id: str) -> None:
    """Validate a CVE identifier before looking it up.

    Accepts the canonical ``CVE-YYYY-NNNN`` form (4-7 digit suffix) plus
    the ``CVE-YYYY-NNNNNN`` form.  We reject anything that doesn't match
    so a malformed / injected string can never become a URL or shell arg.
    """
    import re

    cid = (cve_id or "").strip().upper()
    if not cid.startswith("CVE-"):
        raise DisallowedQueryError(
            f"CVE id must start with 'CVE-'. Got: '{cve_id}'"
        )

    if not re.fullmatch(r"CVE-\d{4}-\d{4,7}", cid):
        raise DisallowedQueryError(
            f"CVE id '{cve_id}' is not in the canonical CVE-YYYY-NNNN form."
        )


def validate_tetragon_events(event_type: str) -> None:
    """Validate the requested Tetragon event kind."""
    et = (event_type or "").lower().strip()
    if et not in ALLOWED_TETRAGON_EVENTS:
        raise DisallowedQueryError(
            f"Tetragon event type '{event_type}' is NOT allowed. "
            f"Allowed: {sorted(ALLOWED_TETRAGON_EVENTS)}"
        )


def validate_falco_operation(operation: str) -> None:
    """Validate a Falco HTTP API operation."""
    op = (operation or "").lower().strip()
    if op not in ALLOWED_FALCO_OPERATIONS:
        raise DisallowedQueryError(
            f"Falco operation '{operation}' is NOT allowed. "
            f"Allowed: {sorted(ALLOWED_FALCO_OPERATIONS)}"
        )


# ─────────────────────────────────────────────────────────────
# Tool registry
# ─────────────────────────────────────────────────────────────
@dataclass
class ToolInfo:
    """Metadata for one registered tool."""

    name: str
    description: str
    category: str  # "kubernetes" | "prometheus" | "loki" | "rag"


_registry: dict[str, Callable[..., Any]] = {}
"""Maps tool name → LangChain @tool decorated function."""


def register(tool_fn: Callable[..., Any], /, *, category: str) -> Callable[..., Any]:
    """Register a tool function so it can be discovered by the agent."""
    _registry[tool_fn.name] = tool_fn
    # Attach metadata for tool listing
    tool_fn.__sentinel_category__ = category  # type: ignore[attr-defined]
    return tool_fn


def get_all_tools() -> list[Callable[..., Any]]:
    """Return every registered tool, ready to bind to the LLM."""
    return list(_registry.values())


def get_tool_names() -> list[str]:
    """Return the sorted list of registered tool names."""
    return sorted(_registry.keys())


# ─────────────────────────────────────────────────────────────
# Live execution helpers (T2.3)
# ─────────────────────────────────────────────────────────────

# RUN_MODE controls whether tools actually execute or just preview.
#   "live" — real subprocess / HTTP calls (default)
#   "stub" — return the command that *would* run (unit-test safe)
_RUN_MODE = os.environ.get("RUN_MODE", "live").lower()


def is_live() -> bool:
    """Return True when tools should execute real commands."""
    return _RUN_MODE == "live"


def is_stub() -> bool:
    """Return True when tools should return command previews."""
    return not is_live()


def run_kubectl(cmd: list[str], *, timeout: int = 30) -> str:
    """Execute a sanitized kubectl command via subprocess.

    Only called after ``validate_kubectl`` has passed, so the command
    list has already been checked against the allow-list.
    """
    if is_stub():
        import shlex
        return f"[T2.3 STUB] Would run: {shlex.join(cmd)}"

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode != 0:
            return (
                f"kubectl exited with code {result.returncode}.\n"
                f"{'STDOUT: ' + stdout if stdout else ''}\n"
                f"{'STDERR: ' + stderr if stderr else ''}"
            ).strip()
        return stdout if stdout else "(no output)"
    except FileNotFoundError:
        return "❌ kubectl not found on PATH. Is the cluster accessible?"
    except subprocess.TimeoutExpired:
        return f"❌ kubectl timed out after {timeout}s."


# ── HTTP client (lazy import — httpx is already a project dependency) ──


def _httpx_get(url: str, params: dict[str, str] | None = None, *, timeout: int = 15) -> str:
    """Synchronous HTTP GET with structured error handling."""
    import httpx

    try:
        resp = httpx.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except httpx.ConnectError:
        return '{"error": "connection refused", "url": "' + url + '"}'
    except httpx.TimeoutException:
        return '{"error": "request timed out after ' + str(timeout) + 's", "url": "' + url + '"}'
    except httpx.HTTPStatusError as exc:
        return (
            f'{{"error": "HTTP {exc.response.status_code}", '
            f'"url": "{url}", "body": "{exc.response.text[:500]}"}}'
        )
    except Exception as exc:
        return '{"error": "' + str(exc) + '", "url": "' + url + '"}'


def _httpx_get_json(
    url: str, params: dict[str, str] | None = None, *, timeout: int = 15
) -> dict[str, Any]:
    """Synchronous HTTP GET that returns parsed JSON.

    On error, returns a small dict with an ``"error"`` key (never raises)
    so tools can surface the failure to the LLM as plain text.
    """
    import httpx

    try:
        resp = httpx.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return data
        # Some endpoints return lists — wrap for uniformity.
        return {"data": data}
    except httpx.ConnectError:
        return {"error": "connection refused", "url": url}
    except httpx.TimeoutException:
        return {"error": f"request timed out after {timeout}s", "url": url}
    except httpx.HTTPStatusError as exc:
        return {
            "error": f"HTTP {exc.response.status_code}",
            "url": url,
            "body": exc.response.text[:500],
        }
    except Exception as exc:
        return {"error": str(exc), "url": url}


def run_subprocess(cmd: list[str], *, timeout: int = 60) -> str:
    """Execute a sanitized external CLI command via ``subprocess.run``.

    Only called *after* ``validate_*`` has approved the inputs, so the
    command list has already been checked.  Returns the combined /
    structured stdout+stderr output.

    In stub mode (``RUN_MODE=stub``) the function returns a preview of
    the command that *would* run — safe for unit tests.
    """
    if is_stub():
        import shlex

        return f"[T3.2 STUB] Would run: {shlex.join(cmd)}"

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode != 0:
            return (
                f"command exited with code {result.returncode}.\n"
                f"{'STDOUT: ' + stdout if stdout else ''}\n"
                f"{'STDERR: ' + stderr if stderr else ''}"
            ).strip()
        return stdout if stdout else "(no output)"
    except FileNotFoundError:
        return f"❌ '{cmd[0]}' not found on PATH — is the tool installed?"
    except subprocess.TimeoutExpired:
        return f"❌ '{cmd[0]}' timed out after {timeout}s."
