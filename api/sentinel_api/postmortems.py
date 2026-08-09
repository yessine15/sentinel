"""Postmortem store — Postgres-backed with in-memory fallback (T3.12).

After an incident is resolved (plan approved → RemediationPlan created
→ operator applied/verified), the Postmortem Agent drafts a markdown
writeup, persists it here, and spawns an ingestion job that embeds it
into Qdrant.  A later ``/ask`` query about that incident then retrieves
the fresh postmortem from the knowledge base.

Two backends behind a single factory (mirrors ``sentinel_api.plans``):

1. :class:`PostgresPostmortemStore` — real persistence in the Sentinel
   Postgres database.  Used when the ``psycopg`` driver is importable
   and the database is reachable.
2. :class:`MemoryPostmortemStore` — deterministic in-memory fallback
   for unit tests / demo runs without a database.

Schema::

    CREATE TABLE postmortems (
        id          UUID PRIMARY KEY,
        plan_id     UUID,
        incident    TEXT NOT NULL,
        content     TEXT NOT NULL,
        status      TEXT NOT NULL DEFAULT 'drafted'
                    CHECK (status IN ('drafted', 'ingested', 'failed')),
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );

Lifecycle states:

* ``drafted``  — writeup persisted, KB ingestion not yet attempted.
* ``ingested`` — writeup embedded into Qdrant (searchable via /ask).
* ``failed``   — ingestion job errored (Qdrant/embedder unreachable);
                 ``POST /postmortems/{id}/ingest`` retries.

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

# Allowed lifecycle states — see module docstring.
POSTMORTEM_STATUSES = frozenset({"drafted", "ingested", "failed"})

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS postmortems (
    id          UUID PRIMARY KEY,
    plan_id     UUID,
    incident    TEXT NOT NULL,
    content     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'drafted'
                CHECK (status IN ('drafted', 'ingested', 'failed')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


# ─────────────────────────────────────────────────────────────
# Postmortem model
# ─────────────────────────────────────────────────────────────
@dataclass
class Postmortem:
    """A persisted incident postmortem writeup.

    Attributes:
        id: UUID string identifying the postmortem.
        plan_id: The remediation plan id this writeup belongs to ("" if
            the postmortem was created without a plan reference).
        incident: Raw incident/alert text the postmortem covers.
        content: The markdown postmortem body.
        status: ``drafted`` / ``ingested`` / ``failed``.
        created_at: Epoch seconds when the postmortem was persisted.
    """

    id: str
    incident: str
    content: str
    plan_id: str = ""
    status: str = "drafted"
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API responses."""
        return {
            "id": self.id,
            "plan_id": self.plan_id,
            "incident": self.incident,
            "content": self.content,
            "status": self.status,
            "created_at": self.created_at,
        }


# ─────────────────────────────────────────────────────────────
# In-memory store (stub / fallback)
# ─────────────────────────────────────────────────────────────
class MemoryPostmortemStore:
    """Thread-safe-ish in-memory postmortem store (deterministic)."""

    def __init__(self) -> None:
        self._postmortems: dict[str, Postmortem] = {}

    def create_postmortem(
        self,
        incident: str,
        content: str,
        plan_id: str = "",
    ) -> Postmortem:
        pm = Postmortem(
            id=str(uuid.uuid4()),
            incident=incident,
            content=content,
            plan_id=plan_id,
        )
        self._postmortems[pm.id] = pm
        return pm

    def get_postmortem(self, postmortem_id: str) -> Postmortem | None:
        return self._postmortems.get(postmortem_id)

    def list_postmortems(
        self,
        plan_id: str | None = None,
        status: str | None = None,
    ) -> list[Postmortem]:
        items = list(self._postmortems.values())
        if plan_id:
            items = [p for p in items if p.plan_id == plan_id]
        if status:
            items = [p for p in items if p.status == status]
        return sorted(items, key=lambda p: p.created_at, reverse=True)

    def set_postmortem_status(self, postmortem_id: str, status: str) -> Postmortem | None:
        pm = self._postmortems.get(postmortem_id)
        if pm is None:
            return None
        if status not in POSTMORTEM_STATUSES:
            raise ValueError(f"invalid postmortem status: {status!r}")
        pm.status = status
        return pm


# ─────────────────────────────────────────────────────────────
# Postgres store (live)
# ─────────────────────────────────────────────────────────────
class PostgresPostmortemStore:
    """Persistent postmortem store on the Sentinel Postgres database.

    Uses the ``psycopg`` driver (sync); the table is created lazily on
    first use so the module import is always cheap.
    """

    def __init__(self, dsn: str = DEFAULT_DSN) -> None:
        self.dsn = dsn
        self._initialized = False

    # -- connection helpers ------------------------------------------------
    def _connect(self):
        import psycopg  # type: ignore[import-not-found]

        # Short connect timeout: the probe-and-fallback factory must be
        # able to detect an unreachable DB quickly.
        return psycopg.connect(self.dsn, connect_timeout=5)

    def _ensure_table(self) -> None:
        if self._initialized:
            return
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(_CREATE_TABLE_SQL)
        self._initialized = True

    # -- CRUD ----------------------------------------------------------------
    def create_postmortem(
        self,
        incident: str,
        content: str,
        plan_id: str = "",
    ) -> Postmortem:
        self._ensure_table()
        pm = Postmortem(
            id=str(uuid.uuid4()),
            incident=incident,
            content=content,
            plan_id=plan_id,
        )
        plan_col = pm.plan_id if pm.plan_id else None
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO postmortems (id, plan_id, incident, content, status) "
                "VALUES (%s, %s, %s, %s, %s)",
                (pm.id, plan_col, pm.incident, pm.content, pm.status),
            )
        return pm

    def get_postmortem(self, postmortem_id: str) -> Postmortem | None:
        self._ensure_table()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, plan_id, incident, content, status, "
                "EXTRACT(EPOCH FROM created_at) "
                "FROM postmortems WHERE id = %s",
                (postmortem_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return Postmortem(
            id=row[0],
            plan_id=row[1] or "",
            incident=row[2],
            content=row[3],
            status=row[4],
            created_at=float(row[5] or 0),
        )

    def list_postmortems(
        self,
        plan_id: str | None = None,
        status: str | None = None,
    ) -> list[Postmortem]:
        self._ensure_table()
        query = (
            "SELECT id, plan_id, incident, content, status, "
            "EXTRACT(EPOCH FROM created_at) "
            "FROM postmortems"
        )
        clauses: list[str] = []
        params: list[str] = []
        if plan_id:
            clauses.append("plan_id = %s")
            params.append(plan_id)
        if status:
            clauses.append("status = %s")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(query, tuple(params))
            rows = cur.fetchall()
        return [
            Postmortem(
                id=r[0],
                plan_id=r[1] or "",
                incident=r[2],
                content=r[3],
                status=r[4],
                created_at=float(r[5] or 0),
            )
            for r in rows
        ]

    def set_postmortem_status(self, postmortem_id: str, status: str) -> Postmortem | None:
        if status not in POSTMORTEM_STATUSES:
            raise ValueError(f"invalid postmortem status: {status!r}")
        self._ensure_table()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE postmortems SET status = %s WHERE id = %s RETURNING id",
                (status, postmortem_id),
            )
            updated = cur.fetchone()
        return self.get_postmortem(postmortem_id) if updated else None


# ─────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────
_store: MemoryPostmortemStore | PostgresPostmortemStore | None = None


def get_postmortem_store() -> MemoryPostmortemStore | PostgresPostmortemStore:
    """Return the process-wide postmortem store.

    Selection order:

    1. ``RUN_MODE=stub`` → :class:`MemoryPostmortemStore`.
    2. Otherwise try :class:`PostgresPostmortemStore`; if the driver is
       missing or the database is unreachable on first use, fall back
       to the memory store so the agent never hard-fails.
    """
    global _store
    if _store is not None:
        return _store

    if os.environ.get("RUN_MODE", "live").lower() == "stub":
        _store = MemoryPostmortemStore()
        return _store

    store: MemoryPostmortemStore | PostgresPostmortemStore = PostgresPostmortemStore(
        os.environ.get("DATABASE_URL", DEFAULT_DSN)
    )
    try:
        store.list_postmortems()
        _store = store
    except Exception:
        _store = MemoryPostmortemStore()
    return _store


def create_postmortem(incident: str, content: str, plan_id: str = "") -> Postmortem:
    """Persist a new postmortem writeup (convenience wrapper)."""
    return get_postmortem_store().create_postmortem(incident, content, plan_id)


def get_postmortem(postmortem_id: str) -> Postmortem | None:
    """Fetch one postmortem by id (convenience wrapper)."""
    return get_postmortem_store().get_postmortem(postmortem_id)


def list_postmortems(
    plan_id: str | None = None,
    status: str | None = None,
) -> list[Postmortem]:
    """List postmortems, newest first, optionally filtered."""
    return get_postmortem_store().list_postmortems(plan_id, status)


def set_postmortem_status(postmortem_id: str, status: str) -> Postmortem | None:
    """Transition a postmortem to ``drafted`` / ``ingested`` / ``failed``."""
    return get_postmortem_store().set_postmortem_status(postmortem_id, status)
