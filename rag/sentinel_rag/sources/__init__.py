"""Source connectors package for Sentinel RAG.

Each module in this package is a *loader* that reads content from one kind of
source and turns it into ``Document`` objects (see ``base.py``).

Available connectors:
    - :mod:`sentinel_rag.sources.markdown`          — ``.md`` files
    - :mod:`sentinel_rag.sources.runbook`           — runbook markdown under ``docs/runbooks``
    - :mod:`sentinel_rag.sources.code`              — source-code files (Python, Go, TS, YAML, HCL)
    - :mod:`sentinel_rag.sources.postgres_incident` — rows from the ``incidents`` table
"""

from __future__ import annotations

from sentinel_rag.sources.base import Document, SourceConnector, print_documents

__all__ = [
    "Document",
    "SourceConnector",
    "print_documents",
]
