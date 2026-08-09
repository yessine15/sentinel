"""Operator bridge API (T3.7) — the only write path to the cluster.

The Executor Agent never runs kubectl.  It POSTs a RemediationPlan
manifest here; this bridge validates it (action allow-list), then
applies it to the cluster via ``kubectl apply`` — the auditable single
write path.  T3.8+ replaces the kubectl backend with the real Go
operator's API, but the endpoint contract stays the same.

Endpoints
---------
======================  ==============================================
``POST /operator/plans``  Validate + create a RemediationPlan object.
======================  ==============================================

Environment variables
---------------------
``RUN_MODE`` : str
    ``"stub"`` returns a preview without touching the cluster.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from sentinel_agents.tools.base import (
    ALLOWED_EXECUTOR_ACTIONS,
    DisallowedQueryError,
    validate_executor_action,
)
from sentinel_api.remediation import build_remediation_plan, to_yaml

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/operator", tags=["operator"])


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------
class CreateRemediationPlanRequest(BaseModel):
    """Body for ``POST /operator/plans``.

    Either pass a full ``manifest`` (already built) or a ``plan`` dict
    which is converted via :func:`build_remediation_plan`.
    """

    plan: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured plan {priority, rationale, steps[]}.",
    )
    incident: str = Field(default="", description="Raw incident text.")
    dry_run: bool = Field(default=False, description="True → preview only.")
    approved_by: str = Field(default="human", description="Who approved.")
    plan_ref: str = Field(default="", description="Approval plan UUID.")
    namespace: str = Field(default="sentinel", description="Target namespace.")
    manifest: dict[str, Any] | None = Field(
        default=None,
        description="Pre-built RemediationPlan manifest (takes precedence).",
    )


def _validate_steps(plan: dict[str, Any]) -> None:
    """Raise HTTP 422 if any step uses a disallowed action verb."""
    steps = plan.get("steps", [])
    if not isinstance(steps, list) or not steps:
        raise HTTPException(status_code=422, detail="plan.steps must be a non-empty list")
    for i, step in enumerate(steps):
        action = step.get("action", "") if isinstance(step, dict) else ""
        try:
            validate_executor_action(action)
        except DisallowedQueryError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"step[{i}] {exc}",
            ) from exc


def _apply_manifest(manifest: dict[str, Any], dry_run: bool) -> str:
    """Apply a RemediationPlan manifest to the cluster (live mode).

    The bridge is the single place that runs kubectl — the agent and
    every other component go through this endpoint.  ``dry_run`` uses
    ``kubectl apply --dry-run=client -o yaml`` which validates the
    object without persisting it.
    """
    import os

    if os.environ.get("RUN_MODE", "live").lower() == "stub":
        return f"[T3.7 STUB] Would apply RemediationPlan (dry_run={dry_run}):\n{to_yaml(manifest)}"

    yaml_text = to_yaml(manifest)
    cmd = ["kubectl", "apply", "-f", "-"]
    if dry_run:
        cmd = ["kubectl", "apply", "--dry-run=client", "-o", "yaml", "-f", "-"]

    try:
        result = subprocess.run(
            cmd,
            input=yaml_text,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail=f"kubectl apply timed out: {exc}") from exc

    if result.returncode != 0:
        raise HTTPException(
            status_code=502,
            detail=f"kubectl apply failed: {result.stderr.strip()[:1000]}",
        )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@router.post("/plans")
def create_remediation_plan(body: CreateRemediationPlanRequest) -> dict[str, Any]:
    """Validate and create a RemediationPlan object in the cluster."""
    if body.manifest is not None:
        manifest = body.manifest
    else:
        if not body.plan:
            raise HTTPException(status_code=422, detail="either plan or manifest is required")
        manifest = build_remediation_plan(
            body.plan,
            incident=body.incident,
            dry_run=body.dry_run,
            approved_by=body.approved_by,
            plan_ref=body.plan_ref,
            namespace=body.namespace,
        )

    if manifest.get("kind") != "RemediationPlan":
        raise HTTPException(status_code=422, detail="manifest.kind must be RemediationPlan")

    _validate_steps(manifest.get("spec", {}))

    output = _apply_manifest(manifest, dry_run=body.dry_run)
    name = manifest["metadata"]["name"]
    namespace = manifest["metadata"]["namespace"]
    logger.info(
        "remediation plan applied name=%s ns=%s dry_run=%s",
        name, namespace, body.dry_run,
    )

    return {
        "status": "Preview" if body.dry_run else "Created",
        "name": name,
        "namespace": namespace,
        "apiVersion": manifest["apiVersion"],
        "kind": manifest["kind"],
        "dry_run": body.dry_run,
        "output": output,
        "allowed_actions": sorted(ALLOWED_EXECUTOR_ACTIONS),
    }
