"""Shared primitives for allow-listed SRE tools.

Every tool that can execute a cluster operation must validate its
arguments against a strict allow-list before running anything.

T2.3: Live execution mode — tools use ``subprocess.run`` for kubectl
and ``httpx`` for Prometheus/Loki HTTP APIs.  Set ``RUN_MODE=stub``
to revert to command-preview mode for unit tests.
"""

from __future__ import annotations

import os
import subprocess  # noqa: S404 — only used with allow-listed args via shlex.join

from dataclasses import dataclass, field
from typing import Any, Callable


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
