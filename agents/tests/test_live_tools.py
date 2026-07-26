"""Live integration tests — require a running kind cluster.

These tests validate that tools can talk to the real cluster.  They
are not run by default (needs ``RUN_MODE=live`` or explicit pytest -m
invocation).  Run with::

    RUN_MODE=live pytest agents/tests/test_live_tools.py -v
"""

from __future__ import annotations

import os
import subprocess

import pytest


# Skip all tests in this module unless RUN_MODE=live
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_MODE", "stub") != "live",
    reason="RUN_MODE=live required (needs a live kind cluster)",
)


class TestLiveKubectlGet:
    """kubectl_get returns real cluster output."""

    def test_live_list_pods_observability(self):
        from sentinel_agents.tools.kubectl_get import kubectl_get

        result = kubectl_get.invoke({"resource": "pods", "namespace": "observability"})
        assert "BLOCKED" not in result
        # Real output from a running cluster should have column headers.
        assert "NAME" in result or "No resources found" in result

    def test_live_list_nodes(self):
        from sentinel_agents.tools.kubectl_get import kubectl_get

        result = kubectl_get.invoke({"resource": "nodes"})
        assert "BLOCKED" not in result
        # kind cluster has at least 1 node.
        assert "sentinel-control-plane" in result

    def test_live_list_namespaces(self):
        from sentinel_agents.tools.kubectl_get import kubectl_get

        result = kubectl_get.invoke({"resource": "namespaces"})
        assert "BLOCKED" not in result
        assert "kube-system" in result

    def test_live_list_services_all_ns(self):
        from sentinel_agents.tools.kubectl_get import kubectl_get

        result = kubectl_get.invoke({"resource": "services", "all_namespaces": True})
        assert "BLOCKED" not in result
        # Should find the kubernetes default service.
        assert "kubernetes" in result


class TestLiveKubectlDescribe:
    """kubectl_describe returns real resource details."""

    def test_live_describe_node(self):
        from sentinel_agents.tools.kubectl_describe import kubectl_describe

        result = kubectl_describe.invoke({
            "resource": "node",
            "name": "sentinel-control-plane",
        })
        assert "BLOCKED" not in result
        assert "sentinel-control-plane" in result

    def test_live_describe_namespace(self):
        from sentinel_agents.tools.kubectl_describe import kubectl_describe

        result = kubectl_describe.invoke({
            "resource": "namespace",
            "name": "kube-system",
        })
        assert "BLOCKED" not in result
        assert "kube-system" in result


class TestLivePromQL:
    """PromQL queries return real metrics (needs port-forward or in-cluster)."""

    def test_live_instant_query_up(self):
        import json
        from sentinel_agents.tools.promql_query import promql_query

        result = promql_query.invoke({"query": "up", "operation": "instant"})
        # May be JSON from live API or a connection-error JSON envelope.
        if result.startswith("{"):
            data = json.loads(result)
            if "error" in data:
                pytest.skip(f"Prometheus unreachable: {data['error']}")
            assert data["status"] == "success"
        else:
            assert "BLOCKED" not in result

    def test_live_labels(self):
        import json
        from sentinel_agents.tools.promql_query import promql_query

        result = promql_query.invoke({"query": "", "operation": "labels"})
        if result.startswith("{"):
            data = json.loads(result)
            if "error" in data:
                pytest.skip(f"Prometheus unreachable: {data['error']}")
            assert data["status"] == "success"
        else:
            assert "BLOCKED" not in result


class TestLiveLogQL:
    """LogQL queries return real logs (needs port-forward or in-cluster)."""

    def test_live_labels(self):
        import json
        from sentinel_agents.tools.logql_query import logql_query

        result = logql_query.invoke({"query": "", "operation": "labels"})
        if result.startswith("{"):
            data = json.loads(result)
            if "error" in data:
                pytest.skip(f"Loki unreachable: {data['error']}")
            if "status" in data:
                assert data["status"] == "success"
        else:
            assert "BLOCKED" not in result

    def test_live_query_demo_api_logs(self):
        import json
        from sentinel_agents.tools.logql_query import logql_query

        result = logql_query.invoke({
            "query": '{app="demo-api"}',
            "operation": "query",
            "limit": 5,
        })
        if result.startswith("{"):
            data = json.loads(result)
            if "error" in data:
                pytest.skip(f"Loki unreachable: {data['error']}")
            if "status" in data:
                assert data["status"] == "success"
        else:
            assert "BLOCKED" not in result
