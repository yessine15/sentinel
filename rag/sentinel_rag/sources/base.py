"""Core types for Sentinel RAG source connectors.

A *source connector* (a.k.a. loader) reads content from one kind of source
(files on disk, a database table, …) and turns it into a list of
``Document`` objects. Each ``Document`` is the unit that later stages of the
pipeline (chunker → embedder → Qdrant) operate on.

For T1.3 the connectors produce *coarse* documents — typically one document
per file or per database row. Task T1.4 (chunkers) is responsible for
splitting these into finer, AST-aware chunks with exact line ranges.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar


@dataclass(frozen=True)
class Document:
    """A single unit of content loaded from a source.

    Attributes:
        doc_id: Stable, unique identifier. Two loads of the same source must
            produce the same ``doc_id`` so re-ingestion updates rather than
            duplicates. Example: ``"md:docs/phase0.md"``.
        source_type: The connector that produced this document. One of
            ``"markdown"``, ``"runbook"``, ``"code"``, ``"postgres_incident"``.
        path: Human-readable location. For files this is a repo-relative path;
            for database rows it is a synthetic URI like
            ``"postgres://postgres/incidents/42"``.
        line_start: 1-indexed first line (inclusive). ``0`` means "not
            applicable" (e.g. a database row).
        line_end: 1-indexed last line (inclusive). ``0`` means "not
            applicable".
        text: The raw content of this document.
        metadata: Extra source-specific fields (language, incident_id,
            title, …). All values are stored as strings for uniformity.
    """

    doc_id: str
    source_type: str
    path: str
    line_start: int
    line_end: int
    text: str
    metadata: dict[str, str] = field(default_factory=dict)


class SourceConnector(ABC):
    """Abstract base for all source connectors.

    Subclasses set the ``source_type`` class variable and implement
    :meth:`load`.
    """

    source_type: ClassVar[str]

    @abstractmethod
    def load(self) -> list[Document]:
        """Load and return all documents from this source."""
        raise NotImplementedError


def print_documents(docs: list[Document]) -> None:
    """Pretty-print a list of documents (used by the standalone CLIs)."""
    for d in docs:
        rng = f"{d.line_start}-{d.line_end}" if d.line_start or d.line_end else "n/a"
        print(f"--- {d.doc_id} [{d.source_type}] {d.path}:{rng}")
        for k, v in d.metadata.items():
            print(f"  {k}: {v}")
        preview = d.text[:200].replace("\n", " ↵ ")
        suffix = "…" if len(d.text) > 200 else ""
        print(f"  text: {preview}{suffix}")
        print()
    print(f"Total: {len(docs)} document(s).")
