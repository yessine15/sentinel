"""Postmortem API (T3.12) — read the closed-loop writeups.

The Postmortem Agent writes postmortems after incidents are resolved;
this router exposes them so the UI / ops can browse them, and allows
re-running the KB ingestion job for a writeup whose first ingestion
attempt failed (e.g. Qdrant was down at draft time).

Endpoints
---------
============================  =========================================
``GET  /postmortems``          List postmortems (``?status=`` / ``?plan_id=``).
``GET  /postmortems/{id}``     Fetch one postmortem (full markdown body).
``POST /postmortems/{id}/ingest``  Re-run the Qdrant ingestion job.
============================  =========================================
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from sentinel_api.postmortems import (
    get_postmortem,
    get_postmortem_store,
    list_postmortems,
    set_postmortem_status,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/postmortems", tags=["postmortems"])

_VALID_STATUSES = ("drafted", "ingested", "failed")


def _postmortem_response(pm) -> dict[str, Any]:
    """Serialize a Postmortem for API responses."""
    return pm.to_dict()


@router.get("")
def list_postmortems_endpoint(
    status: str | None = None,
    plan_id: str | None = None,
) -> dict[str, Any]:
    """List postmortems, newest first.  ``?status=ingested`` to filter."""
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail="status must be drafted|ingested|failed",
        )
    items = list_postmortems(plan_id=plan_id, status=status)
    return {"postmortems": [_postmortem_response(p) for p in items]}


@router.get("/{postmortem_id}")
def get_postmortem_endpoint(postmortem_id: str) -> dict[str, Any]:
    """Fetch a single postmortem by id (includes the markdown body)."""
    pm = get_postmortem(postmortem_id)
    if pm is None:
        raise HTTPException(status_code=404, detail=f"Postmortem {postmortem_id} not found")
    return _postmortem_response(pm)


@router.post("/{postmortem_id}/ingest")
def reingest_postmortem(postmortem_id: str) -> dict[str, Any]:
    """Re-run the KB ingestion job for a postmortem.

    Used when the first ingestion attempt failed (status ``failed`` —
    e.g. Qdrant/embedder was unreachable at draft time).  Re-chunks,
    re-embeds and upserts the writeup into Qdrant, then updates the
    postmortem status to ``ingested``.
    """
    pm = get_postmortem(postmortem_id)
    if pm is None:
        raise HTTPException(status_code=404, detail=f"Postmortem {postmortem_id} not found")

    try:
        from sentinel_rag.ingest import ingest_postmortem

        chunks = ingest_postmortem(
            title=f"Postmortem — {pm.incident.strip().splitlines()[0][:80]}",
            content=pm.content,
            plan_id=pm.plan_id or pm.id,
        )
    except Exception as exc:
        logger.warning("postmortem %s re-ingest failed: %s", postmortem_id, exc)
        raise HTTPException(
            status_code=502,
            detail=f"Ingestion failed: {exc}",
        ) from exc

    set_postmortem_status(postmortem_id, "ingested")
    updated = get_postmortem(postmortem_id)
    return {
        **_postmortem_response(updated),
        "chunks": chunks,
    }
