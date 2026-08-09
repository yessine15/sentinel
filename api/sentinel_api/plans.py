"""Remediation plan store — Postgres-backed with in-memory fallback (T3.6).

The human-in-the-loop approval flow persists each pending remediation
plan so a user can review and approve/reject it later via the API
(``/plans/{id}/approve``, ``/plans/{id}/reject``) or the chat UI.

Two backends behind a single factory:

1. :class:`PostgresPlanStore` — real persistence in the Sentinel
   Postgres database (deployed in T1.2).  Used when the ``psycopg``
   driver is importable and the database is reachable.
2. :class:`MemoryPlanStore` — deterministic in-memory fallback for
   unit tests / demo runs without a database (mirrors the connector
   fallback pattern from ``sentinel_rag.sources.postgres_incident``).

Schema::

    CREATE TABLE plans (
        id          UUID PRIMARY KEY,
        incident    TEXT NOT NULL,
        synthesis   TEXT NOT NULL DEFAULT '',
        plan        JSONB NOT NULL,
        status      TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected')),
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        decided_at  TIMESTAMPTZ
    );

Environment variables
---------------------
``DATABASE_URL`` : str
    Postgres DSN (default ``postgresql://sentinel:sentinel@localhost:5432/sentinel``).
``RUN_MODE`` : str
    ``"stub"`` forces the in-memory store (safe for tests).
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

DEFAULT_DSN = "postgresql://sentinel:sentinel@localhost:5432/sentinel"

# Allowed lifecycle states — matches the graph's approval_status values.
PLAN_STATUSES = frozenset({"pending", "approved", "rejected"})


def _coerce_plan(raw: Any) -> dict[str, Any]:
    """Normalise a JSONB cell into a dict.

    psycopg3 auto-deserialises ``jsonb`` columns to Python dicts;
    older drivers / text columns return a JSON string.  Handle both.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        import json as _json

        return _json.loads(raw)
    return {}

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS plans (
    id          UUID PRIMARY KEY,
    incident    TEXT NOT NULL,
    synthesis   TEXT NOT NULL DEFAULT '',
    plan        JSONB NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at  TIMESTAMPTZ
)
"""


# ─────────────────────────────────────────────────────────────
# Plan model
# ─────────────────────────────────────────────────────────────
@dataclass
class Plan:
    """A persisted remediation plan awaiting (or having received) approval.

    Attributes:
        id: UUID string identifying the plan.
        incident: Raw incident/alert text the plan addresses.
        synthesis: Merged specialist assessment (from the synthesis node).
        plan: The structured plan dict ``{priority, rationale, steps[]}``.
        status: ``pending`` / ``approved`` / ``rejected``.
        created_at: Epoch seconds when the plan was persisted.
        decided_at: Epoch seconds when a decision was recorded (or None).
    """

    id: str
    incident: str
    plan: dict[str, Any]
    synthesis: str = ""
    status: str = "pending"
    created_at: float = field(default_factory=time.time)
    decided_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API responses."""
        return {
            "id": self.id,
            "incident": self.incident,
            "synthesis": self.synthesis,
            "plan": self.plan,
            "status": self.status,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
        }


# ─────────────────────────────────────────────────────────────
# In-memory store (stub / fallback)
# ─────────────────────────────────────────────────────────────
class MemoryPlanStore:
    """Thread-safe-ish in-memory plan store (deterministic, test-friendly)."""

    def __init__(self) -> None:
        self._plans: dict[str, Plan] = {}

    def create_plan(
        self,
        incident: str,
        plan: dict[str, Any],
        synthesis: str = "",
    ) -> Plan:
        p = Plan(id=str(uuid.uuid4()), incident=incident, plan=plan, synthesis=synthesis)
        self._plans[p.id] = p
        return p

    def get_plan(self, plan_id: str) -> Plan | None:
        return self._plans.get(plan_id)

    def list_plans(self, status: str | None = None) -> list[Plan]:
        plans = list(self._plans.values())
        if status:
            plans = [p for p in plans if p.status == status]
        return sorted(plans, key=lambda p: p.created_at, reverse=True)

    def set_plan_status(self, plan_id: str, status: str) -> Plan | None:
        p = self._plans.get(plan_id)
        if p is None:
            return None
        if status not in PLAN_STATUSES:
            raise ValueError(f"invalid plan status: {status!r}")
        p.status = status
        if status != "pending":
            p.decided_at = time.time()
        return p


# ─────────────────────────────────────────────────────────────
# Postgres store (live)
# ─────────────────────────────────────────────────────────────
class PostgresPlanStore:
    """Persistent plan store on the Sentinel Postgres database.

    Uses the ``psycopg`` driver (sync).  The table is created lazily
    on first use so the module import is always cheap.
    """

    def __init__(self, dsn: str = DEFAULT_DSN) -> None:
        self.dsn = dsn
        self._initialized = False

    # -- connection helpers ------------------------------------------------
    def _connect(self):
        import psycopg  # type: ignore[import-not-found]

        # Short connect timeout: the probe-and-fallback factory must be
        # able to detect an unreachable DB quickly (psycopg has no
        # default timeout — without this, a dead port-forward hangs the
        # whole request for minutes).
        return psycopg.connect(self.dsn, connect_timeout=5)

    def _ensure_table(self) -> None:
        if self._initialized:
            return
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(_CREATE_TABLE_SQL)
        self._initialized = True

    # -- CRUD ----------------------------------------------------------------
    def create_plan(
        self,
        incident: str,
        plan: dict[str, Any],
        synthesis: str = "",
    ) -> Plan:
        import json as _json

        self._ensure_table()
        p = Plan(id=str(uuid.uuid4()), incident=incident, plan=plan, synthesis=synthesis)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO plans (id, incident, synthesis, plan, status) "
                "VALUES (%s, %s, %s, %s, %s)",
                (p.id, p.incident, p.synthesis, _json.dumps(p.plan), p.status),
            )
        return p

    def get_plan(self, plan_id: str) -> Plan | None:
        self._ensure_table()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, incident, synthesis, plan, status, "
                "EXTRACT(EPOCH FROM created_at), "
                "EXTRACT(EPOCH FROM decided_at) "
                "FROM plans WHERE id = %s",
                (plan_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return Plan(
            id=row[0],
            incident=row[1],
            synthesis=row[2],
            plan=_coerce_plan(row[3]),
            status=row[4],
            created_at=float(row[5] or 0),
            decided_at=float(row[6]) if row[6] is not None else None,
        )

    def list_plans(self, status: str | None = None) -> list[Plan]:
        self._ensure_table()
        query = (
            "SELECT id, incident, synthesis, plan, status, "
            "EXTRACT(EPOCH FROM created_at), EXTRACT(EPOCH FROM decided_at) "
            "FROM plans"
        )
        params: tuple[str, ...] = ()
        if status:
            query += " WHERE status = %s"
            params = (status,)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
        return [
            Plan(
                id=r[0],
                incident=r[1],
                synthesis=r[2],
                plan=_coerce_plan(r[3]),
                status=r[4],
                created_at=float(r[5] or 0),
                decided_at=float(r[6]) if r[6] is not None else None,
            )
            for r in rows
        ]

    def set_plan_status(self, plan_id: str, status: str) -> Plan | None:
        if status not in PLAN_STATUSES:
            raise ValueError(f"invalid plan status: {status!r}")
        self._ensure_table()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE plans SET status = %s, decided_at = now() "
                "WHERE id = %s RETURNING id",
                (status, plan_id),
            )
            updated = cur.fetchone()
        return self.get_plan(plan_id) if updated else None


# ─────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────
_store: MemoryPlanStore | PostgresPlanStore | None = None


def get_plan_store() -> MemoryPlanStore | PostgresPlanStore:
    """Return the process-wide plan store.

    Selection order:

    1. ``RUN_MODE=stub`` → :class:`MemoryPlanStore` (tests, demos).
    2. Otherwise try :class:`PostgresPlanStore`; if the driver is
       missing or the database is unreachable on first use, fall back
       to :class:`MemoryPlanStore` so the API never hard-fails.
    """
    global _store
    if _store is not None:
        return _store

    if os.environ.get("RUN_MODE", "live").lower() == "stub":
        _store = MemoryPlanStore()
        return _store

    store: MemoryPlanStore | PostgresPlanStore = PostgresPlanStore(
        os.environ.get("DATABASE_URL", DEFAULT_DSN)
    )
    # Probe: create the table / list plans; fall back to memory on any error.
    try:
        store.list_plans()
        _store = store
    except Exception:
        _store = MemoryPlanStore()
    return _store


def create_plan(incident: str, plan: dict[str, Any], synthesis: str = "") -> Plan:
    """Persist a new pending plan (convenience wrapper)."""
    return get_plan_store().create_plan(incident, plan, synthesis)


def get_plan(plan_id: str) -> Plan | None:
    """Fetch one plan by id (convenience wrapper)."""
    return get_plan_store().get_plan(plan_id)


def list_plans(status: str | None = None) -> list[Plan]:
    """List plans, newest first, optionally filtered by status."""
    return get_plan_store().list_plans(status)


def set_plan_status(plan_id: str, status: str) -> Plan | None:
    """Transition a plan to ``approved`` / ``rejected`` / ``pending``."""
    return get_plan_store().set_plan_status(plan_id, status)
