"""Chunkers package for Sentinel RAG.

Each module in this package takes :class:`~sentinel_rag.sources.base.Document`
objects produced by the source connectors and splits them into finer-grained
:class:`Chunk` objects suitable for embedding and retrieval.

Available chunkers:
    - :mod:`sentinel_rag.chunkers.prose` — sentence-aware sliding window
    - :mod:`sentinel_rag.chunkers.code`  — AST-aware via tree-sitter
"""

from __future__ import annotations

from sentinel_rag.chunkers.base import Chunk, Chunker, print_chunks
from sentinel_rag.chunkers.code import CodeChunker
from sentinel_rag.chunkers.prose import ProseChunker

__all__ = [
    "Chunk",
    "Chunker",
    "CodeChunker",
    "ProseChunker",
    "print_chunks",
]
