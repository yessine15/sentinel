"""Hybrid retriever for Sentinel RAG (T1.7).

Combines dense vector search and sparse BM25-style retrieval using
Qdrant's multi-vector ``query_points`` with Reciprocal Rank Fusion (RRF).

The retriever issues two prefetch queries in parallel:

1. **Dense** — cosine-similarity over the ``dense`` named vector (BGE-M3).
2. **Sparse** — dot-product over the ``sparse`` named vector (corpus-wide TF-IDF).

Results are fused via RRF and returned as a ranked list of
:class:`RetrievedPoint` objects.

Usage::

    from sentinel_rag.retrieve import retrieve

    results = retrieve("how does authentication work?")
    for r in results:
        print(f"{r.path}:{r.line_start}-{r.line_end}  score={r.score:.4f}")
        print(f"  {r.text[:120]}…")

Run standalone to smoke-test::

    python -m sentinel_rag.retrieve "what is the health endpoint?"

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

import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from qdrant_client.http.models.models import Fusion
from qdrant_client.models import FusionQuery, Prefetch, ScoredPoint

from sentinel_rag.embed import Embedder, get_embedder
from sentinel_rag.ingest import (
    COLLECTION_NAME,
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    _get_qdrant_client,
)
from sentinel_rag.sparse import sparse_query_vector

if TYPE_CHECKING:
    from qdrant_client import QdrantClient

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class RetrievedPoint:
    """A single result from the hybrid retriever.

    Attributes:
        chunk_id: Stable chunk identifier from ingest.
        text: The chunk content.
        path: Source file path or synthetic URI.
        line_start: 1-indexed first line of the chunk.
        line_end: 1-indexed last line (inclusive).
        source_type: One of ``"code"``, ``"markdown"``, ``"runbook"``, …
        score: RRF-fused score (higher is better, but scale depends on
            the number of prefetch results).
        metadata: Extra source-specific fields from the ingest payload.
    """

    chunk_id: str
    text: str
    path: str
    line_start: int
    line_end: int
    source_type: str
    score: float
    metadata: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Core retriever
# ---------------------------------------------------------------------------


def retrieve(
    query: str,
    embedder: Embedder | None = None,
    client: QdrantClient | None = None,
    top_k: int = 50,
    prefetch_limit: int = 100,
) -> list[RetrievedPoint]:
    """Run a hybrid (dense + sparse) query against the Qdrant collection.

    Args:
        query: Natural-language query string.
        embedder: An :class:`~sentinel_rag.embed.Embedder` instance.  If
            ``None``, one is created via :func:`~sentinel_rag.embed.get_embedder`.
        client: A :class:`QdrantClient` instance.  If ``None``, one is
            created via :func:`_get_qdrant_client`.
        top_k: Maximum number of results to return (default 50).
        prefetch_limit: How many candidates to fetch from each prefetch
            before fusion (default 100).

    Returns:
        A list of :class:`RetrievedPoint` objects, ranked by RRF score
        (best first).

    Raises:
        ValueError: If *query* is empty or whitespace-only.
        RuntimeError: If the Qdrant query fails.
    """
    if not query or not query.strip():
        raise ValueError("query must be non-empty")

    if embedder is None:
        embedder = get_embedder()
    if client is None:
        client = _get_qdrant_client()

    # 1. Dense embedding of the query
    dense_vec = embedder.embed(query)

    # 2. Sparse encoding of the query (TF-only, hash-based indices)
    sparse_vec = sparse_query_vector(query)

    # 3. Hybrid query with RRF fusion
    try:
        results: list[ScoredPoint] = client.query_points(
            collection_name=COLLECTION_NAME,
            prefetch=[
                Prefetch(
                    query=dense_vec,
                    using=DENSE_VECTOR_NAME,
                    limit=prefetch_limit,
                ),
                Prefetch(
                    query=sparse_vec,
                    using=SPARSE_VECTOR_NAME,
                    limit=prefetch_limit,
                ),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=top_k,
            with_payload=True,
            with_vectors=False,
        ).points
    except Exception as exc:
        raise RuntimeError(f"Qdrant hybrid query failed: {exc}") from exc

    # 4. Map ScoredPoint → RetrievedPoint
    return [
        RetrievedPoint(
            chunk_id=str(p.id),
            text=str(p.payload.get("text", "") if p.payload else ""),
            path=str(p.payload.get("path", "") if p.payload else ""),
            line_start=int(p.payload.get("line_start", 0) if p.payload else 0),
            line_end=int(p.payload.get("line_end", 0) if p.payload else 0),
            source_type=str(p.payload.get("source_type", "") if p.payload else ""),
            score=p.score,
            metadata={
                k: str(v)
                for k, v in (p.payload or {}).items()
                if k
                not in {"text", "path", "line_start", "line_end", "source_type", "parent_doc_id"}
            },
        )
        for p in results
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _format_results(results: list[RetrievedPoint]) -> None:
    """Pretty-print retrieval results to stdout."""
    if not results:
        print("No results found.", file=sys.stderr)
        return

    for i, r in enumerate(results, 1):
        print(f"#{i}  [{r.source_type}] {r.path}:{r.line_start}-{r.line_end}  score={r.score:.4f}")
        # Truncate long snippets for readability
        text = r.text.replace("\n", " ↵ ")[:200]
        suffix = "…" if len(r.text) > 200 else ""
        print(f"    {text}{suffix}")
        if r.metadata:
            meta_str = "  ".join(f"{k}={v}" for k, v in sorted(r.metadata.items()))
            print(f"    [{meta_str}]")
        print()


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m sentinel_rag.retrieve``.

    Usage::

        python -m sentinel_rag.retrieve "your search query here"
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Hybrid (dense + sparse) retrieval from the Sentinel knowledge base.",
    )
    parser.add_argument(
        "query",
        nargs="+",
        help="Natural-language query string.",
    )
    parser.add_argument(
        "-k",
        "--top-k",
        type=int,
        default=50,
        help="Number of results to return (default: 50).",
    )
    args = parser.parse_args(argv)
    query = " ".join(args.query)

    try:
        results = retrieve(query, top_k=args.top_k)
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    _format_results(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
