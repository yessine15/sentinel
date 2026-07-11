"""Postgres incident source connector.

Loads incident rows from the Sentinel Postgres database (deployed in T1.2)
and converts each row into one ``Document``. The ``incidents`` table is the
relational home of structured incident records; its ``summary`` / ``content``
columns become the document text, and the row metadata (id, severity,
service, status, created_at) becomes document metadata.

Schema expected (created by a later task; the connector tolerates its
absence and falls back to a bundled JSON sample)::

    CREATE TABLE incidents (
        id           SERIAL PRIMARY KEY,
        title        TEXT NOT NULL,
        summary      TEXT NOT NULL,
        severity     TEXT NOT NULL,
        service      TEXT,
        status       TEXT NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    );

DSN defaults match the T1.2 Helm values (``sentinel/sentinel`` user,
``sentinel`` db). When running outside the cluster, set ``--dsn`` to a
port-forwarded URL, e.g. ``postgresql://sentinel:sentinel@localhost:5432/sentinel``.

Fallback mode
-------------
If Postgres is unreachable *or* the ``psycopg`` driver is not installed, the
connector can load from a JSON sample file (``--sample``) so that the pipeline
is testable end-to-end without a live database. The sample file is a JSON
array of objects with the same keys as the table columns.

Run standalone (live DB):

    python -m sentinel_rag.sources.postgres_incident \
        --dsn postgresql://sentinel:sentinel@localhost:5432/sentinel

Run standalone (sample fallback):

    python -m sentinel_rag.sources.postgres_incident --sample \
        rag/sentinel_rag/sources/_sample_incidents.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sentinel_rag.sources.base import Document, SourceConnector, print_documents

DEFAULT_DSN = "postgresql://sentinel:sentinel@localhost:5432/sentinel"
DEFAULT_SAMPLE = Path(__file__).with_name("_sample_incidents.json")

# Columns we SELECT, in order. Keep ``summary`` last so it's easy to slice.
_SELECT_SQL = (
    "SELECT id, title, summary, severity, service, status, created_at FROM incidents ORDER BY id"
)


class PostgresIncidentConnector(SourceConnector):
    """Load incident rows from Postgres, with a JSON-sample fallback.

    Parameters:
        dsn:    Postgres connection string. Ignored when ``sample_path`` is
                used or when the DB is unreachable and a sample is available.
        sample_path: Path to a JSON file used when the live DB is not
                available. ``None`` disables the fallback (raises on error).
    """

    source_type = "postgres_incident"

    def __init__(
        self,
        dsn: str = DEFAULT_DSN,
        *,
        sample_path: str | Path | None = DEFAULT_SAMPLE,
    ) -> None:
        self.dsn = dsn
        self.sample_path = Path(sample_path) if sample_path else None

    def load(self) -> list[Document]:
        rows = self._load_rows()
        return [self._row_to_document(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Row loading
    # ------------------------------------------------------------------ #
    def _load_rows(self) -> list[dict[str, Any]]:
        try:
            return self._load_from_db()
        except Exception as exc:  # broad net: any DB error triggers fallback
            if self.sample_path is None:
                raise
            print(
                f"[postgres_incident] DB unavailable ({exc!r}); "
                f"falling back to sample file: {self.sample_path}",
                file=sys.stderr,
            )
            return self._load_from_sample()

    def _load_from_db(self) -> list[dict[str, Any]]:
        """Live-load rows via psycopg (lazy import — optional dependency)."""
        try:
            import psycopg  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "psycopg is not installed; run `uv sync --extra db` or use --sample"
            ) from exc

        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(_SELECT_SQL)
            cols = [c.name for c in cur.description]
            return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]

    def _load_from_sample(self) -> list[dict[str, Any]]:
        if self.sample_path is None or not self.sample_path.exists():
            return []
        data = json.loads(self.sample_path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError(f"sample file {self.sample_path} must be a JSON array")
        return data

    # ------------------------------------------------------------------ #
    # Row → Document
    # ------------------------------------------------------------------ #
    @staticmethod
    def _row_to_document(row: dict[str, Any]) -> Document:
        inc_id = row["id"]
        title = row.get("title", "")
        summary = row.get("summary", "")
        text = f"# {title}\n\n{summary}" if title else summary
        service = row.get("service") or "unknown"
        return Document(
            doc_id=f"incident:{inc_id}",
            source_type="postgres_incident",
            path=f"postgres://postgres/incidents/{inc_id}",
            line_start=0,
            line_end=0,
            text=text,
            metadata={
                "incident_id": str(inc_id),
                "title": str(title),
                "severity": str(row.get("severity", "")),
                "service": str(service),
                "status": str(row.get("status", "")),
                "created_at": str(row.get("created_at", "")),
            },
        )


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(description="Load Sentinel incidents from Postgres.")
    parser.add_argument("--dsn", default=DEFAULT_DSN, help="Postgres connection string")
    parser.add_argument(
        "--sample",
        type=Path,
        default=DEFAULT_SAMPLE,
        help=(
            "Path to a JSON sample file used when the DB is unreachable "
            "(default: the bundled _sample_incidents.json next to this module)"
        ),
    )
    parser.add_argument(
        "--no-sample",
        action="store_true",
        help="Disable the sample fallback; raise if the DB is unreachable",
    )
    ns = parser.parse_args(args)

    connector = PostgresIncidentConnector(
        dsn=ns.dsn,
        sample_path=None if ns.no_sample else ns.sample,
    )
    docs = connector.load()
    print_documents(docs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
