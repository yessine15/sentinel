"""Create RemediationPlan — the Executor Agent's only write tool (T3.7).

The Executor Agent is the ONLY agent that can act, and this is its only
tool.  It emits a declarative ``RemediationPlan`` spec (allow-listed
action verbs) and submits it through the operator bridge API
(``POST /operator/plans``) — the Executor never runs kubectl itself.

Environment variables
---------------------
OPERATOR_API_URL : str
    Base URL of the operator bridge (default ``http://localhost:8000``;
    in-cluster this becomes the Sentinel API service URL).
"""

from __future__ import annotations

import json
import os
from typing import Any

from langchain_core.tools import tool

from sentinel_agents.tools.base import (
    DisallowedQueryError,
    is_stub,
    register,
    validate_executor_action,
)

_DEFAULT_OPERATOR_URL = os.environ.get("OPERATOR_API_URL", "http://localhost:8000")


def _stub_preview(plan: dict[str, Any], incident: str, dry_run: bool) -> str:
    """Return a deterministic stub preview (safe for unit tests)."""
    manifest = {
        "apiVersion": "sentinel.io/v1",
        "kind": "RemediationPlan",
        "metadata": {"name": "rp-stub-plan-00000000", "namespace": "sentinel"},
        "spec": {
            "incident": incident[:120],
            "priority": str(plan.get("priority", "medium")),
            "rationale": str(plan.get("rationale", "")),
            "dryRun": bool(dry_run),
            "approvedBy": "human",
            "steps": plan.get("steps", []),
        },
    }
    return json.dumps(
        {
            "status": "Preview" if dry_run else "Created",
            "name": manifest["metadata"]["name"],
            "namespace": manifest["metadata"]["namespace"],
            "dry_run": bool(dry_run),
            "manifest": manifest,
        },
        indent=2,
    )


@tool
def create_remediation_plan(
    plan: dict[str, Any],
    incident: str = "",
    dry_run: bool = False,
) -> str:
    """Create a RemediationPlan object for an approved remediation plan.

    This is the Executor Agent's ONLY write tool.  Use it after a
    human has APPROVED a plan (never before).  It converts the
    structured plan ``{priority, rationale, steps[{action, target,
    detail}]}`` into a declarative ``RemediationPlan`` Kubernetes
    object and submits it through the operator bridge, which applies
    it to the cluster.  The Executor itself never runs kubectl.

    Args:
        plan: The approved plan dict — must contain a non-empty
            ``steps`` list; every step's ``action`` must be one of the
            allow-listed verbs (restart, scale, rollback, cordon,
            drain, patch, delete_pod, escalate).
        incident: Raw incident/alert text for traceability.
        dry_run: ``True`` → validate and preview only (the object is
            NOT persisted); ``False`` → create the real object.
            Always start with ``dry_run=True`` and inspect the output
            before creating for real.

    Returns:
        JSON with the created object's name/namespace and status, or a
        ``❌ BLOCKED`` message when a step action is disallowed.
    """
    # ── Allow-list enforcement: every step action must be allowed ──
    steps = plan.get("steps", [])
    if not isinstance(steps, list) or not steps:
        return "❌ BLOCKED: plan.steps must be a non-empty list."
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            return f"❌ BLOCKED: step[{i}] must be an object."
        try:
            validate_executor_action(step.get("action", ""))
        except DisallowedQueryError as exc:
            return f"❌ BLOCKED: step[{i}] {exc}"

    if is_stub():
        return _stub_preview(plan, incident, bool(dry_run))

    # ── Live mode: submit through the operator bridge ──
    import httpx

    url = f"{_DEFAULT_OPERATOR_URL.rstrip('/')}/operator/plans"
    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                url,
                json={
                    "plan": plan,
                    "incident": incident,
                    "dry_run": bool(dry_run),
                    "approved_by": "human",
                },
            )
    except Exception as exc:
        return f"❌ Operator bridge unreachable ({type(exc).__name__}: {exc})"

    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        return f"❌ Operator bridge rejected the plan ({resp.status_code}): {detail}"

    try:
        return json.dumps(resp.json(), indent=2)
    except Exception:
        return resp.text


register(create_remediation_plan, category="executor")
