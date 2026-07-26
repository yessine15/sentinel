"""kubectl get — read-only resource listing (live in T2.3).

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


def _build_cmd(
    resource: str,
    namespace: str = "",
    all_namespaces: bool = False,
    output: str = "wide",
    field_selector: str = "",
    label_selector: str = "",
    sort_by: str = "",
) -> list[str]:
    """Build a sanitized ``kubectl get`` command line."""
    cmd = ["kubectl", "get", resource]
    if all_namespaces:
        cmd.append("--all-namespaces")
    elif namespace:
        cmd.extend(["-n", namespace])
    if output:
        cmd.extend(["-o", output])
    if field_selector:
        cmd.extend(["--field-selector", field_selector])
    if label_selector:
        cmd.extend(["-l", label_selector])
    if sort_by:
        cmd.extend(["--sort-by", sort_by])
    return cmd


@tool
def kubectl_get(
    resource: str,
    namespace: str = "",
    all_namespaces: bool = False,
) -> str:
    """Run ``kubectl get <resource>`` and return the live listing.

    Use this to inspect the current state of Kubernetes resources:
    pods, deployments, services, nodes, namespaces, events, configmaps,
    ingresses, PVCs, replicasets, statefulsets, daemonsets, jobs,
    cronjobs, endpoints, serviceaccounts, roles, rolebindings,
    clusterroles, clusterrolebindings.

    Args:
        resource: K8s resource type (e.g. "pods", "deployments", "nodes").
        namespace: Limit to this namespace (omit for default or use
            ``all_namespaces=True``).
        all_namespaces: If True, list across all namespaces.

    Returns:
        Live ``kubectl get`` output (T2.3) or a stub preview
        (``RUN_MODE=stub``).
    """
    # ── Allow-list enforcement ──
    try:
        _validate("get", resource)
    except (DisallowedVerbError, DisallowedResourceError) as exc:
        return f"❌ BLOCKED: {exc}"

    cmd = _build_cmd(
        resource,
        namespace=namespace,
        all_namespaces=all_namespaces,
    )

    return run_kubectl(cmd)


# Auto-register so the registry finds this tool.
register(kubectl_get, category="kubernetes")
