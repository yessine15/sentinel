"""Tests for T3.6 — plan store + /plans API + graph resume."""

import os

# Force stub mode so the plan store uses the in-memory backend and the
# LLM gateway is offline (deterministic fallback paths).
os.environ["RUN_MODE"] = "stub"
os.environ["LLM_GATEWAY_URL"] = "http://127.0.0.1:1/v1"

import pytest
from fastapi.testclient import TestClient

from sentinel_api.main import app
from sentinel_api.plans import (
    MemoryPlanStore,
    get_plan,
    get_plan_store,
    list_plans,
    set_plan_status,
)

client = TestClient(app)

SAMPLE_PLAN = {
    "priority": "high",
    "rationale": "api pod is crash-looping after OOMKill.",
    "steps": [
        {"action": "restart", "target": "deployment/demo-api", "detail": "Restart to clear bad state."},
        {"action": "patch", "target": "deployment/demo-api", "detail": "Raise memory limit 1Gi → 2Gi."},
    ],
}


@pytest.fixture(autouse=True)
def _fresh_store():
    """Reset the in-memory store between tests."""
    get_plan_store.__globals__["_store"] = MemoryPlanStore()
    yield


# ════════════════════════════════════════════════════════════
# Plan store
# ════════════════════════════════════════════════════════════
class TestPlanStore:
    """The store persists pending plans and transitions status."""

    def test_store_is_memory_in_stub_mode(self):
        from sentinel_api.plans import MemoryPlanStore

        assert isinstance(get_plan_store(), MemoryPlanStore)

    def test_create_plan(self):
        plan = get_plan_store().create_plan("ALERTS: oom", SAMPLE_PLAN, synthesis="x")
        assert plan.status == "pending"
        assert plan.plan["steps"]
        assert plan.id

    def test_get_plan_roundtrip(self):
        store = get_plan_store()
        created = store.create_plan("incident", SAMPLE_PLAN)
        fetched = store.get_plan(created.id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.plan == SAMPLE_PLAN

    def test_get_missing_plan_returns_none(self):
        assert get_plan_store().get_plan("nope") is None

    def test_list_plans_newest_first(self):
        store = get_plan_store()
        a = store.create_plan("one", SAMPLE_PLAN)
        b = store.create_plan("two", SAMPLE_PLAN)
        plans = store.list_plans()
        assert {p.id for p in plans} == {a.id, b.id}
        assert plans[0].id == b.id  # newest first

    def test_list_plans_filter_by_status(self):
        store = get_plan_store()
        a = store.create_plan("one", SAMPLE_PLAN)
        store.set_plan_status(a.id, "approved")
        store.create_plan("two", SAMPLE_PLAN)
        pending = store.list_plans("pending")
        approved = store.list_plans("approved")
        assert len(pending) == 1
        assert len(approved) == 1
        assert approved[0].id == a.id

    def test_set_status_approved(self):
        store = get_plan_store()
        created = store.create_plan("incident", SAMPLE_PLAN)
        updated = store.set_plan_status(created.id, "approved")
        assert updated is not None
        assert updated.status == "approved"
        assert updated.decided_at is not None

    def test_set_status_invalid_raises(self):
        store = get_plan_store()
        created = store.create_plan("incident", SAMPLE_PLAN)
        with pytest.raises(ValueError):
            store.set_plan_status(created.id, "banana")

    def test_set_status_missing_returns_none(self):
        assert get_plan_store().set_plan_status("nope", "approved") is None

    def test_convenience_wrappers(self):
        created = get_plan_store().create_plan("incident", SAMPLE_PLAN)
        assert get_plan(created.id) is not None
        assert list_plans()  # non-empty
        assert set_plan_status(created.id, "rejected").status == "rejected"


# ════════════════════════════════════════════════════════════
# /plans API
# ════════════════════════════════════════════════════════════
class TestPlansAPI:
    """The plans endpoints expose the full lifecycle."""

    def _create(self):
        resp = client.post(
            "/plans",
            json={"incident": "ALERTS: kube_pod_oom", "plan": SAMPLE_PLAN, "synthesis": "s"},
        )
        assert resp.status_code == 200
        return resp.json()

    def test_create_plan(self):
        data = self._create()
        assert data["status"] == "pending"
        assert data["plan"]["priority"] == "high"
        assert data["id"]

    def test_create_plan_validates_body(self):
        resp = client.post("/plans", json={"incident": "", "plan": {}})
        assert resp.status_code == 422

    def test_list_plans(self):
        self._create()
        resp = client.get("/plans")
        assert resp.status_code == 200
        assert len(resp.json()["plans"]) == 1

    def test_list_plans_filter(self):
        self._create()
        resp = client.get("/plans?status=pending")
        assert len(resp.json()["plans"]) == 1
        resp = client.get("/plans?status=approved")
        assert resp.json()["plans"] == []
        resp = client.get("/plans?status=bogus")
        assert resp.status_code == 422

    def test_get_plan(self):
        plan = self._create()
        resp = client.get(f"/plans/{plan['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == plan["id"]

    def test_get_plan_404(self):
        resp = client.get("/plans/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_approve_unblocks_graph(self):
        """T3.6 acceptance: approving a plan unblocks the graph."""
        plan = self._create()
        resp = client.post(f"/plans/{plan['id']}/approve")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "approved"
        # The graph ran past the human gate.
        assert body["approval_status"] == "approved"

    def test_reject_unblocks_graph(self):
        plan = self._create()
        resp = client.post(f"/plans/{plan['id']}/reject")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "rejected"
        assert body["approval_status"] == "rejected"

    def test_approve_missing_plan_404(self):
        resp = client.post("/plans/00000000-0000-0000-0000-000000000000/approve")
        assert resp.status_code == 404
