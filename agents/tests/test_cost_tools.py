"""Tests for the T3.3 Cost Agent tools — kube_resource_usage."""

import pytest

from sentinel_agents.tools.base import (
    ALLOWED_COST_METRICS,
    ALLOWED_COST_RESOURCES,
    DisallowedQueryError,
    validate_cost_metric,
    validate_cost_resource,
)
from sentinel_agents.tools.kube_resource_usage import kube_resource_usage


# ─────────────────────────────────────────────────────────────
# Allow-list shape
# ─────────────────────────────────────────────────────────────
class TestCostAllowLists:
    """The cost allow-lists are correctly defined."""

    def test_cost_metrics_are_frozenset(self):
        assert isinstance(ALLOWED_COST_METRICS, frozenset)

    def test_cost_resources_are_frozenset(self):
        assert isinstance(ALLOWED_COST_RESOURCES, frozenset)

    def test_core_metrics_present(self):
        assert "cpu_requests" in ALLOWED_COST_METRICS
        assert "cpu_usage" in ALLOWED_COST_METRICS
        assert "cpu_utilisation" in ALLOWED_COST_METRICS
        assert "memory_requests" in ALLOWED_COST_METRICS
        assert "memory_usage" in ALLOWED_COST_METRICS
        assert "memory_utilisation" in ALLOWED_COST_METRICS
        assert "all" in ALLOWED_COST_METRICS

    def test_workload_resources_present(self):
        assert "deployments" in ALLOWED_COST_RESOURCES
        assert "deployment" in ALLOWED_COST_RESOURCES
        assert "statefulsets" in ALLOWED_COST_RESOURCES
        assert "daemonsets" in ALLOWED_COST_RESOURCES


# ─────────────────────────────────────────────────────────────
# Validators
# ─────────────────────────────────────────────────────────────
class TestCostMetricValidator:
    """validate_cost_metric enforces the allow-list."""

    def test_all_allowed(self):
        validate_cost_metric("all")  # should not raise

    def test_cpu_requests_allowed(self):
        validate_cost_metric("cpu_requests")

    def test_cpu_utilisation_allowed(self):
        validate_cost_metric("cpu_utilisation")

    def test_memory_usage_allowed(self):
        validate_cost_metric("memory_usage")

    def test_case_insensitive(self):
        validate_cost_metric("CPU_REQUESTS")  # uppercase OK
        validate_cost_metric("Cpu_Usage")     # mixed case OK

    def test_whitespace_trimmed(self):
        validate_cost_metric("  all  ")

    def test_disallowed_metric_blocked(self):
        with pytest.raises(DisallowedQueryError):
            validate_cost_metric("disk_usage")

    def test_empty_metric_blocked(self):
        with pytest.raises(DisallowedQueryError):
            validate_cost_metric("")

    def test_none_metric_blocked(self):
        with pytest.raises(DisallowedQueryError):
            validate_cost_metric(None)


class TestCostResourceValidator:
    """validate_cost_resource enforces the allow-list."""

    def test_deployments_allowed(self):
        validate_cost_resource("deployments")

    def test_deployment_singular_allowed(self):
        validate_cost_resource("deployment")

    def test_statefulsets_allowed(self):
        validate_cost_resource("statefulsets")

    def test_daemonsets_allowed(self):
        validate_cost_resource("daemonsets")

    def test_jobs_allowed(self):
        validate_cost_resource("jobs")

    def test_pods_allowed(self):
        validate_cost_resource("pods")

    def test_case_insensitive(self):
        validate_cost_resource("Deployments")
        validate_cost_resource("STATEFULSETS")

    def test_disallowed_resource_blocked(self):
        with pytest.raises(DisallowedQueryError):
            validate_cost_resource("secrets")

    def test_empty_resource_blocked(self):
        with pytest.raises(DisallowedQueryError):
            validate_cost_resource("")

    def test_none_resource_blocked(self):
        with pytest.raises(DisallowedQueryError):
            validate_cost_resource(None)


# ─────────────────────────────────────────────────────────────
# Tool invocation (stub mode)
# ─────────────────────────────────────────────────────────────
class TestKubeResourceUsageStub:
    """kube_resource_usage returns stub output in RUN_MODE=stub."""

    def test_metric_all_returns_stub(self):
        result = kube_resource_usage.invoke({"metric": "all"})
        assert "[T3.3 STUB]" in result
        assert "cpu_utilisation" in result
        assert "memory_utilisation" in result

    def test_metric_cpu_utilisation_returns_stub(self):
        result = kube_resource_usage.invoke({"metric": "cpu_utilisation"})
        assert "[T3.3 STUB]" in result
        assert "cpu_utilisation" in result

    def test_metric_memory_utilisation_returns_stub(self):
        result = kube_resource_usage.invoke({"metric": "memory_utilisation"})
        assert "[T3.3 STUB]" in result
        assert "memory_utilisation" in result

    def test_metric_cpu_requests_returns_stub(self):
        result = kube_resource_usage.invoke({"metric": "cpu_requests"})
        assert "[T3.3 STUB]" in result
        assert "cpu_requests" in result

    def test_stub_includes_right_sizing_suggestion(self):
        result = kube_resource_usage.invoke({"metric": "all"})
        assert "Right-sizing suggestion" in result or "right-sizing" in result.lower()
        assert "Terraform" in result
        assert "OVER-PROVISIONED" in result

    def test_stub_includes_terraform_hcl(self):
        result = kube_resource_usage.invoke({"metric": "all"})
        assert "```hcl" in result
        assert "resource \"kubernetes_deployment\"" in result

    def test_namespace_filter_in_stub(self):
        result = kube_resource_usage.invoke({
            "metric": "cpu_utilisation",
            "namespace": "sentinel",
        })
        assert "[T3.3 STUB]" in result
        assert "sentinel" in result

    def test_threshold_in_stub(self):
        result = kube_resource_usage.invoke({
            "metric": "all",
            "threshold": 0.2,
        })
        assert "[T3.3 STUB]" in result

    def test_resource_filter_in_stub(self):
        result = kube_resource_usage.invoke({
            "metric": "all",
            "resource": "statefulsets",
        })
        assert "[T3.3 STUB]" in result

    def test_disallowed_metric_blocked(self):
        result = kube_resource_usage.invoke({"metric": "disk_io"})
        assert "BLOCKED" in result

    def test_disallowed_resource_blocked(self):
        result = kube_resource_usage.invoke({
            "metric": "cpu_utilisation",
            "resource": "secrets",
        })
        assert "BLOCKED" in result


# ─────────────────────────────────────────────────────────────
# Tool registration
# ─────────────────────────────────────────────────────────────
class TestCostToolRegistration:
    """kube_resource_usage is properly registered in the tool registry."""

    def test_tool_is_registered(self):
        from sentinel_agents.tools import ALLOWED_TOOLS
        names = {t.name for t in ALLOWED_TOOLS}
        assert "kube_resource_usage" in names

    def test_tool_has_category(self):
        from sentinel_agents.tools import ALLOWED_TOOLS
        for t in ALLOWED_TOOLS:
            if t.name == "kube_resource_usage":
                cat = getattr(t, "__sentinel_category__", None)
                assert cat == "cost", f"Expected category 'cost', got {cat!r}"
                return
        pytest.fail("kube_resource_usage not found in ALLOWED_TOOLS")

    def test_tool_has_docstring(self):
        assert kube_resource_usage.description is not None
        assert len(kube_resource_usage.description) > 50

    def test_tool_count_is_ten(self):
        """After T3.3: 10 tools total (5 SRE + 4 security + 1 cost)."""
        from sentinel_agents.tools import ALLOWED_TOOLS
        assert len(ALLOWED_TOOLS) == 10
