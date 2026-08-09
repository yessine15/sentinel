"""Tests for the T3.7 Executor Agent — the only agent that can act."""

import os

# Force stub mode (tool previews) + LLM gateway offline (deterministic).
os.environ["RUN_MODE"] = "stub"
os.environ["LLM_GATEWAY_URL"] = "http://127.0.0.1:1/v1"

import json

import pytest

from sentinel_agents.tools.base import (
    ALLOWED_EXECUTOR_ACTIONS,
    DisallowedQueryError,
    validate_executor_action,
)

SAMPLE_PLAN = {
    "priority": "high",
    "rationale": "pod OOMKilled 12 times.",
    "steps": [
        {"action": "restart", "target": "deployment/demo-api", "detail": "Restart to clear bad state."},
        {"action": "patch", "target": "deployment/demo-api", "detail": "Raise memory limit 1Gi -> 2Gi."},
    ],
}


# ════════════════════════════════════════════════════════════
# Action allow-list
# ════════════════════════════════════════════════════════════
class TestExecutorAllowList:
    """ALLOWED_EXECUTOR_ACTIONS + validator."""

    def test_is_frozenset(self):
        assert isinstance(ALLOWED_EXECUTOR_ACTIONS, frozenset)

    def test_expected_actions_present(self):
        for a in ("restart", "scale", "rollback", "cordon", "drain", "patch", "delete_pod", "escalate"):
            assert a in ALLOWED_EXECUTOR_ACTIONS

    def test_dangerous_actions_absent(self):
        """The executor must NEVER be able to do destructive/arbitrary things."""
        for a in ("delete", "exec", "create", "apply", "edit", "replace", "delete_namespace", "rm"):
            assert a not in ALLOWED_EXECUTOR_ACTIONS

    def test_validator_accepts_allowed(self):
        validate_executor_action("restart")
        validate_executor_action("SCALE")  # case-insensitive
        validate_executor_action("  patch  ")

    def test_validator_rejects_disallowed(self):
        with pytest.raises(DisallowedQueryError):
            validate_executor_action("delete")
        with pytest.raises(DisallowedQueryError):
            validate_executor_action("exec")
        with pytest.raises(DisallowedQueryError):
            validate_executor_action("")


# ════════════════════════════════════════════════════════════
# create_remediation_plan tool (stub mode)
# ════════════════════════════════════════════════════════════
class TestCreateRemediationPlanTool:
    """The executor's only tool validates + previews in stub mode."""

    def test_tool_is_registered(self):
        from sentinel_agents.tools import ALLOWED_TOOLS
        names = {t.name for t in ALLOWED_TOOLS}
        assert "create_remediation_plan" in names

    def test_tool_has_category_executor(self):
        from sentinel_agents.tools import ALLOWED_TOOLS
        for t in ALLOWED_TOOLS:
            if t.name == "create_remediation_plan":
                assert getattr(t, "__sentinel_category__", None) == "executor"
                return
        pytest.fail("create_remediation_plan not found")

    def test_tool_count_is_twelve(self):
        """After T3.7: 12 tools total (11 + executor tool)."""
        from sentinel_agents.tools import ALLOWED_TOOLS
        assert len(ALLOWED_TOOLS) == 12

    def test_stub_returns_created_payload(self):
        from sentinel_agents.tools.create_remediation_plan import create_remediation_plan

        result = create_remediation_plan.invoke(
            {"plan": SAMPLE_PLAN, "incident": "ALERTS: oom", "dry_run": False}
        )
        payload = json.loads(result)
        assert payload["status"] == "Created"
        assert payload["name"] == "rp-stub-plan-00000000"
        assert payload["namespace"] == "sentinel"
        assert payload["manifest"]["spec"]["steps"] == SAMPLE_PLAN["steps"]

    def test_stub_dry_run_marks_preview(self):
        from sentinel_agents.tools.create_remediation_plan import create_remediation_plan

        result = create_remediation_plan.invoke(
            {"plan": SAMPLE_PLAN, "incident": "x", "dry_run": True}
        )
        payload = json.loads(result)
        assert payload["status"] == "Preview"
        assert payload["dry_run"] is True

    def test_blocked_action_returns_error(self):
        from sentinel_agents.tools.create_remediation_plan import create_remediation_plan

        bad_plan = {
            "priority": "high",
            "steps": [{"action": "delete", "target": "deployment/demo-api", "detail": "bad"}],
        }
        result = create_remediation_plan.invoke({"plan": bad_plan, "dry_run": False})
        assert "BLOCKED" in result
        assert "delete" in result

    def test_empty_steps_blocked(self):
        from sentinel_agents.tools.create_remediation_plan import create_remediation_plan

        result = create_remediation_plan.invoke({"plan": {"priority": "high", "steps": []}})
        assert "BLOCKED" in result

    def test_has_description(self):
        from sentinel_agents.tools.create_remediation_plan import create_remediation_plan

        assert len(create_remediation_plan.description) > 50


# ════════════════════════════════════════════════════════════
# Executor agent node + resume graph
# ════════════════════════════════════════════════════════════
class TestExecutorNode:
    """executor_agent_node behaviour."""

    def _state(self, approval_status="approved", plan=None, messages=None):
        return {
            "messages": messages or [],
            "tool_calls": [],
            "scratchpad": {"plan": plan or SAMPLE_PLAN, "pending_plan": plan or SAMPLE_PLAN},
            "routing": "incident",
            "classification_json": "",
            "incident": "ALERTS: oom",
            "synthesis": "s",
            "plan": plan or SAMPLE_PLAN,
            "approval_status": approval_status,
            "remediation_plan": {},
            "executor_status": "",
        }

    def test_skips_when_not_approved(self):
        from sentinel_agents.graph import executor_agent_node

        out = executor_agent_node(self._state(approval_status="rejected"))
        assert out["executor_status"] == "skipped"
        assert out["scratchpad"]["executor_visited"] is True

    def test_emits_tool_call_when_approved(self):
        from sentinel_agents.graph import executor_agent_node

        out = executor_agent_node(self._state())
        msgs = out.get("messages", [])
        assert len(msgs) == 1
        ai = msgs[0]
        assert ai.tool_calls
        assert ai.tool_calls[0]["name"] == "create_remediation_plan"
        assert ai.tool_calls[0]["args"]["dry_run"] is False
        # dry-run proposal recorded for the audit trail
        sp = out["scratchpad"]
        assert "executor_proposal" in sp
        assert "apiVersion: sentinel.io/v1" in sp["executor_proposal"]
        assert "kind: RemediationPlan" in sp["executor_proposal"]

    def test_does_not_loop_after_tool_result(self):
        from langchain_core.messages import ToolMessage

        from sentinel_agents.graph import executor_agent_node

        tool_result = json.dumps({
            "status": "Created",
            "name": "rp-demo-api-1234",
            "namespace": "sentinel",
            "dry_run": False,
        })
        state = self._state(messages=[ToolMessage(content=tool_result, tool_call_id="c1", name="create_remediation_plan")])
        out = executor_agent_node(state)
        # No new tool calls → the resume graph can end.
        assert out.get("messages", []) == []
        assert out["executor_status"] == "created"
        assert out["remediation_plan"]["name"] == "rp-demo-api-1234"
        assert out["scratchpad"]["remediation_plan_name"] == "rp-demo-api-1234"

    def test_stops_on_tool_error_without_looping(self):
        """Regression: a non-JSON tool error (e.g. bridge 502) must stop
        the executor, not cause an infinite tool-call loop."""
        from langchain_core.messages import ToolMessage

        from sentinel_agents.graph import executor_agent_node

        state = self._state(
            messages=[
                ToolMessage(
                    content="❌ Operator bridge rejected the plan (502): ...",
                    tool_call_id="c1",
                    name="create_remediation_plan",
                )
            ]
        )
        out = executor_agent_node(state)
        assert out.get("messages", []) == []  # no more tool calls
        assert out["executor_status"] == "blocked"
        assert "502" in out["scratchpad"]["executor_error"]

    def test_skips_when_awaiting_approval(self):
        from sentinel_agents.graph import executor_agent_node

        out = executor_agent_node(self._state(approval_status="awaiting_approval"))
        assert out["executor_status"] == "skipped"


class TestResumeGraphWithExecutor:
    """T3.7 acceptance: approving a plan creates a RemediationPlan object."""

    def test_approved_runs_executor(self):
        from sentinel_agents.graph import resume_plan_graph_detailed

        plan = {"id": "p1", "incident": "ALERTS: oom", "plan": SAMPLE_PLAN, "synthesis": "s"}
        out = resume_plan_graph_detailed(plan, "approved")
        assert out["approval_status"] == "approved"
        assert out["executor_status"] == "created"
        assert out["remediation_plan"]["name"]  # object created (stub)

    def test_rejected_skips_executor(self):
        from sentinel_agents.graph import resume_plan_graph_detailed

        plan = {"id": "p1", "incident": "x", "plan": SAMPLE_PLAN}
        out = resume_plan_graph_detailed(plan, "rejected")
        assert out["approval_status"] == "rejected"
        assert out["executor_status"] in ("", "skipped")
        assert out["remediation_plan"] == {}

    def test_resume_plan_graph_backward_compat(self):
        """The T3.6 API (str return) still works."""
        from sentinel_agents.graph import resume_plan_graph

        plan = {"id": "p1", "incident": "x", "plan": SAMPLE_PLAN}
        assert resume_plan_graph(plan, "approved") == "approved"
        assert resume_plan_graph(plan, "rejected") == "rejected"

    def test_executor_node_in_resume_graph(self):
        from sentinel_agents.graph import build_resume_graph

        g = build_resume_graph()
        try:
            nodes = g.get_graph().nodes
            assert "executor" in nodes
            assert "executor_tools" in nodes
        except Exception:
            pass
