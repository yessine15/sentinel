"""Prose chunker — sentence-aware sliding window.

Splits markdown, runbook, and incident text into roughly-equal chunks at
sentence / paragraph boundaries so embedding vectors capture coherent units
of meaning rather than arbitrary byte windows.

The algorithm:

1. Split the document text into **lines** and record original line numbers.
2. Split into **sentences** using a simple regex (``.?!`` followed by
   whitespace, blank lines treated as paragraph breaks).
3. Accumulate sentences greedily until the chunk size (in characters) is
   reached, then emit a chunk.  Lines belonging to each chunk are tracked
   so ``line_start`` / ``line_end`` are exact.
4. Apply a **sliding window** with configurable overlap: each new chunk
   starts *overlap* characters before the previous chunk's end, re-using
   the last few sentences of the prior chunk.

Run standalone:

    python -m sentinel_rag.chunkers.prose <dir>

Loads all ``.md`` under *dir* via the MarkdownConnector and prints the
resulting chunks.
"""

from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING

from sentinel_rag.chunkers.base import Chunk, Chunker, print_chunks
from sentinel_rag.sources.markdown import MarkdownConnector

if TYPE_CHECKING:
    from sentinel_rag.sources.base import Document


class ProseChunker(Chunker):
    """Sentence-aware sliding-window chunker for prose documents.

    Parameters:
        chunk_size: Target size of each chunk in characters (approximate).
        chunk_overlap: Number of characters of overlap between consecutive
            chunks. The overlap is realised by re-using the last few
            sentences of the previous chunk.
    """

    source_type = "prose"

    def __init__(self, chunk_size: int = 512, chunk_overlap: int = 64) -> None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be >= 1")
        if chunk_overlap < 0:
            raise ValueError("chunk_overlap must be >= 0")
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be < chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def chunk(self, document: Document) -> list[Chunk]:
        """Split *document* into prose chunks."""
        lines = document.text.splitlines()
        if not lines:
            return []

        sentences = self._split_sentences(lines)
        if not sentences:
            return []

        chunks: list[Chunk] = []
        buf: list[_Span] = []
        buf_len = 0
        chunk_idx = 0

        for sent in sentences:
            # If adding this sentence would blow past chunk_size and we
            # already have something in the buffer, emit the current chunk.
            if buf and buf_len + len(sent.text) > self.chunk_size:
                chunks.append(self._build_chunk(document, buf, chunk_idx, len(chunks)))
                chunk_idx += 1
                # Sliding overlap: keep the last few sentences whose total
                # length <= chunk_overlap.
                buf, buf_len = self._overlap_tail(buf, buf_len)
            buf.append(sent)
            buf_len += len(sent.text)

        # Emit the final chunk if anything remains.
        if buf:
            chunks.append(self._build_chunk(document, buf, chunk_idx, len(chunks)))

        return chunks

    # ------------------------------------------------------------------ #
    # Sentence splitting
    # ------------------------------------------------------------------ #
    @staticmethod
    def _split_sentences(lines: list[str]) -> list[_Span]:
        """Split *lines* into sentence-like spans.

        Each line or group of lines ending with ``.?!`` is a sentence.
        Blank lines are paragraph breaks (treated as sentence boundaries
        when they separate non-blank text).
        """
        spans: list[_Span] = []
        current_lines: list[str] = []
        current_start: int | None = None

        for i, line in enumerate(lines, start=1):
            stripped = line.rstrip()

            # Blank line → flush accumulated text as a sentence.
            if not stripped:
                if current_lines:
                    spans.append(
                        _Span(
                            text="\n".join(current_lines),
                            first_line=current_start or i - len(current_lines),
                            last_line=i - 1,
                        )
                    )
                    current_lines = []
                    current_start = None
                continue

            if current_start is None:
                current_start = i

            current_lines.append(stripped)

            # A sentence-ending punctuation at the end of a line signals a
            # natural break.
            if re.search(r"[.!?]$", stripped):
                spans.append(
                    _Span(
                        text="\n".join(current_lines),
                        first_line=current_start,
                        last_line=i,
                    )
                )
                current_lines = []
                current_start = None

        # Don't forget trailing text without a sentence end.
        if current_lines:
            spans.append(
                _Span(
                    text="\n".join(current_lines),
                    first_line=current_start or len(lines) - len(current_lines) + 1,
                    last_line=len(lines),
                )
            )

        return spans

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _build_chunk(
        self,
        document: Document,
        buf: list[_Span],
        chunk_idx: int,
        total_before: int,
    ) -> Chunk:
        """Assemble a :class:`Chunk` from a buffer of spans."""
        text = "\n\n".join(s.text for s in buf)
        first = buf[0].first_line
        last = buf[-1].last_line
        return Chunk(
            chunk_id=f"{document.doc_id}:c{chunk_idx}",
            parent_doc_id=document.doc_id,
            source_type=document.source_type,
            path=document.path,
            line_start=first,
            line_end=last,
            text=text,
            metadata={
                **document.metadata,
                "chunk_index": str(total_before),
                "char_count": str(len(text)),
            },
        )

    def _overlap_tail(self, buf: list[_Span], buf_len: int) -> tuple[list[_Span], int]:
        """Return the tail of *buf* whose total length <= chunk_overlap."""
        tail: list[_Span] = []
        tail_len = 0
        for s in reversed(buf):
            if tail_len + len(s.text) > self.chunk_overlap and tail:
                break
            tail.insert(0, s)
            tail_len += len(s.text)
        return tail, tail_len


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #
class _Span:
    """A sentence-like piece of text with original line range."""

    __slots__ = ("first_line", "last_line", "text")

    def __init__(self, text: str, first_line: int, last_line: int) -> None:
        self.text = text
        self.first_line = first_line
        self.last_line = last_line


# --------------------------------------------------------------------------- #
# Standalone CLI
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m sentinel_rag.chunkers.prose <dir> [chunk_size] [overlap]")
        sys.exit(1)

    root = sys.argv[1]
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 512
    overlap = int(sys.argv[3]) if len(sys.argv) > 3 else 64

    chunker = ProseChunker(chunk_size=size, chunk_overlap=overlap)
    docs = MarkdownConnector(root).load()
    all_chunks: list[Chunk] = []
    for d in docs:
        all_chunks.extend(chunker.chunk(d))
    print_chunks(all_chunks)
