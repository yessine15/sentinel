"""Tests for T3.7 — RemediationPlan spec builder + operator bridge."""

import os

# Stub mode → the bridge returns a preview without touching the cluster.
os.environ["RUN_MODE"] = "stub"
os.environ["LLM_GATEWAY_URL"] = "http://127.0.0.1:1/v1"

from fastapi.testclient import TestClient

from sentinel_api.main import app
from sentinel_api.remediation import build_remediation_plan, to_yaml

client = TestClient(app)

SAMPLE_PLAN = {
    "priority": "high",
    "rationale": "pod OOMKilled 12 times.",
    "steps": [
        {"action": "restart", "target": "deployment/demo-api", "detail": "Restart to clear bad state."},
        {"action": "patch", "target": "deployment/demo-api", "detail": "Raise memory limit 1Gi -> 2Gi."},
    ],
}


# ════════════════════════════════════════════════════════════
# Spec builder
# ════════════════════════════════════════════════════════════
class TestRemediationPlanBuilder:
    """build_remediation_plan produces a valid K8s-style manifest."""

    def test_manifest_shape(self):
        m = build_remediation_plan(SAMPLE_PLAN, incident="ALERTS: oom")
        assert m["apiVersion"] == "sentinel.io/v1"
        assert m["kind"] == "RemediationPlan"
        assert m["metadata"]["namespace"] == "sentinel"
        assert m["metadata"]["name"].startswith("rp-")
        spec = m["spec"]
        assert spec["incident"] == "ALERTS: oom"
        assert spec["priority"] == "high"
        assert spec["dryRun"] is False
        assert spec["approvedBy"] == "human"
        assert spec["steps"] == SAMPLE_PLAN["steps"]

    def test_dry_run_flag(self):
        m = build_remediation_plan(SAMPLE_PLAN, dry_run=True)
        assert m["spec"]["dryRun"] is True

    def test_custom_name_and_ref(self):
        m = build_remediation_plan(
            SAMPLE_PLAN,
            name="rp-fixed-name",
            plan_ref="abc-123",
            namespace="prod",
        )
        assert m["metadata"]["name"] == "rp-fixed-name"
        assert m["metadata"]["namespace"] == "prod"
        assert m["spec"]["planRef"] == "abc-123"

    def test_generated_name_is_slugified_and_short(self):
        m = build_remediation_plan(
            {"priority": "high", "steps": [{"action": "restart", "target": "Deployment/Foo Bar"}]}
        )
        name = m["metadata"]["name"]
        assert name.startswith("rp-deployment-foo-bar-")
        assert len(name) <= 253

    def test_to_yaml_roundtrip_basics(self):
        m = build_remediation_plan(SAMPLE_PLAN, name="rp-yaml")
        y = to_yaml(m)
        assert "apiVersion: sentinel.io/v1" in y
        assert "kind: RemediationPlan" in y
        assert "name: rp-yaml" in y
        assert "action: restart" in y
        assert "dryRun: false" in y

    def test_to_yaml_quotes_lookalike_numbers(self):
        m = build_remediation_plan(
            {"priority": "high", "steps": [{"action": "scale", "target": "deployment/x", "detail": "3 replicas"}]}
        )
        y = to_yaml(m)
        # the incident is empty "" — must be quoted, not `incident:`
        assert 'incident: ""' in y


# ════════════════════════════════════════════════════════════
# Operator bridge API
# ════════════════════════════════════════════════════════════
class TestOperatorBridge:
    """POST /operator/plans validates and (in stub) previews."""

    def test_create_plan(self):
        resp = client.post(
            "/operator/plans",
            json={"plan": SAMPLE_PLAN, "incident": "ALERTS: oom", "dry_run": False},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "Created"
        assert body["kind"] == "RemediationPlan"
        assert body["name"].startswith("rp-")
        assert "[T3.7 STUB]" in body["output"]
        assert set(body["allowed_actions"]) == {
            "restart", "scale", "rollback", "cordon", "drain", "patch", "delete_pod", "escalate"
        }

    def test_dry_run_preview(self):
        resp = client.post(
            "/operator/plans",
            json={"plan": SAMPLE_PLAN, "dry_run": True},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "Preview"
        assert resp.json()["dry_run"] is True

    def test_prebuilt_manifest_takes_precedence(self):
        m = build_remediation_plan(SAMPLE_PLAN, name="rp-prebuilt", incident="x")
        resp = client.post("/operator/plans", json={"manifest": m})
        assert resp.status_code == 200
        assert resp.json()["name"] == "rp-prebuilt"

    def test_disallowed_action_rejected_422(self):
        bad = {
            "priority": "high",
            "steps": [{"action": "delete", "target": "deployment/demo-api", "detail": "bad"}],
        }
        resp = client.post("/operator/plans", json={"plan": bad})
        assert resp.status_code == 422
        assert "delete" in resp.json()["detail"]

    def test_empty_steps_rejected_422(self):
        resp = client.post("/operator/plans", json={"plan": {"priority": "high", "steps": []}})
        assert resp.status_code == 422

    def test_missing_body_rejected_422(self):
        resp = client.post("/operator/plans", json={})
        assert resp.status_code == 422

    def test_wrong_kind_rejected_422(self):
        resp = client.post(
            "/operator/plans",
            json={"manifest": {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "x"}}},
        )
        assert resp.status_code == 422
