"""Shared sparse vector utilities for Sentinel RAG.

Provides deterministic token-to-index hashing so that the ingest pipeline
(T1.6) and the hybrid retriever (T1.7) produce compatible sparse vectors.

Usage::

    from sentinel_rag.sparse import tokenize, token_hash, sparse_query_vector

    # Tokenize a query
    tokens = tokenize("How does auth work?")

    # Hash a token to a stable sparse index
    idx = token_hash("auth")

    # Build a query sparse vector (TF-only, no IDF)
    sv = sparse_query_vector("How does auth work?")
    # → {"indices": [hash("how"), hash("does"), ...], "values": [0.25, ...]}
"""

from __future__ import annotations

import re
import zlib
from collections import Counter

# ---------------------------------------------------------------------------
# Tokenization — shared between ingest and retrieve
# ---------------------------------------------------------------------------


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokenization.

    Must stay exactly in sync between ingest and retrieval — any change
    here breaks sparse vector compatibility.
    """
    return re.findall(r"[a-z0-9]+", text.lower())


# ---------------------------------------------------------------------------
# Deterministic token → sparse-index hashing
# ---------------------------------------------------------------------------


def token_hash(token: str) -> int:
    """Return a stable 31-bit integer hash for *token*.

    Uses CRC32 for speed and cross-process determinism.  The 31-bit mask
    keeps indices positive (Qdrant is fine with large ints but some
    clients prefer non-negative).
    """
    return zlib.crc32(token.encode()) & 0x7FFFFFFF


# ---------------------------------------------------------------------------
# Query-time sparse encoding
# ---------------------------------------------------------------------------


def sparse_query_vector(text: str) -> dict[str, list[float]]:
    """Build a sparse query vector from *text*.

    Encodes the query with term-frequency (TF) weights and deterministic
    hash-based indices.  No IDF weighting is applied — the stored vectors
    already carry corpus IDF weights, and Qdrant's sparse dot-product
    handles the matching.

    Returns:
        A dict with ``"indices"`` (``list[int]``) and ``"values"``
        (``list[float]``), ready to pass as a sparse ``Prefetch.query``.
    """
    tokens = tokenize(text)
    if not tokens:
        return {"indices": [], "values": []}

    tf: Counter[str] = Counter(tokens)
    total = len(tokens)
    indices: list[int] = []
    values: list[float] = []

    for token, count in tf.items():
        indices.append(token_hash(token))
        values.append(count / total)

    return {"indices": indices, "values": values}
