"""Tests for T3.12 — postmortem store + markdown builder + /postmortems API."""

import os

# Force stub mode so stores use the in-memory backends and the LLM
# gateway is offline (deterministic fallback paths).
os.environ["RUN_MODE"] = "stub"
os.environ["LLM_GATEWAY_URL"] = "http://127.0.0.1:1/v1"

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from sentinel_api.main import app
from sentinel_api.postmortem import build_postmortem_markdown
from sentinel_api.postmortems import (
    MemoryPostmortemStore,
    get_postmortem_store,
    list_postmortems,
    set_postmortem_status,
)

client = TestClient(app)

SAMPLE_PLAN = {
    "priority": "high",
    "rationale": "api pod is crash-looping after OOMKill.",
    "steps": [
        {"action": "restart", "target": "deployment/demo-api", "detail": "Restart to clear bad state."},
        {"action": "patch", "target": "deployment/demo-api", "detail": "Raise memory limit 1Gi -> 2Gi."},
    ],
}

SAMPLE_CONTENT = build_postmortem_markdown(
    incident="ALERTS: kube_pod_oom demo-api",
    synthesis="Pod demo-api OOMKilled 12 times.",
    plan=SAMPLE_PLAN,
    executor_result={"status": "Created", "name": "rp-demo-api-1234", "namespace": "sentinel"},
    verification={"state": "Verified", "message": "deploy ready", "verified_at": "2026-08-09T10:00:00Z"},
    plan_ref="plan-1",
    now=datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc),
)


@pytest.fixture(autouse=True)
def _fresh_store():
    """Reset the in-memory postmortem store between tests."""
    get_postmortem_store.__globals__["_store"] = MemoryPostmortemStore()
    yield


# ════════════════════════════════════════════════════════════
# Markdown builder
# ════════════════════════════════════════════════════════════
class TestBuildPostmortemMarkdown:
    """The writeup is deterministic and contains every lifecycle stage."""

    def test_deterministic_with_fixed_now(self):
        a = build_postmortem_markdown(
            incident="i", synthesis="s", plan=SAMPLE_PLAN,
            now=datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc),
        )
        b = build_postmortem_markdown(
            incident="i", synthesis="s", plan=SAMPLE_PLAN,
            now=datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc),
        )
        assert a == b

    def test_all_sections_present(self):
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
            assert section in SAMPLE_CONTENT

    def test_incident_and_synthesis_embedded(self):
        assert "kube_pod_oom demo-api" in SAMPLE_CONTENT
        assert "OOMKilled 12 times" in SAMPLE_CONTENT

    def test_steps_rendered_as_table(self):
        assert "`restart`" in SAMPLE_CONTENT
        assert "`patch`" in SAMPLE_CONTENT
        assert "| Action | Target | Detail |" in SAMPLE_CONTENT

    def test_executor_and_verification_recorded(self):
        assert "rp-demo-api-1234" in SAMPLE_CONTENT
        assert "Verified" in SAMPLE_CONTENT
        assert "deploy ready" in SAMPLE_CONTENT

    def test_pipe_in_detail_escaped(self):
        content = build_postmortem_markdown(
            incident="i",
            synthesis="s",
            plan={"priority": "low", "steps": [{"action": "restart", "target": "d/x", "detail": "a | b"}]},
        )
        assert "a \\| b" in content

    def test_empty_plan_steps(self):
        content = build_postmortem_markdown(incident="i", synthesis="s", plan={})
        assert "_No remediation steps were proposed._" in content


# ════════════════════════════════════════════════════════════
# Postmortem store
# ════════════════════════════════════════════════════════════
class TestPostmortemStore:
    """The store persists writeups and tracks the ingestion status."""

    def test_store_is_memory_in_stub_mode(self):
        assert isinstance(get_postmortem_store(), MemoryPostmortemStore)

    def test_create_postmortem(self):
        pm = get_postmortem_store().create_postmortem("incident", "# Postmortem", plan_id="plan-1")
        assert pm.status == "drafted"
        assert pm.plan_id == "plan-1"
        assert pm.id

    def test_get_roundtrip(self):
        store = get_postmortem_store()
        created = store.create_postmortem("incident", "body", plan_id="p1")
        fetched = store.get_postmortem(created.id)
        assert fetched is not None
        assert fetched.content == "body"
        assert fetched.plan_id == "p1"

    def test_get_missing_returns_none(self):
        assert get_postmortem_store().get_postmortem("nope") is None

    def test_list_newest_first_and_filters(self):
        store = get_postmortem_store()
        a = store.create_postmortem("one", "body", plan_id="p1")
        b = store.create_postmortem("two", "body", plan_id="p2")
        store.set_postmortem_status(a.id, "ingested")

        all_items = store.list_postmortems()
        assert {p.id for p in all_items} == {a.id, b.id}
        assert all_items[0].id == b.id  # newest first

        by_plan = store.list_postmortems(plan_id="p1")
        assert [p.id for p in by_plan] == [a.id]

        by_status = store.list_postmortems(status="ingested")
        assert [p.id for p in by_status] == [a.id]

    def test_set_status_validates(self):
        store = get_postmortem_store()
        pm = store.create_postmortem("i", "body")
        store.set_postmortem_status(pm.id, "failed")
        assert store.get_postmortem(pm.id).status == "failed"
        with pytest.raises(ValueError):
            store.set_postmortem_status(pm.id, "bogus")

    def test_set_status_missing_returns_none(self):
        assert set_postmortem_status("nope", "ingested") is None


# ════════════════════════════════════════════════════════════
# /postmortems API
# ════════════════════════════════════════════════════════════
class TestPostmortemsAPI:
    """REST surface for browsing postmortems + re-running ingestion."""

    def test_list_empty(self):
        r = client.get("/postmortems")
        assert r.status_code == 200
        assert r.json()["postmortems"] == []

    def test_create_and_get(self):
        pm = get_postmortem_store().create_postmortem("incident-x", "# Postmortem", plan_id="p9")
        r = client.get(f"/postmortems/{pm.id}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == pm.id
        assert body["content"] == "# Postmortem"
        assert body["plan_id"] == "p9"

    def test_get_missing_404(self):
        r = client.get("/postmortems/nope")
        assert r.status_code == 404

    def test_list_filters(self):
        store = get_postmortem_store()
        a = store.create_postmortem("one", "body", plan_id="p1")
        store.set_postmortem_status(a.id, "ingested")
        store.create_postmortem("two", "body", plan_id="p2")

        r = client.get("/postmortems", params={"status": "ingested"})
        assert [p["id"] for p in r.json()["postmortems"]] == [a.id]

        r = client.get("/postmortems", params={"plan_id": "p1"})
        assert [p["id"] for p in r.json()["postmortems"]] == [a.id]

    def test_list_invalid_status_422(self):
        r = client.get("/postmortems", params={"status": "bogus"})
        assert r.status_code == 422

    def test_reingest_updates_status(self, monkeypatch):
        pm = get_postmortem_store().create_postmortem("incident", "body", plan_id="p1")
        get_postmortem_store().set_postmortem_status(pm.id, "failed")

        import sentinel_rag.ingest as ingest_mod

        monkeypatch.setattr(ingest_mod, "ingest_postmortem", lambda *a, **k: 5)
        r = client.post(f"/postmortems/{pm.id}/ingest")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ingested"
        assert body["chunks"] == 5

    def test_reingest_missing_404(self):
        r = client.post("/postmortems/nope/ingest")
        assert r.status_code == 404

    def test_reingest_failure_502(self, monkeypatch):
        pm = get_postmortem_store().create_postmortem("incident", "body")

        import sentinel_rag.ingest as ingest_mod

        def _boom(*a, **k):
            raise ConnectionError("qdrant down")

        monkeypatch.setattr(ingest_mod, "ingest_postmortem", _boom)
        r = client.post(f"/postmortems/{pm.id}/ingest")
        assert r.status_code == 502


# ════════════════════════════════════════════════════════════
# Plan approval now surfaces the postmortem
# ════════════════════════════════════════════════════════════
class TestApproveSurfacesPostmortem:
    """POST /plans/{id}/approve reports postmortem_status + postmortem."""

    def _create_plan(self) -> str:
        r = client.post(
            "/plans",
            json={
                "incident": "ALERTS: kube_pod_oom demo-api",
                "synthesis": "Pod OOMKilled.",
                "plan": SAMPLE_PLAN,
            },
        )
        assert r.status_code == 200
        return r.json()["id"]

    def test_approve_returns_postmortem_fields(self, monkeypatch):
        plan_id = self._create_plan()

        import sentinel_rag.ingest as ingest_mod

        monkeypatch.setattr(ingest_mod, "ingest_postmortem", lambda *a, **k: 2)
        r = client.post(f"/plans/{plan_id}/approve")
        assert r.status_code == 200
        body = r.json()
        assert body["approval_status"] == "approved"
        assert body["postmortem_status"] == "ingested"
        assert body["postmortem"]["id"]
        assert "Postmortem" in body["postmortem"]["content"]

        # The writeup is now browsable via the postmortems API too.
        r2 = client.get(f"/postmortems/{body['postmortem']['id']}")
        assert r2.status_code == 200

    def test_reject_returns_skipped(self):
        plan_id = self._create_plan()
        r = client.post(f"/plans/{plan_id}/reject")
        assert r.status_code == 200
        body = r.json()
        assert body["approval_status"] == "rejected"
        assert body["postmortem_status"] == "skipped"
