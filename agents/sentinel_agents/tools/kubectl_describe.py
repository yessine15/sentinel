"""kubectl describe — read-only resource inspection.

Strictly allow-listed: only ``describe`` verb, only safe resource types.
Any attempt to describe a disallowed resource is blocked before execution.
"""

from __future__ import annotations

from langchain_core.tools import tool

from sentinel_agents.tools.base import DisallowedVerbError, DisallowedResourceError, register
from sentinel_agents.tools.base import validate_kubectl as _validate


@tool
def kubectl_describe(
    resource: str,
    name: str,
    namespace: str = "",
) -> str:
    """Run ``kubectl describe <resource> <name>`` and return the details.

    Use this to get detailed information about a specific Kubernetes
    resource: pod events, deployment status, node conditions, service
    endpoints, etc.

    Args:
        resource: K8s resource type (e.g. "pod", "deployment", "node").
        name: Exact name of the resource to describe.
        namespace: Namespace of the resource (omit for cluster-scoped
            resources like nodes).

    Returns:
        The command that would be executed (T2.2 stub) or the actual
        command output (T2.3+).
    """
    # ── Allow-list enforcement ──
    try:
        _validate("describe", resource)
    except (DisallowedVerbError, DisallowedResourceError) as exc:
        return f"❌ BLOCKED: {exc}"

    cmd = ["kubectl", "describe", resource, name]
    if namespace:
        cmd.extend(["-n", namespace])

    # T2.2 stub — return what *would* be run.
    return (
        f"[T2.2 STUB] Would run: {' '.join(cmd)}\n"
        f"(Live execution will be wired in T2.3 — real kubectl output will appear here.)"
    )


register(kubectl_describe, category="kubernetes")
