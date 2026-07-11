"""Core types for Sentinel RAG chunkers.

A *chunker* takes a :class:`sentinel_rag.sources.base.Document` (one coarse
document per file or database row) and splits it into finer-grained
:class:`Chunk` objects suitable for embedding and retrieval.

Each ``Chunk`` records exact line ranges so citations can point to
``path:line_start-line_end`` with precision.

For T1.4 we ship two chunkers:

* :class:`ProseChunker` — sentence-aware sliding window for markdown /
  runbook / incident prose.
* :class:`CodeChunker` — AST-aware via tree-sitter that never cuts
  mid-function/class.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from sentinel_rag.sources.base import Document


@dataclass(frozen=True)
class Chunk:
    """A single embeddable piece of text produced by a chunker.

    Attributes:
        chunk_id: Stable, unique identifier. Must be reproducible across
            re-ingestion runs. Example: ``"code:main.py:5-25"``.
        parent_doc_id: The ``doc_id`` of the source :class:`Document` this
            chunk was split from.
        source_type: Matches the parent document's ``source_type``.
        path: Matches the parent document's ``path``.
        line_start: 1-indexed first line of this chunk in the parent.
        line_end: 1-indexed last line (inclusive).
        text: The chunk content.
        metadata: Inherited from parent + chunk-specific keys (``language``,
            ``chunk_index``, ``node_type``, …).
    """

    chunk_id: str
    parent_doc_id: str
    source_type: str
    path: str
    line_start: int
    line_end: int
    text: str
    metadata: dict[str, str] = field(default_factory=dict)


class Chunker(ABC):
    """Abstract base for all chunkers.

    Subclasses set the ``source_type`` class variable and implement
    :meth:`chunk`.
    """

    source_type: ClassVar[str]

    @abstractmethod
    def chunk(self, document: Document) -> list[Chunk]:
        """Split *document* into one or more :class:`Chunk` objects."""
        raise NotImplementedError


def print_chunks(chunks: list[Chunk]) -> None:
    """Pretty-print a list of chunks (used by the standalone CLIs)."""
    for c in chunks:
        print(f"--- {c.chunk_id} [{c.source_type}] {c.path}:{c.line_start}-{c.line_end}")
        for k, v in c.metadata.items():
            print(f"  {k}: {v}")
        preview = c.text[:200].replace("\n", " ↵ ")
        suffix = "…" if len(c.text) > 200 else ""
        print(f"  text: {preview}{suffix}")
        print()
    print(f"Total: {len(chunks)} chunk(s).")
