"""Remediation plan API — human-in-the-loop approval (T3.6).

Exposes the plan lifecycle so a user (or the chat UI) can review a
pending remediation plan and approve / reject it.  Approving or
rejecting a plan *unblocks the graph*: the decision is fed back
through the ``approval`` node via :func:`resume_plan_graph`, and the
endpoint returns the resulting ``approval_status``.

Endpoints
---------
====================  ===============================================
``POST /plans``        Create a pending plan (used by the chat flow).
``GET  /plans``        List plans (``?status=pending`` to filter).
``GET  /plans/{id}``   Fetch one plan.
``POST /plans/{id}/approve``  Approve → resume graph → approved.
``POST /plans/{id}/reject``   Reject  → resume graph → rejected.
====================  ===============================================
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from sentinel_api.plans import get_plan, list_plans, set_plan_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plans", tags=["plans"])


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------
class CreatePlanRequest(BaseModel):
    """Body for ``POST /plans``."""

    incident: str = Field(..., description="Raw incident/alert text.", min_length=1)
    plan: dict[str, Any] = Field(..., description="Structured plan {priority, rationale, steps}.")
    synthesis: str = Field(default="", description="Merged specialist assessment.")


def _plan_response(plan) -> dict[str, Any]:
    """Serialize a Plan for API responses."""
    return plan.to_dict()


def _resume_after_decision(plan_id: str, decision: str) -> dict[str, Any]:
    """Apply a decision and unblock the graph.

    Persists the status transition, then re-runs the graph's
    ``approval`` node with the decision so the graph provably continues
    past the human gate (``approval_status`` becomes ``approved`` /
    ``rejected`` instead of ``awaiting_approval``).
    """
    plan = set_plan_status(plan_id, decision)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found")

    try:
        # T3.7: the detailed resume also runs the Executor Agent after an
        # approval, so the response reports the RemediationPlan object.
        from sentinel_agents.graph import resume_plan_graph_detailed

        outcome = resume_plan_graph_detailed(plan.to_dict(), decision)
        approval_status = outcome["approval_status"]
        executor_status = outcome.get("executor_status", "")
        remediation_plan = outcome.get("remediation_plan", {})
    except Exception as exc:  # pragma: no cover - agent graph import edge case
        logger.warning("resume_plan_graph failed for %s: %s", plan_id, exc)
        approval_status = decision  # DB state is authoritative anyway
        executor_status = ""
        remediation_plan = {}

    return {
        **_plan_response(plan),
        "approval_status": approval_status,
        "executor_status": executor_status,
        "remediation_plan": remediation_plan,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("")
def create_plan(body: CreatePlanRequest) -> dict[str, Any]:
    """Persist a new pending remediation plan (status ``pending``)."""
    from sentinel_api.plans import create_plan as store_create

    plan = store_create(
        incident=body.incident,
        plan=body.plan,
        synthesis=body.synthesis,
    )
    logger.info("plan created id=%s", plan.id)
    return _plan_response(plan)


@router.get("")
def list_plans_endpoint(status: str | None = None) -> dict[str, Any]:
    """List plans, newest first.  ``?status=pending|approved|rejected``."""
    if status is not None and status not in ("pending", "approved", "rejected"):
        raise HTTPException(status_code=422, detail="status must be pending|approved|rejected")
    plans = list_plans(status)
    return {"plans": [_plan_response(p) for p in plans]}


@router.get("/{plan_id}")
def get_plan_endpoint(plan_id: str) -> dict[str, Any]:
    """Fetch a single plan by id."""
    plan = get_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found")
    return _plan_response(plan)


@router.post("/{plan_id}/approve")
def approve_plan(plan_id: str) -> dict[str, Any]:
    """Approve a plan — unblocks the graph (approval_status → approved)."""
    return _resume_after_decision(plan_id, "approved")


@router.post("/{plan_id}/reject")
def reject_plan(plan_id: str) -> dict[str, Any]:
    """Reject a plan — unblocks the graph (approval_status → rejected)."""
    return _resume_after_decision(plan_id, "rejected")
