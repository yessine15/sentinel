"""kubectl describe — read-only resource inspection (live in T2.3).

Allow-list enforced *before* subprocess execution.
Set ``RUN_MODE=stub`` to revert to command-preview mode.
"""

from __future__ import annotations

from langchain_core.tools import tool

from sentinel_agents.tools.base import (
    DisallowedResourceError,
    DisallowedVerbError,
    register,
    run_kubectl,
)
from sentinel_agents.tools.base import validate_kubectl as _validate


@tool
def kubectl_describe(
    resource: str,
    name: str,
    namespace: str = "",
) -> str:
    """Run ``kubectl describe <resource> <name>`` and return live details.

    Use this to get detailed information about a specific Kubernetes
    resource: pod events, deployment status, node conditions, service
    endpoints, etc.

    Args:
        resource: K8s resource type (e.g. "pod", "deployment", "node").
        name: Exact name of the resource to describe.
        namespace: Namespace of the resource (omit for cluster-scoped
            resources like nodes).

    Returns:
        Live ``kubectl describe`` output (T2.3) or a stub preview
        (``RUN_MODE=stub``).
    """
    # ── Allow-list enforcement ──
    try:
        _validate("describe", resource)
    except (DisallowedVerbError, DisallowedResourceError) as exc:
        return f"❌ BLOCKED: {exc}"

    cmd = ["kubectl", "describe", resource, name]
    if namespace:
        cmd.extend(["-n", namespace])

    return run_kubectl(cmd)


register(kubectl_describe, category="kubernetes")
