"""Tests for the allow-listed tool registry (T2.2).

Every test verifies that disallowed operations are BLOCKED and
allowed operations pass through.
"""

import pytest

from sentinel_agents.tools.base import (
    ALLOWED_KUBECTL_RESOURCES,
    ALLOWED_KUBECTL_VERBS,
    ALLOWED_LOGQL_OPERATIONS,
    ALLOWED_PROMQL_OPERATIONS,
    DisallowedQueryError,
    DisallowedResourceError,
    DisallowedVerbError,
    ToolSecurityError,
    get_all_tools,
    get_tool_names,
    validate_kubectl,
    validate_logql,
    validate_promql,
)


# ════════════════════════════════════════════════════════════
# Registry
# ════════════════════════════════════════════════════════════
class TestRegistry:
    """The tool registry discovers all nine allow-listed tools.

    T2.2: five SRE tools.  T3.2: four security tools (trivy, cve, falco,
    tetragon).  Total: nine.
    """

    def test_get_all_tools_returns_nine_tools(self):
        tools = get_all_tools()
        assert len(tools) == 9  # kubectl_get, describe, promql, logql, rag_search
        #                      #   + trivy_scan, cve_lookup, falco_events, tetragon_events

    def test_get_tool_names_is_sorted(self):
        names = get_tool_names()
        assert names == sorted(names)
        assert "kubectl_get" in names
        assert "kubectl_describe" in names
        assert "promql_query" in names
        assert "logql_query" in names
        assert "rag_search" in names
        # T3.2 security tools
        assert "trivy_scan" in names
        assert "cve_lookup" in names
        assert "falco_events" in names
        assert "tetragon_events" in names

    def test_every_tool_has_name_and_docstring(self):
        for t in get_all_tools():
            assert t.name, f"Tool has no name: {t}"
            assert t.description, f"Tool {t.name} has no description"

    def test_security_tools_categorised(self):
        """T3.2: the four security tools are tagged 'security'."""
        from sentinel_agents.tools import ALLOWED_TOOLS
        sec = [t for t in ALLOWED_TOOLS
               if getattr(t, "__sentinel_category__", None) == "security"]
        assert {t.name for t in sec} == {
            "trivy_scan", "cve_lookup", "falco_events", "tetragon_events"
        }


# ════════════════════════════════════════════════════════════
# kubectl_get — allow-list enforcement
# ════════════════════════════════════════════════════════════
class TestKubectlGetAllowList:
    """kubectl_get blocks disallowed verbs and resources."""

    def test_allowed_pods_returns_stub(self):
        from sentinel_agents.tools.kubectl_get import kubectl_get
        result = kubectl_get.invoke({"resource": "pods"})
        assert "Would run: kubectl get pods" in result
        assert "BLOCKED" not in result

    def test_allowed_deployments_with_namespace(self):
        from sentinel_agents.tools.kubectl_get import kubectl_get
        result = kubectl_get.invoke({"resource": "deployments", "namespace": "default"})
        assert "Would run: kubectl get deployments -n default" in result
        assert "BLOCKED" not in result

    def test_allowed_nodes_all_namespaces(self):
        from sentinel_agents.tools.kubectl_get import kubectl_get
        result = kubectl_get.invoke({"resource": "nodes"})
        assert "Would run: kubectl get nodes" in result
        assert "BLOCKED" not in result

    def test_allowed_services(self):
        from sentinel_agents.tools.kubectl_get import kubectl_get
        result = kubectl_get.invoke({"resource": "services", "all_namespaces": True})
        assert "Would run: kubectl get services --all-namespaces" in result
        assert "BLOCKED" not in result

    def test_allowed_configmaps(self):
        from sentinel_agents.tools.kubectl_get import kubectl_get
        result = kubectl_get.invoke({"resource": "configmaps", "namespace": "kube-system"})
        assert "Would run: kubectl get configmaps -n kube-system" in result
        assert "BLOCKED" not in result

    def test_disallowed_verb_delete_is_blocked(self):
        """kubectl delete should never be callable.  Even if an LLM
        hallucinates a delete, the tool layer catches it."""
        with pytest.raises(DisallowedVerbError, match="delete"):
            validate_kubectl("delete", "pods")

    def test_disallowed_verb_exec_is_blocked(self):
        with pytest.raises(DisallowedVerbError, match="exec"):
            validate_kubectl("exec", "pods")

    def test_disallowed_verb_apply_is_blocked(self):
        with pytest.raises(DisallowedVerbError, match="apply"):
            validate_kubectl("apply", "deployments")

    def test_allowed_resource_secrets_passes(self):
        """Listing secrets (names only, no values) is allowed for SRE."""
        # ``kubectl get secrets`` shows only NAME / TYPE / DATA / AGE.
        # No secret values are exposed with -o wide.
        validate_kubectl("get", "secrets")  # no exception

    def test_allowed_resource_secret_singular_passes(self):
        validate_kubectl("get", "secret")  # no exception

    def test_verb_is_case_insensitive(self):
        """'GET' and 'Get' should work just like 'get'."""
        validate_kubectl("GET", "pods")  # no exception
        validate_kubectl("Get", "deployments")  # no exception

    def test_resource_alias_svc_is_allowed(self):
        """Short resource aliases like 'svc' are in the allow-list."""
        validate_kubectl("get", "svc")  # no exception

    def test_resource_alias_ns_is_allowed(self):
        validate_kubectl("get", "ns")  # no exception

    def test_resource_alias_ing_is_allowed(self):
        validate_kubectl("get", "ing")  # no exception

    def test_resource_alias_pvc_is_allowed(self):
        validate_kubectl("get", "pvc")  # no exception


# ════════════════════════════════════════════════════════════
# kubectl_describe — allow-list enforcement
# ════════════════════════════════════════════════════════════
class TestKubectlDescribeAllowList:
    """kubectl_describe blocks disallowed verbs and resources."""

    def test_allowed_describe_pod(self):
        from sentinel_agents.tools.kubectl_describe import kubectl_describe
        result = kubectl_describe.invoke({"resource": "pod", "name": "my-pod", "namespace": "default"})
        assert "Would run: kubectl describe pod my-pod -n default" in result
        assert "BLOCKED" not in result

    def test_allowed_describe_node(self):
        from sentinel_agents.tools.kubectl_describe import kubectl_describe
        result = kubectl_describe.invoke({"resource": "node", "name": "worker-1"})
        assert "Would run: kubectl describe node worker-1" in result
        assert "BLOCKED" not in result

    def test_allowed_describe_secret_passes(self):
        """Describing secrets (names, labels, annotations) is allowed for SRE.

        ``kubectl describe secret`` shows metadata and the data key count —
        not the decoded values — so it is safe for the agent to inspect.
        """
        validate_kubectl("describe", "secrets")  # no exception


# ════════════════════════════════════════════════════════════
# promql_query — allow-list enforcement
# ════════════════════════════════════════════════════════════
class TestPromQLAllowList:
    """PromQL queries are restricted to read-only operations."""

    def test_allowed_instant_query(self):
        from sentinel_agents.tools.promql_query import promql_query
        result = promql_query.invoke({"query": "up", "operation": "instant"})
        assert "Would query Prometheus" in result
        assert "BLOCKED" not in result

    def test_allowed_range_query(self):
        from sentinel_agents.tools.promql_query import promql_query
        result = promql_query.invoke({
            "query": "rate(http_requests_total[5m])",
            "operation": "range",
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-01T01:00:00Z",
        })
        assert "Would query Prometheus" in result
        assert "BLOCKED" not in result

    def test_allowed_labels_operation(self):
        from sentinel_agents.tools.promql_query import promql_query
        result = promql_query.invoke({"query": "", "operation": "labels"})
        assert "Would query Prometheus" in result
        assert "BLOCKED" not in result

    def test_disallowed_delete_series_is_blocked(self):
        with pytest.raises(DisallowedQueryError, match="delete_series"):
            validate_promql("instant", "delete_series({__name__=~'up'})")

    def test_disallowed_clean_tombstones_is_blocked(self):
        with pytest.raises(DisallowedQueryError, match="clean_tombstones"):
            validate_promql("status", "clean_tombstones")

    def test_disallowed_snapshot_is_blocked(self):
        with pytest.raises(DisallowedQueryError, match="snapshot"):
            validate_promql("instant", "tsdb snapshot")

    def test_disallowed_operation_is_blocked(self):
        """A completely made-up operation name should fail."""
        with pytest.raises(DisallowedQueryError, match="fake_op"):
            validate_promql("fake_op", "up")


# ════════════════════════════════════════════════════════════
# logql_query — allow-list enforcement
# ════════════════════════════════════════════════════════════
class TestLogQLAllowList:
    """LogQL queries are restricted to read-only operations."""

    def test_allowed_query(self):
        from sentinel_agents.tools.logql_query import logql_query
        result = logql_query.invoke({
            "query": '{app="demo-api"} |= "error"',
            "operation": "query",
        })
        assert "Would query Loki" in result
        assert "BLOCKED" not in result

    def test_allowed_query_range(self):
        from sentinel_agents.tools.logql_query import logql_query
        result = logql_query.invoke({
            "query": '{app="demo-api"} |= "error"',
            "operation": "query_range",
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-01T01:00:00Z",
        })
        assert "Would query Loki" in result
        assert "BLOCKED" not in result

    def test_allowed_labels_operation(self):
        from sentinel_agents.tools.logql_query import logql_query
        result = logql_query.invoke({"query": "", "operation": "labels"})
        assert "Would query Loki" in result
        assert "BLOCKED" not in result

    def test_disallowed_push_is_blocked(self):
        with pytest.raises(DisallowedQueryError):
            validate_logql("query", "/loki/api/v1/push")

    def test_disallowed_delete_is_blocked(self):
        with pytest.raises(DisallowedQueryError, match="delete"):
            validate_logql("query", "delete everything")

    def test_disallowed_flush_is_blocked(self):
        with pytest.raises(DisallowedQueryError, match="flush"):
            validate_logql("query", "flush the ingester")

    def test_disallowed_operation_is_blocked(self):
        with pytest.raises(DisallowedQueryError, match="fake_op"):
            validate_logql("fake_op", "up")


# ════════════════════════════════════════════════════════════
# Tool function smoke tests — invoke each tool directly
# ════════════════════════════════════════════════════════════
class TestToolSmoke:
    """Every registered tool can be invoked without raising."""

    def test_kubectl_get_invoke(self):
        from sentinel_agents.tools.kubectl_get import kubectl_get
        result = kubectl_get.invoke({"resource": "pods", "namespace": "default"})
        assert "Would run: kubectl get pods" in result
        assert "BLOCKED" not in result

    def test_kubectl_describe_invoke(self):
        from sentinel_agents.tools.kubectl_describe import kubectl_describe
        result = kubectl_describe.invoke({
            "resource": "deployment",
            "name": "nginx",
            "namespace": "default",
        })
        assert "Would run: kubectl describe deployment nginx" in result
        assert "BLOCKED" not in result

    def test_promql_query_invoke(self):
        from sentinel_agents.tools.promql_query import promql_query
        result = promql_query.invoke({
            "query": "container_memory_usage_bytes",
            "operation": "instant",
        })
        assert "Would query Prometheus" in result
        assert "BLOCKED" not in result

    def test_logql_query_invoke(self):
        from sentinel_agents.tools.logql_query import logql_query
        result = logql_query.invoke({
            "query": '{app="demo-api"}',
            "operation": "query",
        })
        assert "Would query Loki" in result
        assert "BLOCKED" not in result


# ════════════════════════════════════════════════════════════
# rag_search — KB retrieval tool
# ════════════════════════════════════════════════════════════
class TestRagSearch:
    """rag_search handles errors gracefully."""
    # NOTE: Integration tests against a live Qdrant are in
    #       agents/tests/test_live_tools.py — these only test that
    #       the tool is registered and handles error paths safely.

    def test_empty_query_returns_error(self):
        from sentinel_agents.tools.rag_search import rag_search
        result = rag_search.invoke({"query": ""})
        assert "non-empty" in result.lower() or "❌" in result

    def test_whitespace_query_returns_error(self):
        from sentinel_agents.tools.rag_search import rag_search
        result = rag_search.invoke({"query": "   "})
        assert "non-empty" in result.lower() or "❌" in result

    def test_rag_search_is_registered(self):
        """rag_search appears in the tool registry."""
        from sentinel_agents.tools import get_tool_names
        assert "rag_search" in get_tool_names()

    def test_rag_search_has_category(self):
        """rag_search is tagged with category 'rag'."""
        from sentinel_agents.tools.rag_search import rag_search
        assert getattr(rag_search, "__sentinel_category__", None) == "rag"


# ════════════════════════════════════════════════════════════
# Exception hierarchy
# ════════════════════════════════════════════════════════════
class TestExceptionHierarchy:
    """All security exceptions inherit from ToolSecurityError."""

    def test_disallowed_verb_is_tool_security_error(self):
        with pytest.raises(ToolSecurityError):
            raise DisallowedVerbError("test")

    def test_disallowed_resource_is_tool_security_error(self):
        with pytest.raises(ToolSecurityError):
            raise DisallowedResourceError("test")

    def test_disallowed_query_is_tool_security_error(self):
        with pytest.raises(ToolSecurityError):
            raise DisallowedQueryError("test")


# ════════════════════════════════════════════════════════════
# Allow-list constant immutability
# ════════════════════════════════════════════════════════════
class TestAllowLists:
    """The allow-list constants are frozensets — cannot be mutated."""

    def test_kubectl_verbs_is_frozenset(self):
        assert isinstance(ALLOWED_KUBECTL_VERBS, frozenset)

    def test_kubectl_resources_is_frozenset(self):
        assert isinstance(ALLOWED_KUBECTL_RESOURCES, frozenset)

    def test_promql_operations_is_frozenset(self):
        assert isinstance(ALLOWED_PROMQL_OPERATIONS, frozenset)

    def test_logql_operations_is_frozenset(self):
        assert isinstance(ALLOWED_LOGQL_OPERATIONS, frozenset)

    def test_dangerous_kubectl_verbs_are_not_allowed(self):
        """Explicit safety check: dangerous verbs MUST NOT be in the set."""
        dangerous = {"delete", "exec", "apply", "patch", "edit", "create",
                     "replace", "rollout", "drain", "cordon", "uncordon"}
        for verb in dangerous:
            assert verb not in ALLOWED_KUBECTL_VERBS, (
                f"DANGER: '{verb}' is in the kubectl verb allow-list!"
            )

    def test_dangerous_promql_operations_are_not_allowed(self):
        """Write/delete Prometheus operations MUST NOT be in the set."""
        dangerous = {"delete_series", "clean_tombstones", "snapshot",
                     "admin", "tsdb"}
        for op in dangerous:
            assert op not in ALLOWED_PROMQL_OPERATIONS, (
                f"DANGER: '{op}' is in the PromQL operation allow-list!"
            )
