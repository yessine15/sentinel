"""Postmortem writeup builder (T3.12) — the closed-loop artifact.

The Postmortem Agent converts the full incident lifecycle into a
markdown writeup: raw alert text, merged specialist assessment, the
approved remediation plan, the executor result (RemediationPlan object)
and the operator verification state read back from the cluster.

Like the Executor Agent, the writeup is built **deterministically**
(no LLM): every input is already structured, and the writeup must be
reproducible + testable — the LLM is not needed to restate facts, and
it must never invent any.

The resulting markdown is stored in Postgres (``postmortems`` table)
and embedded into Qdrant, so a later ``/ask`` query about the incident
retrieves it from the knowledge base.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _fmt_steps(steps: list[dict[str, Any]]) -> str:
    """Render the plan's steps as a markdown table."""
    if not steps:
        return "_No remediation steps were proposed._"
    rows = ["| # | Action | Target | Detail |", "|---|--------|--------|--------|"]
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            continue
        action = str(s.get("action", ""))
        target = str(s.get("target", ""))
        detail = str(s.get("detail", "")).replace("|", "\\|").replace("\n", " ")
        rows.append(f"| {i} | `{action}` | `{target}` | {detail} |")
    return "\n".join(rows)


def build_postmortem_markdown(
    incident: str,
    synthesis: str,
    plan: dict[str, Any],
    executor_result: dict[str, Any] | None = None,
    verification: dict[str, Any] | None = None,
    plan_ref: str = "",
    *,
    now: datetime | None = None,
) -> str:
    """Build the full markdown postmortem for a resolved incident.

    Args:
        incident: Raw incident/alert text (from the triage capture).
        synthesis: Merged specialist assessment (from the synthesis node).
        plan: The approved remediation plan ``{priority, rationale, steps[]}``.
        executor_result: The executor's outcome (``{status, name, ...}``
            from the RemediationPlan creation) — may be ``None``.
        verification: The operator verification state read back from the
            cluster (``{state, message, ...}``) — may be ``None``.
        plan_ref: The persisted plan id (human-in-the-loop reference).
        now: Timestamp override (tests); defaults to the current UTC time.

    Returns:
        The complete postmortem as markdown text.
    """
    now = now or datetime.now(timezone.utc)
    priority = str(plan.get("priority", "unknown"))
    rationale = str(plan.get("rationale", ""))
    steps = plan.get("steps", []) or []

    executor = executor_result or {}
    executor_status = str(executor.get("status", "unknown"))
    rp_name = str(executor.get("name", ""))
    rp_namespace = str(executor.get("namespace", ""))

    verif = verification or {}
    verif_state = str(verif.get("state", "unknown"))
    verif_message = str(verif.get("message", ""))
    verif_at = str(verif.get("verified_at", "")) if verif.get("verified_at") else "n/a"

    title = incident.strip().splitlines()[0][:80] if incident.strip() else "Untitled incident"

    lines: list[str] = [
        f"# Postmortem — {title}",
        "",
        f"> Written automatically by the Sentinel Postmortem Agent at "
        f"{now.isoformat(timespec='seconds')}Z.",
        "",
        "## Summary",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| Incident | {incident.strip().replace(chr(10), ' ')[:200]} |",
        f"| Priority | {priority} |",
        f"| Plan ref | {plan_ref or 'n/a'} |",
        f"| RemediationPlan | {rp_name or 'n/a'} (namespace {rp_namespace or 'n/a'}) |",
        f"| Execution | {executor_status} |",
        f"| Verification | {verif_state} |",
        "",
        "## Incident",
        "",
        "```text",
        incident.strip(),
        "```",
        "",
        "## Assessment",
        "",
        synthesis.strip() or "_No specialist assessment recorded._",
        "",
        "## Remediation plan",
        "",
        f"**Rationale:** {rationale or 'n/a'}",
        "",
        _fmt_steps(steps),
        "",
        "## Execution",
        "",
        f"The approved plan was submitted to the operator as "
        f"`{rp_name or 'n/a'}` (status: `{executor_status}`).",
        "",
        "## Verification",
        "",
        f"- **State:** `{verif_state}`",
        f"- **Observed at:** {verif_at}",
    ]
    if verif_message:
        lines.append(f"- **Message:** {verif_message}")
    lines += [
        "",
        "## Lessons",
        "",
        "- The incident was detected by the Sentinel triage agent and routed "
        "through the specialist pipeline.",
        "- A human approved the remediation plan before any action was taken "
        "(human-in-the-loop gate).",
        "- The operator applied and verified the change; this writeup closes "
        "the loop by feeding the outcome back into the knowledge base.",
        "",
    ]
    return "\n".join(lines)
