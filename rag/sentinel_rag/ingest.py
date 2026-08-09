"""Ingest CLI for Sentinel RAG (T1.6).

One command to ingest a source into Qdrant with dense + sparse vectors.
Wires together T1.3 (source connectors), T1.4 (chunkers), and T1.5
(embedding service) into a single pipeline:

    Source → Documents → Chunks → Embeddings → Qdrant

Usage::

    python -m sentinel_rag.ingest code ./api ./rag
    python -m sentinel_rag.ingest markdown ./docs
    python -m sentinel_rag.ingest runbook ./docs/runbooks

Environment variables
---------------------

====================  =======  ================================================
Variable              Default  Description
====================  =======  ================================================
``QDRANT_URL``        http://  Qdrant REST API URL.
                      localhost
                      :6333
``QDRANT_API_KEY``    (none)   API key for Qdrant Cloud (optional).
``EMBED_PROVIDER``    ollama   ``"ollama"`` or ``"openai"`` (forwarded to the
                               embedder factory).
====================  =======  ================================================

All embedder environment variables (``OLLAMA_BASE_URL``, ``OLLAMA_MODEL``,
``OPENAI_API_KEY``, …) are also honoured — see :mod:`sentinel_rag.embed`.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from math import log
from typing import TYPE_CHECKING, Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    SparseVectorParams,
    VectorParams,
)

from sentinel_rag.chunkers.code import CodeChunker
from sentinel_rag.chunkers.prose import ProseChunker
from sentinel_rag.embed import get_embedder
from sentinel_rag.sources.code import CodeConnector
from sentinel_rag.sources.markdown import MarkdownConnector
from sentinel_rag.sources.runbook import RunbookConnector
from sentinel_rag.sparse import token_hash

if TYPE_CHECKING:
    from sentinel_rag.chunkers.base import Chunk, Chunker
    from sentinel_rag.embed import Embedder
    from sentinel_rag.sources.base import Document

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COLLECTION_NAME = "sentinel_kb"
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"


# ======================================================================
# Qdrant helpers
# ======================================================================


def _get_qdrant_client() -> QdrantClient:
    """Build a :class:`QdrantClient` from environment variables."""
    url = os.getenv(
        "QDRANT_URL",
        # In-cluster: k8s service DNS.  Override for local dev.
        "http://qdrant.qdrant.svc:6333",
    )
    api_key = os.getenv("QDRANT_API_KEY")
    if api_key:
        return QdrantClient(url=url, api_key=api_key)
    return QdrantClient(url=url)


def _ensure_collection(client: QdrantClient, dense_dim: int) -> None:
    """Create (or recreate) the ``sentinel_kb`` collection.

    The collection stores two named vectors per point:

    * ``dense``  — ``dense_dim``-dimension float vector (COSINE distance).
    * ``sparse`` — variable-length sparse vector for BM25 retrieval.

    If the collection already exists it is deleted and recreated so every
    ingestion starts from a clean slate.
    """
    if client.collection_exists(COLLECTION_NAME):
        client.delete_collection(COLLECTION_NAME)

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config={
            DENSE_VECTOR_NAME: VectorParams(
                size=dense_dim,
                distance=Distance.COSINE,
            )
        },
        sparse_vectors_config={
            SPARSE_VECTOR_NAME: SparseVectorParams(),
        },
    )


def _ensure_collection_if_missing(client: QdrantClient, dense_dim: int) -> None:
    """Create the ``sentinel_kb`` collection ONLY if it does not exist.

    Used by incremental ingestion (T3.12 postmortems): unlike
    :func:`_ensure_collection` this does NOT wipe existing points, so a
    single postmortem can be added on top of the full KB without
    re-running the whole pipeline.
    """
    if client.collection_exists(COLLECTION_NAME):
        return

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config={
            DENSE_VECTOR_NAME: VectorParams(
                size=dense_dim,
                distance=Distance.COSINE,
            )
        },
        sparse_vectors_config={
            SPARSE_VECTOR_NAME: SparseVectorParams(),
        },
    )


# ======================================================================
# Sparse encoder — corpus-wide TF-IDF
# ======================================================================


class SparseEncoder:
    """Builds vocabulary-wide TF-IDF sparse vectors for chunks.

    The encoder is **fit on the full corpus** before individual chunks are
    encoded, so IDF weights reflect true document frequencies.  This gives
    the hybrid retriever (T1.7) meaningful BM25-style sparse scores.

    Usage::

        enc = SparseEncoder()
        enc.fit([chunk1.text, chunk2.text, ...])
        sparse_vec = enc.encode(chunk1.text)
        # → {"indices": [0, 5, 12], "values": [0.42, 0.18, 0.31]}
    """

    def __init__(self) -> None:
        self._vocab: dict[str, int] = {}  # token → index
        self._df: Counter[str] = Counter()  # document frequency
        self._N: int = 0  # total document count

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def fit(self, texts: list[str]) -> None:
        """Build vocabulary and document frequencies from *texts*."""
        for text in texts:
            tokens = set(self._tokenize(text))
            for token in tokens:
                if token not in self._vocab:
                    self._vocab[token] = len(self._vocab)
                self._df[token] += 1
            self._N += 1

    def encode(self, text: str) -> dict[str, list[float]]:
        """Encode *text* as a Qdrant-compatible sparse vector.

        Uses deterministic hash-based indices (CRC32 → 31-bit) so that
        the retriever (T1.7) can produce matching sparse query vectors
        without access to the ingest-time vocabulary.

        Returns:
            Dict with ``"indices"`` (``list[int]``) and ``"values"``
            (``list[float]``) keys, suitable for the Qdrant REST API.
        """
        tokens = self._tokenize(text)
        if not tokens:
            return {"indices": [], "values": []}

        tf: Counter[str] = Counter(tokens)
        indices: list[int] = []
        values: list[float] = []

        for token, count in tf.items():
            # Skip tokens not seen in the training corpus (no IDF weight).
            if token not in self._vocab:
                continue
            # Smooth IDF: log((N+1) / (df+1)) + 1  (prevents zero and negative).
            tf_norm = count / len(tokens)
            idf = log((self._N + 1) / (self._df[token] + 1)) + 1.0
            indices.append(token_hash(token))
            values.append(tf_norm * idf)

        return {"indices": indices, "values": values}

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Lowercase alphanumeric tokenization (same logic at query time)."""
        return re.findall(r"[a-z0-9]+", text.lower())


# ======================================================================
# Pipeline
# ======================================================================


def _build_points(
    chunks: list[Chunk],
    vectors: list[list[float]],
    sparse_encoder: SparseEncoder,
) -> list[PointStruct]:
    """Assemble Qdrant :class:`PointStruct` objects from chunks and embeddings."""
    import uuid

    points: list[PointStruct] = []
    for chunk, vec in zip(chunks, vectors, strict=True):
        sparse = sparse_encoder.encode(chunk.text)
        point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk.chunk_id))
        points.append(
            PointStruct(
                id=point_id,
                vector={
                    DENSE_VECTOR_NAME: vec,
                    SPARSE_VECTOR_NAME: sparse,
                },
                payload={
                    "text": chunk.text,
                    "path": chunk.path,
                    "line_start": chunk.line_start,
                    "line_end": chunk.line_end,
                    "source_type": chunk.source_type,
                    "parent_doc_id": chunk.parent_doc_id,
                    "chunk_id": chunk.chunk_id,
                    **chunk.metadata,
                },
            )
        )
    return points


def ingest(
    docs: list[Document],
    chunker: Chunker,
    embedder: Embedder,
    client: QdrantClient,
) -> int:
    """Run the full ingest pipeline and return the number of points stored.

    Pipeline steps:

    1. Chunk every document (via *chunker*).
    2. Fit a sparse vocabulary on all chunk texts.
    3. Embed all chunks in a single batch (via *embedder*).
    4. Upsert points into the Qdrant *client*.
    """
    # Step 1 — chunk
    all_chunks: list[Chunk] = []
    for doc in docs:
        all_chunks.extend(chunker.chunk(doc))

    if not all_chunks:
        print("No chunks produced — nothing to ingest.", file=sys.stderr)
        return 0

    # Step 2 — sparse vocabulary
    sparse_encoder = SparseEncoder()
    sparse_encoder.fit([c.text for c in all_chunks])

    # Step 3 — embed
    texts = [c.text for c in all_chunks]
    vectors = embedder.embed_batch(texts)

    # Step 4 — upsert
    points = _build_points(all_chunks, vectors, sparse_encoder)
    client.upsert(collection_name=COLLECTION_NAME, points=points)

    print(f"Ingested {len(points)} chunk(s) from {len(docs)} document(s) into '{COLLECTION_NAME}'.")
    return len(points)


# ======================================================================
# Subcommand handlers
# ======================================================================


def _run_source(
    connector: Any,
    chunker: Chunker,
    paths: list[str],
) -> int:
    """Shared helper for source subcommands."""
    embedder = get_embedder()
    client = _get_qdrant_client()
    _ensure_collection(client, embedder.dimension)
    docs = connector.load()
    return ingest(docs, chunker, embedder, client)


def ingest_code(paths: list[str]) -> int:
    """Ingest source-code files from one or more root directories."""
    connector = CodeConnector(*paths)
    return _run_source(connector, CodeChunker(), paths)


def ingest_markdown(paths: list[str]) -> int:
    """Ingest markdown files from a root directory."""
    if len(paths) != 1:
        print("markdown requires exactly one root directory.", file=sys.stderr)
        return 1
    connector = MarkdownConnector(paths[0])
    return _run_source(connector, ProseChunker(), paths)


def ingest_runbook(paths: list[str]) -> int:
    """Ingest runbook markdown files from a root directory."""
    if len(paths) != 1:
        print("runbook requires exactly one root directory.", file=sys.stderr)
        return 1
    connector = RunbookConnector(paths[0])
    return _run_source(connector, ProseChunker(), paths)


# ======================================================================
# Incremental ingestion — postmortems (T3.12)
# ======================================================================


def ingest_postmortem(
    title: str,
    content: str,
    plan_id: str = "",
    *,
    chunker: Chunker | None = None,
    embedder: Embedder | None = None,
    client: QdrantClient | None = None,
) -> int:
    """Ingest a single postmortem writeup into the knowledge base.

    The Postmortem Agent (T3.12) calls this after an incident has been
    resolved: the writeup is chunked, embedded and upserted so a later
    ``/ask`` about that incident retrieves it.

    Unlike the CLI subcommands this does NOT wipe the collection — the
    collection is created only if missing, then only the postmortem
    chunks are upserted (incremental ingestion on top of the KB).

    Args:
        title: Postmortem title (stored in the point payload).
        content: The full markdown postmortem text.
        plan_id: The remediation plan id the postmortem belongs to
            (used for the stable doc id + path).

    Returns:
        The number of chunks stored in Qdrant.
    """
    from sentinel_rag.sources.base import Document

    safe_id = plan_id or "unknown"
    doc = Document(
        doc_id=f"postmortem:{safe_id}",
        source_type="postmortem",
        path=f"postmortems/{safe_id}.md",
        line_start=0,
        line_end=0,
        text=content,
        metadata={
            "title": title,
            "plan_id": safe_id,
            "kind": "postmortem",
        },
    )
    chunker = chunker or ProseChunker()
    embedder = embedder or get_embedder()
    client = client or _get_qdrant_client()
    _ensure_collection_if_missing(client, embedder.dimension)
    return ingest([doc], chunker, embedder, client)


# ======================================================================
# CLI entry point
# ======================================================================


def main(argv: list[str] | None = None) -> int:
    """Parse *argv* and dispatch to the right subcommand handler."""
    parser = argparse.ArgumentParser(
        prog="python -m sentinel_rag.ingest",
        description="Ingest sources into the Sentinel Qdrant knowledge base.",
    )
    sub = parser.add_subparsers(dest="source_type", required=True)

    p_code = sub.add_parser("code", help="Ingest source code files.")
    p_code.add_argument("paths", nargs="+", help="Root directories to scan.")

    p_md = sub.add_parser("markdown", help="Ingest markdown documentation.")
    p_md.add_argument("paths", nargs="+", help="Root directory containing .md files.")

    p_rb = sub.add_parser("runbook", help="Ingest runbooks.")
    p_rb.add_argument("paths", nargs="+", help="Root directory containing runbook .md files.")

    args = parser.parse_args(argv)

    handlers: dict[str, Any] = {
        "code": ingest_code,
        "markdown": ingest_markdown,
        "runbook": ingest_runbook,
    }
    handler = handlers[args.source_type]
    return handler(args.paths)


if __name__ == "__main__":
    sys.exit(main())
