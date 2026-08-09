"""Tests for the T3.12 Postmortem Agent — the closed-loop writeup node."""

import os

# Force stub mode (tool previews, memory stores) + LLM gateway offline.
os.environ["RUN_MODE"] = "stub"
os.environ["LLM_GATEWAY_URL"] = "http://127.0.0.1:1/v1"

import pytest

from sentinel_agents.graph import (
    AgentState,
    _fetch_plan_verification,
    postmortem_agent_node,
    resume_plan_graph_detailed,
)

SAMPLE_PLAN = {
    "priority": "high",
    "rationale": "api pod is crash-looping after OOMKill.",
    "steps": [
        {"action": "restart", "target": "deployment/demo-api", "detail": "Restart to clear bad state."},
        {"action": "patch", "target": "deployment/demo-api", "detail": "Raise memory limit 1Gi -> 2Gi."},
    ],
}

EXECUTOR_RESULT = {
    "status": "Created",
    "name": "rp-demo-api-1234abcd",
    "namespace": "sentinel",
    "dry_run": False,
}


def _base_state(**overrides) -> AgentState:
    state: AgentState = {
        "messages": [],
        "tool_calls": [],
        "scratchpad": {
            "plan_ref": "plan-1",
            "incident": "ALERTS: kube_pod_oom demo-api",
            "synthesis": "Pod demo-api OOMKilled 12 times; memory limit too low.",
        },
        "routing": "incident",
        "classification_json": "",
        "incident": "ALERTS: kube_pod_oom demo-api",
        "synthesis": "Pod demo-api OOMKilled 12 times; memory limit too low.",
        "plan": SAMPLE_PLAN,
        "approval_status": "approved",
        "remediation_plan": EXECUTOR_RESULT,
        "executor_status": "created",
        "postmortem": {},
        "postmortem_status": "",
    }
    state.update(overrides)
    return state


@pytest.fixture
def mock_ingest(monkeypatch):
    """Deterministic KB ingestion: record args, return a chunk count."""
    calls = {}

    def _fake_ingest(title, content, plan_id="", **kwargs):
        calls["title"] = title
        calls["content"] = content
        calls["plan_id"] = plan_id
        return 3  # fake chunk count

    import sentinel_rag.ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "ingest_postmortem", _fake_ingest)
    return calls


# ════════════════════════════════════════════════════════════
# Node guards
# ════════════════════════════════════════════════════════════
class TestPostmortemNodeGuards:
    """The node only writes when a plan was actually created."""

    def test_skipped_when_not_approved(self):
        out = postmortem_agent_node(_base_state(approval_status="rejected"))
        assert out["postmortem_status"] == "skipped"

    def test_skipped_when_executor_blocked(self):
        out = postmortem_agent_node(_base_state(executor_status="blocked"))
        assert out["postmortem_status"] == "skipped"

    def test_skipped_when_executor_preview(self):
        out = postmortem_agent_node(_base_state(executor_status="preview"))
        assert out["postmortem_status"] == "skipped"

    def test_idempotent_second_call(self, mock_ingest):
        """Once written, the node never writes a second postmortem."""
        state = _base_state()
        state["scratchpad"]["postmortem_written"] = True
        state["scratchpad"]["postmortem_status"] = "ingested"
        out = postmortem_agent_node(state)
        assert out["postmortem_status"] == "ingested"
        assert mock_ingest == {}  # ingestion job NOT re-spawned


# ════════════════════════════════════════════════════════════
# Node happy path
# ════════════════════════════════════════════════════════════
class TestPostmortemNodeWrites:
    """Happy path: writeup stored (memory store in stub mode) + ingested."""

    def test_creates_postmortem_in_store(self, mock_ingest):
        from sentinel_api.postmortems import get_postmortem_store

        store = get_postmortem_store()
        out = postmortem_agent_node(_base_state())
        assert out["postmortem_status"] == "ingested"
        pm = store.get_postmortem(out["postmortem"]["id"])
        assert pm is not None
        assert pm.plan_id == "plan-1"
        assert "Postmortem" in pm.content
        assert "demo-api" in pm.content

    def test_ingest_job_receives_writeup(self, mock_ingest):
        out = postmortem_agent_node(_base_state())
        assert mock_ingest["plan_id"] == out["postmortem"]["id"]
        assert "## Verification" in mock_ingest["content"]

    def test_markdown_contains_all_sections(self, mock_ingest):
        out = postmortem_agent_node(_base_state())
        content = out["postmortem"]["content"]
        for section in (
            "# Postmortem",
            "## Summary",
            "## Incident",
            "## Assessment",
            "## Remediation plan",
            "## Execution",
            "## Verification",
            "## Lessons",
        ):
            assert section in content
        # Plan steps rendered
        assert "`restart`" in content
        assert "`patch`" in content
        # Verification unknown in stub mode — recorded, not fatal
        assert "unknown" in content

    def test_postmortem_state_populated(self, mock_ingest):
        out = postmortem_agent_node(_base_state())
        pm = out["postmortem"]
        assert pm["id"]
        assert pm["status"] == "ingested"
        assert pm["chunks"] == 3
        assert pm["verification"]["state"] == "unknown"

    def test_ingest_failure_marks_failed_not_raise(self, monkeypatch):
        import sentinel_rag.ingest as ingest_mod

        def _boom(*args, **kwargs):
            raise ConnectionError("qdrant down")

        monkeypatch.setattr(ingest_mod, "ingest_postmortem", _boom)
        out = postmortem_agent_node(_base_state())
        assert out["postmortem_status"] == "failed"
        assert out["postmortem"]["status"] == "failed"
        assert "qdrant down" in out["scratchpad"].get("postmortem_ingest_error", "")

    def test_store_failure_marks_failed_not_raise(self, monkeypatch):
        import sentinel_api.postmortems as pm_mod

        def _boom(*args, **kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(pm_mod, "create_postmortem", _boom)
        out = postmortem_agent_node(_base_state())
        assert out["postmortem_status"] == "failed"


# ════════════════════════════════════════════════════════════
# Verification fetch
# ════════════════════════════════════════════════════════════
class TestFetchPlanVerification:
    def test_stub_mode_returns_unknown(self):
        verif = _fetch_plan_verification("rp-demo-api")
        assert verif["state"] == "unknown"

    def test_live_parses_status_and_passes_namespace(self, monkeypatch):
        from sentinel_agents.tools import base as tools_base

        calls = {}

        def _fake_is_stub():
            return False

        def _fake_run(cmd, timeout=60):
            calls["cmd"] = cmd
            return '{"status": {"state": "Verified", "message": "deploy ready", "verifiedAt": "2026-08-09T10:00:00Z"}}'

        monkeypatch.setattr(tools_base, "is_stub", _fake_is_stub)
        monkeypatch.setattr(tools_base, "run_subprocess", _fake_run)
        verif = _fetch_plan_verification("rp-demo-api", "sentinel")
        assert verif["state"] == "Verified"
        assert verif["message"] == "deploy ready"
        assert "-n" in calls["cmd"]
        assert "sentinel" in calls["cmd"]

    def test_live_non_json_returns_unknown(self, monkeypatch):
        from sentinel_agents.tools import base as tools_base

        monkeypatch.setattr(tools_base, "is_stub", lambda: False)
        monkeypatch.setattr(
            tools_base, "run_subprocess", lambda *a, **k: "command exited with code 1"
        )
        verif = _fetch_plan_verification("rp-demo-api", "sentinel")
        assert verif["state"] == "unknown"


# ════════════════════════════════════════════════════════════
# Resume graph integration
# ════════════════════════════════════════════════════════════
class TestResumeGraphPostmortem:
    """Approving a plan now produces a postmortem end-to-end."""

    def _plan_doc(self, pid: str) -> dict:
        return {
            "id": pid,
            "incident": "ALERTS: kube_pod_oom demo-api",
            "synthesis": "Pod OOMKilled; limit too low.",
            "plan": SAMPLE_PLAN,
            "status": "pending",
        }

    def test_approve_runs_postmortem_agent(self, mock_ingest):
        outcome = resume_plan_graph_detailed(self._plan_doc("plan-9"), "approved")
        assert outcome["approval_status"] == "approved"
        assert outcome["executor_status"] == "created"
        assert outcome["postmortem_status"] == "ingested"
        pm = outcome["postmortem"]
        assert pm["id"]
        assert "Postmortem" in pm["content"]
        assert pm["verification"]["state"] == "unknown"

    def test_reject_skips_postmortem(self):
        outcome = resume_plan_graph_detailed(self._plan_doc("plan-10"), "rejected")
        assert outcome["approval_status"] == "rejected"
        assert outcome["postmortem_status"] == "skipped"
        assert outcome["postmortem"] == {}

    def test_resume_plan_graph_short_form_still_works(self):
        from sentinel_agents.graph import resume_plan_graph

        assert resume_plan_graph(self._plan_doc("plan-11"), "approved") == "approved"


# ════════════════════════════════════════════════════════════
# Tool surface check (no accidental cluster writes)
# ════════════════════════════════════════════════════════════
class TestNoNewTools:
    def test_tool_count_unchanged(self):
        """T3.12 adds NO tools — the postmortem node is direct code."""
        from sentinel_agents.tools import ALLOWED_TOOLS

        assert len(ALLOWED_TOOLS) == 12
