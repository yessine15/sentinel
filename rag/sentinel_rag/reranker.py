"""Cross-encoder reranker for Sentinel RAG (T1.8).

Re-ranks hybrid retrieval candidates using a BGE cross-encoder model to
produce a high-quality final ordering.  The cross-encoder jointly encodes
the query and each candidate document, producing a relevance score that
captures fine-grained semantic matching invisible to bi-encoder (dense)
and sparse (BM25) scoring alone.

Usage::

    from sentinel_rag.reranker import CrossEncoderReranker
    from sentinel_rag.retrieve import retrieve

    results = retrieve("how does auth work?")
    reranker = CrossEncoderReranker()
    top5 = reranker.rerank("how does auth work?", results, top_k=5)

Standalone smoke-test::

    python -m sentinel_rag.reranker "your query here"

Environment variables
---------------------

======================  ===========================  ============================
Variable                Default                      Description
======================  ===========================  ============================
``RERANK_MODEL``        BAAI/bge-reranker-v2-m3      HuggingFace model ID for the
                                                     cross-encoder.
``RERANK_DEVICE``       ``None`` (auto-detect)        ``"cpu"``, ``"cuda"``, or
                                                     ``None`` for auto.
======================  ===========================  ============================
"""

from __future__ import annotations

import os
import sys

from sentinel_rag.retrieve import RetrievedPoint, retrieve

# ---------------------------------------------------------------------------
# Cross-encoder reranker
# ---------------------------------------------------------------------------


class CrossEncoderReranker:
    """Re-ranks retrieval candidates with a cross-encoder model.

    The cross-encoder jointly processes ``(query, document)`` pairs and
    produces a relevance score for each.  This is more accurate than
    bi-encoder scoring but also more expensive — it should be applied to
    a relatively small candidate set (e.g. the top 50 from hybrid retrieval).

    The model is loaded lazily on first use so that importing the module
    is always cheap.
    """

    def __init__(
        self,
        model_name: str | None = None,
        device: str | None = None,
    ) -> None:
        """Initialise the reranker.

        Args:
            model_name: HuggingFace model ID for the cross-encoder.  If
                ``None``, the ``RERANK_MODEL`` env var is read, defaulting to
                ``"BAAI/bge-reranker-v2-m3"``.
            device: ``"cpu"``, ``"cuda"``, or ``None`` for auto-detection.
                Defaults to ``RERANK_DEVICE`` env var (``None`` if unset).
        """
        self.model_name = model_name or os.getenv("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
        self.device = device or os.getenv("RERANK_DEVICE") or None
        self._model: object | None = None

    # -- lazy model loading --------------------------------------------------

    @property
    def model(self) -> object:
        """Lazily-loaded :class:`CrossEncoder` instance."""
        if self._model is None:
            from sentence_transformers import CrossEncoder

            kwargs: dict[str, object] = {}
            if self.device is not None:
                kwargs["device"] = self.device
            self._model = CrossEncoder(self.model_name, **kwargs)  # type: ignore[arg-type]
        return self._model

    # -- reranking -----------------------------------------------------------

    def rerank(
        self,
        query: str,
        candidates: list[RetrievedPoint],
        top_k: int = 5,
    ) -> list[RetrievedPoint]:
        """Re-rank *candidates* using the cross-encoder.

        Args:
            query: Original natural-language query.
            candidates: Candidate chunks from hybrid retrieval (e.g. top 50).
            top_k: Number of results to return after re-ranking (default 5).

        Returns:
            A new list of :class:`~sentinel_rag.retrieve.RetrievedPoint`
            objects with cross-encoder relevance scores, sorted best-first.
            The returned list is at most *top_k* long.

        Raises:
            ValueError: If *query* is empty or *top_k* < 1.
            RuntimeError: If the cross-encoder model fails.
        """
        if not query or not query.strip():
            raise ValueError("query must be non-empty")
        if top_k < 1:
            raise ValueError("top_k must be >= 1")
        if not candidates:
            return []

        # Build (query, document) pairs
        pairs: list[tuple[str, str]] = [(query, c.text) for c in candidates]

        # Get cross-encoder scores
        try:
            scores: list[float] = self.model.predict(pairs, show_progress_bar=False)  # type: ignore[union-attr]
        except Exception as exc:
            raise RuntimeError(f"Cross-encoder reranking failed: {exc}") from exc

        # Normalise scores to a flat list of floats.
        # predict() may return a 2D array (n,1), 1D array (n,), or list.
        flat_scores: list[float] = []
        for s in scores:
            try:
                # If s is a list/array, extract first element.
                if isinstance(s, (list, tuple)) or (hasattr(s, "__len__") and hasattr(s, "__getitem__") and not isinstance(s, (str, bytes))):
                    flat_scores.append(float(s[0]) if len(s) > 0 else 0.0)  # type: ignore[index]
                else:
                    flat_scores.append(float(s))  # type: ignore[arg-type]
            except (TypeError, ValueError, IndexError):
                flat_scores.append(0.0)

        # Pair, sort descending by score, and trim to top_k
        ranked = sorted(
            zip(candidates, flat_scores, strict=True), key=lambda pair: pair[1], reverse=True
        )

        return [
            RetrievedPoint(
                chunk_id=c.chunk_id,
                text=c.text,
                path=c.path,
                line_start=c.line_start,
                line_end=c.line_end,
                source_type=c.source_type,
                score=float(score),
                metadata=dict(c.metadata),
            )
            for c, score in ranked[:top_k]
        ]

    def is_available(self) -> bool:
        """Return ``True`` if the cross-encoder model can be loaded.

        Does not actually load the model — only checks that
        ``sentence-transformers`` is importable.
        """
        try:
            import sentence_transformers  # noqa: F401

            return True
        except ImportError:
            return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m sentinel_rag.reranker``.

    Pulls the top 50 hybrid results, re-ranks with the cross-encoder,
    and prints the top 5.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Cross-encoder reranking for Sentinel RAG.",
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
        default=5,
        help="Number of results after re-ranking (default: 5).",
    )
    parser.add_argument(
        "--prefetch",
        type=int,
        default=50,
        help="Candidates from hybrid retrieval to re-rank (default: 50).",
    )
    args = parser.parse_args(argv)
    query = " ".join(args.query)

    # 1. Hybrid retrieval
    try:
        candidates = retrieve(query, top_k=args.prefetch)
    except (ValueError, RuntimeError) as exc:
        print(f"Error during retrieval: {exc}", file=sys.stderr)
        return 1

    if not candidates:
        print("No candidates from retrieval.", file=sys.stderr)
        return 0

    # 2. Cross-encoder rerank
    reranker = CrossEncoderReranker()
    if not reranker.is_available():
        print(
            "sentence-transformers is not installed. Install with:"
            " uv pip install sentence-transformers",
            file=sys.stderr,
        )
        return 1

    try:
        ranked = reranker.rerank(query, candidates, top_k=args.top_k)
    except RuntimeError as exc:
        print(f"Error during reranking: {exc}", file=sys.stderr)
        return 1

    # 3. Print results
    if not ranked:
        print("No results after reranking.", file=sys.stderr)
        return 0

    for i, r in enumerate(ranked, 1):
        print(f"#{i}  [{r.source_type}] {r.path}:{r.line_start}-{r.line_end}  score={r.score:.4f}")
        text = r.text.replace("\n", " ↵ ")[:200]
        suffix = "…" if len(r.text) > 200 else ""
        print(f"    {text}{suffix}")
        if r.metadata:
            meta_str = "  ".join(f"{k}={v}" for k, v in sorted(r.metadata.items()))
            print(f"    [{meta_str}]")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
