"""RemediationPlan spec builder (T3.7) — the auditable contract.

The Executor Agent is the ONLY agent that can act.  It never runs
kubectl itself — instead it emits a **RemediationPlan** object: a
declarative spec that describes one healing action (or a small sequence
of them) with a strict, allow-listed action vocabulary.  T3.8+ builds a
Go operator that watches these objects and applies them; until then,
the operator bridge (``routes/operator.py``) applies them to the
cluster.

The spec shape mirrors the future CRD (``sentinel.io/v1``,
``kind: RemediationPlan``) so the objects we create today are exactly
what the operator will reconcile tomorrow.

Example manifest::

    apiVersion: sentinel.io/v1
    kind: RemediationPlan
    metadata:
      name: rp-demo-api-oom-7f3a
      namespace: sentinel
      labels:
        app.kubernetes.io/part-of: sentinel
        sentinel.io/incident: "kube_pod_oom"
    spec:
      incident: "ALERTS: [1] kube_pod_oom severity=critical ..."
      priority: high
      rationale: "pod OOMKilled 12 times in 10m"
      dryRun: false
      approvedBy: "human"
      planRef: "865d4e57-6ab3-4870-95aa-f4af3d3072b2"
      steps:
        - action: patch
          target: deployment/demo-api
          detail: "Raise memory limit 1Gi → 2Gi"
"""

from __future__ import annotations

import re
import uuid
from typing import Any

# API group/version of the RemediationPlan CRD (T3.8 defines it in Go).
API_GROUP = "sentinel.io"
API_VERSION = "v1"
KIND = "RemediationPlan"

# Max length for generated object names (K8s names are ≤253 chars).
_NAME_MAX = 253


def _slugify(text: str, max_len: int = 40) -> str:
    """Turn arbitrary incident text into a safe name fragment."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-")


def build_remediation_plan(
    plan: dict[str, Any],
    *,
    incident: str = "",
    dry_run: bool = False,
    approved_by: str = "human",
    plan_ref: str = "",
    namespace: str = "sentinel",
    name: str | None = None,
) -> dict[str, Any]:
    """Build a RemediationPlan manifest dict from an approved plan.

    Args:
        plan: The structured plan ``{priority, rationale, steps[]}``
            produced by the planner (and approved by a human).
        incident: Raw incident/alert text (for traceability).
        dry_run: ``True`` → the object is a preview that must NOT be
            acted on by the operator; ``False`` → a real action plan.
        approved_by: Who approved the plan (``"human"`` for now).
        plan_ref: UUID of the persisted approval plan (from /plans).
        namespace: Target namespace (default ``sentinel``).
        name: Explicit object name (generated if omitted).

    Returns:
        A full Kubernetes-style manifest dict ready to be serialised
        to YAML and applied.
    """
    steps = plan.get("steps", [])
    priority = str(plan.get("priority", "medium"))
    rationale = str(plan.get("rationale", ""))

    if not name:
        target = ""
        if steps:
            target = _slugify(str(steps[0].get("target", "")))
        name = f"rp-{target or 'plan'}-{uuid.uuid4().hex[:8]}"
    name = name[:_NAME_MAX]

    return {
        "apiVersion": f"{API_GROUP}/{API_VERSION}",
        "kind": KIND,
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/part-of": "sentinel",
            },
        },
        "spec": {
            "incident": incident,
            "priority": priority,
            "rationale": rationale,
            "dryRun": bool(dry_run),
            "approvedBy": approved_by,
            "planRef": plan_ref,
            "steps": steps,
        },
    }


def to_yaml(manifest: dict[str, Any]) -> str:
    """Serialize a manifest dict to YAML (stdlib-only, no PyYAML dep).

    This is intentionally minimal — it covers the nested structure of a
    RemediationPlan (dicts, lists, scalars).  Strings that would
    otherwise be mis-parsed by YAML (numeric look-alikes, colons,
    brackets, hashes, quotes, leading/trailing spaces) are double-
    quoted.
    """
    lines: list[str] = []

    def _scalar(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        s = str(value)
        needs_quote = (
            s == ""
            or re.fullmatch(r"-?\d+(\.\d+)?", s)
            or re.search(r"[:#\[\]{},&*!|>'\"%@`]", s)
            or s != s.strip()
            or s[:1] in "-?."
        )
        if needs_quote:
            return f'"{s.replace(chr(34), chr(92) + chr(34))}"'
        return s

    def _emit(obj: Any, indent: int = 0) -> None:
        pad = "  " * indent
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, (dict, list)):
                    lines.append(f"{pad}{key}:")
                    _emit(value, indent + 1)
                else:
                    lines.append(f"{pad}{key}: {_scalar(value)}")
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, (dict, list)):
                    lines.append(f"{pad}-")
                    _emit(item, indent + 1)
                else:
                    lines.append(f"{pad}- {_scalar(item)}")

    _emit(manifest)
    return "\n".join(lines)
