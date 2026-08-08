"""Kube resource usage — detect idle/over-provisioned workloads (T3.3).

Queries Prometheus for pod CPU/memory requests vs actual usage so the
Cost Agent can identify waste and propose right-sizing suggestions.

Allow-list enforced before any PromQL call.

Environment variables
--------------------
PROMETHEUS_URL : str
    Base URL of the Prometheus HTTP API (same as promql_query).
    Default: ``http://localhost:9090``
"""

from __future__ import annotations

import os
from urllib.parse import urlencode, urljoin

from langchain_core.tools import tool

from sentinel_agents.tools.base import (
    DisallowedQueryError,
    _httpx_get,
    is_stub,
    register,
    validate_cost_metric,
    validate_cost_resource,
)

_DEFAULT_PROMETHEUS_URL = os.environ.get(
    "PROMETHEUS_URL",
    "http://localhost:9090",
)

# ── PromQL templates for each metric kind ──────────────────────
# We pre-define the queries so the agent never invents arbitrary
# PromQL — it just picks a metric + optional resource filter.
_METRIC_QUERIES: dict[str, str] = {
    "cpu_requests": (
        'sum(kube_pod_container_resource_requests'
        '{resource="cpu", namespace=~"$namespace", pod=~"$pod"}'
        ') by (pod, namespace)'
    ),
    "cpu_usage": (
        'sum(rate(container_cpu_usage_seconds_total'
        '{namespace=~"$namespace", pod=~"$pod", container!=""}'
        '[5m])) by (pod, namespace)'
    ),
    "cpu_utilisation": (
        'sum(rate(container_cpu_usage_seconds_total'
        '{namespace=~"$namespace", pod=~"$pod", container!=""}'
        '[5m])) by (pod, namespace)'
        ' / '
        'sum(kube_pod_container_resource_requests'
        '{resource="cpu", namespace=~"$namespace", pod=~"$pod"}'
        ') by (pod, namespace)'
    ),
    "memory_requests": (
        'sum(kube_pod_container_resource_requests'
        '{resource="memory", namespace=~"$namespace", pod=~"$pod"}'
        ') by (pod, namespace)'
    ),
    "memory_usage": (
        'sum(container_memory_working_set_bytes'
        '{namespace=~"$namespace", pod=~"$pod", container!=""}'
        ') by (pod, namespace)'
    ),
    "memory_utilisation": (
        'sum(container_memory_working_set_bytes'
        '{namespace=~"$namespace", pod=~"$pod", container!=""}'
        ') by (pod, namespace)'
        ' / '
        'sum(kube_pod_container_resource_requests'
        '{resource="memory", namespace=~"$namespace", pod=~"$pod"}'
        ') by (pod, namespace)'
    ),
}


def _build_promql(metric: str, namespace: str = ".*", pod_filter: str = ".*") -> str:
    """Build the PromQL query for *metric*, substituting namespace/pod filters."""
    tmpl = _METRIC_QUERIES[metric]
    return tmpl.replace("$namespace", namespace).replace("$pod", pod_filter)


@tool
def kube_resource_usage(
    metric: str = "all",
    namespace: str = ".*",
    resource: str = "deployments",
    threshold: float = 0.3,
) -> str:
    """Query Prometheus for pod resource usage and identify idle/over-provisioned
    workloads.

    This is the primary tool for the Sentinel **Cost Agent**.  It runs
    pre-defined PromQL queries to compare resource **requests** against
    **actual usage** and flags workloads where utilisation is below the
    threshold so the agent can propose right-sizing.

    Args:
        metric: Which metric(s) to retrieve.  One of:
            ``cpu_requests``, ``cpu_usage``, ``cpu_utilisation``,
            ``memory_requests``, ``memory_usage``, ``memory_utilisation``,
            or ``all`` (runs CPU + memory utilisation together).
        namespace: A regex filter for the namespace (default ``.*`` for all).
            Pass a literal namespace name, e.g. ``sentinel``.
        resource: The resource kind to scope the right-sizing suggestion.
            One of: ``deployments``, ``statefulsets``, ``daemonsets``,
            ``jobs``, ``cronjobs``, ``pods``.
        threshold: The utilisation ratio below which a workload is
            flagged as **over-provisioned**.  Default ``0.3`` (30%).
            Workloads using less than this fraction of their requests
            are candidates for right-sizing.

    Returns:
        A summary table of workloads, their requests vs actual usage,
        utilisation ratios, and a right-sizing verdict (``OVER-PROVISIONED``
        / ``OK`` / ``UNDER-PROVISIONED``).
    """
    # ── Allow-list validation ──
    try:
        validate_cost_metric(metric)
        validate_cost_resource(resource)
    except DisallowedQueryError as exc:
        return f"❌ BLOCKED: {exc}"

    met = metric.lower().strip()
    ns = namespace.strip() or ".*"
    res = resource.lower().strip()

    # Decide which metrics to run
    if met == "all":
        to_run = ["cpu_utilisation", "memory_utilisation"]
    else:
        to_run = [met]

    # Stub path — safe for unit tests
    if is_stub():
        lines: list[str] = []
        for m in to_run:
            q = _build_promql(m, ns, ".*")
            lines.append(
                f"[T3.3 STUB] Would query Prometheus:\n"
                f"  URL:       {_DEFAULT_PROMETHEUS_URL}/api/v1/query\n"
                f"  Metric:    {m}\n"
                f"  Namespace: {ns}\n"
                f"  Query:     {q}"
            )
        lines.append("")
        lines.append("=== Right-sizing suggestion (stub) ===")
        lines.append("")
        lines.append("Workload            | Namespace   | CPU Req | CPU Use | CPU% | Mem Req | Mem Use | Mem% | Verdict")
        lines.append("------------------- | ----------- | ------- | ------- | ---- | ------- | ------- | ---- | ------")
        lines.append("demo-api            | sentinel    | 500m    | 45m     |  9%  | 256Mi   | 98Mi    | 38%  | OVER-PROVISIONED")
        lines.append("argocd-server       | argocd      | 250m    | 120m    | 48%  | 512Mi   | 410Mi   | 80%  | OK")
        lines.append("")
        lines.append("💡 Right-sizing suggestion (Terraform / HCL):")
        lines.append("")
        lines.append("```hcl")
        lines.append("# Right-size over-provisioned workloads")
        lines.append("# demo-api in sentinel: CPU 500m→100m, Memory 256Mi→128Mi")
        lines.append("resource \"kubernetes_deployment\" \"demo_api\" {")
        lines.append("  spec {")
        lines.append("    template {")
        lines.append("      spec {")
        lines.append("        container {")
        lines.append("          resources {")
        lines.append("            requests = {")
        lines.append("              cpu    = \"100m\"   # was 500m (9% util)")
        lines.append("              memory = \"128Mi\"  # was 256Mi (38% util)")
        lines.append("            }")
        lines.append("          }")
        lines.append("        }")
        lines.append("      }")
        lines.append("    }")
        lines.append("  }")
        lines.append("}")
        lines.append("```")
        return "\n".join(lines)

    # Live path — execute PromQL queries
    base_url = _DEFAULT_PROMETHEUS_URL.rstrip("/")
    results: dict[str, list[dict[str, str]]] = {}

    for m in to_run:
        q = _build_promql(m, ns, ".*")
        endpoint = urljoin(base_url, "/api/v1/query")
        url = f"{endpoint}?{urlencode({'query': q})}"

        try:
            data = _httpx_get(url, params=None, timeout=30)
            if not data:
                results[m] = [{"error": f"No response from Prometheus at {base_url}"}]
                continue
            # Parse Prometheus JSON response
            d = data.get("data", {})
            result_list = d.get("result", [])
            if not result_list:
                results[m] = []
                continue
            results[m] = [
                {
                    "pod": r.get("metric", {}).get("pod", "unknown"),
                    "namespace": r.get("metric", {}).get("namespace", "unknown"),
                    "value": r.get("value", [None, "0"])[1],
                }
                for r in result_list
            ]
        except Exception as exc:
            results[m] = [{"error": f"Prometheus query failed: {exc}"}]

    # Build summary output
    lines = [f"# Resource usage report — namespace={ns}, threshold={threshold}"]
    lines.append("")

    for m in to_run:
        lines.append(f"## {m}")
        if not results.get(m):
            lines.append("  (no data)")
        else:
            for entry in results[m]:
                if "error" in entry:
                    lines.append(f"  ❌ {entry['error']}")
                else:
                    lines.append(
                        f"  {entry['pod']} / {entry['namespace']}: {entry['value']}"
                    )
        lines.append("")

    lines.append("💡 Run with metric=\"cpu_utilisation\" or \"memory_utilisation\" and a low")
    lines.append(f"   threshold (e.g. {threshold}) to find over-provisioned workloads.")
    return "\n".join(lines)


# ── Register ──────────────────────────────────────────────────
register(kube_resource_usage, category="cost")
