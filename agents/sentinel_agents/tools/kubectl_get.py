"""kubectl get — read-only resource listing.

Strictly allow-listed: only ``get`` verb, only safe resource types.
Any attempt to ``delete``, ``exec``, ``apply``, etc. is blocked
*before* a subprocess is spawned.

Real execution (T2.3) will use ``subprocess.run`` against the live
cluster.  For T2.2 the tool validates args and returns the *command
that would be run*.
"""

from __future__ import annotations

from langchain_core.tools import tool

from sentinel_agents.tools.base import DisallowedVerbError, DisallowedResourceError, register
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
    """Run ``kubectl get <resource>`` and return the listing.

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
        The command that would be executed (T2.2 stub) or the actual
        command output (T2.3+).
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

    # T2.2 stub — return what *would* be run.
    # T2.3 will replace this with subprocess.run(cmd, capture_output=True, text=True).
    return (
        f"[T2.2 STUB] Would run: {' '.join(cmd)}\n"
        f"(Live execution will be wired in T2.3 — real kubectl output will appear here.)"
    )


# Auto-register so the registry finds this tool.
register(kubectl_get, category="kubernetes")
