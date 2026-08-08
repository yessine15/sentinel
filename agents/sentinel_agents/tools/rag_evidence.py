"""RAG evidence tool — ranked evidence with citations (T3.4).

Wraps the Phase 1 retrieval pipeline (hybrid retrieve + cross-encoder
rerank) and returns *ranked evidence records* — path, line range,
score, source type, and snippet — as structured JSON so the RAG Agent
(and any other agent via the graph state) can ground its answers in
citations.

Unlike :mod:`rag_search` (which returns a human-readable text block),
this tool returns machine-parseable JSON that the graph extracts into
``scratchpad["evidence"]`` so downstream specialists receive evidence
with citations through the shared state.

Environment variables
---------------------
QDRANT_URL / OLLAMA_BASE_URL / OLLAMA_MODEL / RERANK_MODEL / etc.
    Forwarded to the Phase 1 pipeline — see :mod:`sentinel_rag.retrieve`
    and :mod:`sentinel_rag.reranker`.
"""

from __future__ import annotations

import json

from langchain_core.tools import tool

from sentinel_agents.tools.base import is_stub, register

_MAX_SNIPPET_CHARS = 300


def _stub_payload(query: str) -> str:
    """Return a deterministic stub evidence payload (safe for unit tests)."""
    payload = {
        "query": query,
        "evidence": [
            {
                "path": "agents/sentinel_agents/graph.py",
                "lines": "1-40",
                "score": 0.93,
                "source_type": "code",
                "snippet": "LangGraph multi-agent orchestrator — Triage → "
                           "Specialist pattern. T2.1-T2.2: Single SRE agent "
                           "with tool loop.",
            },
            {
                "path": "docs/architecture.md",
                "lines": "12-30",
                "score": 0.87,
                "source_type": "markdown",
                "snippet": "The Sentinel platform is built around a "
                           "multi-agent graph where a triage agent routes "
                           "incidents to specialist agents.",
            },
            {
                "path": "docs/runbooks/oomkilled.md",
                "lines": "5-18",
                "score": 0.71,
                "source_type": "runbook",
                "snippet": "When a pod is OOMKilled, check the memory "
                           "requests/limits and the node allocatable "
                           "memory before resizing.",
            },
        ],
    }
    return json.dumps(payload, indent=2)


def _render_evidence(query: str, results: list) -> str:
    """Convert retrieved (and re-ranked) points into structured JSON evidence."""
    evidence = [
        {
            "path": r.path,
            "lines": f"{r.line_start}-{r.line_end}",
            "score": round(float(r.score), 4),
            "source_type": r.source_type,
            "snippet": r.text[:_MAX_SNIPPET_CHARS],
        }
        for r in results
    ]
    payload = {"query": query, "evidence": evidence}
    return json.dumps(payload, indent=2)


@tool
def rag_evidence(query: str, top_k: int = 5) -> str:
    """Retrieve ranked evidence with citations from the Sentinel knowledge base.

    This is the RAG Agent's primary tool.  It runs the full Phase 1
    retrieval pipeline (hybrid dense + sparse retrieval, cross-encoder
    re-ranking) and returns structured JSON evidence records — each with
    ``path``, ``lines``, ``score``, ``source_type``, and ``snippet`` —
    so answers can be grounded in citable sources.

    Use this when the user asks a question that might be answered by
    existing documentation, source code, or runbooks.  The knowledge
    base contains Sentinel source code, docs, and past incident runbooks.

    Args:
        query: A natural-language question or search phrase.
        top_k: How many evidence records to return (1-10, default 5).

    Returns:
        JSON with ``{"query": ..., "evidence": [{path, lines, score,
        source_type, snippet}, ...]}`` — or an inline error string.
    """
    if not query or not query.strip():
        return "❌ Please provide a non-empty search query."

    q = query.strip()

    # Bound top_k — keep evidence small enough for the LLM context window.
    try:
        k = int(top_k)
    except (TypeError, ValueError):
        return f"❌ top_k must be an integer, got '{top_k}'."
    if k < 1 or k > 10:
        return "❌ top_k must be between 1 and 10."

    # Stub mode — deterministic fake evidence (safe for unit tests).
    if is_stub():
        return _stub_payload(q)

    # Live mode — Phase 1 pipeline.
    try:
        from sentinel_rag.retrieve import retrieve
    except ImportError as exc:
        return (
            f"❌ RAG pipeline not available: {exc}\n\n"
            "The sentinel_rag package is not installed or importable. "
            "Make sure the rag extras are installed "
            "(pip install -e '.[rag]')."
        )

    try:
        results = retrieve(q, top_k=10)
    except ValueError as exc:
        return f"❌ Invalid query: {exc}"
    except RuntimeError as exc:
        return (
            f"❌ Knowledge base unavailable: {exc}\n\n"
            "Qdrant may be down or the collection may not exist yet. "
            "Try running `python -m sentinel_rag.ingest code ./api` to "
            "populate the knowledge base."
        )
    except Exception as exc:
        return f"❌ Unexpected error during retrieval: {type(exc).__name__}: {exc}"

    # Best-effort cross-encoder re-ranking — fall back to raw top-k if the
    # reranker model can't be loaded (heavy HF download on first use).
    try:
        from sentinel_rag.reranker import CrossEncoderReranker

        reranked = CrossEncoderReranker().rerank(q, results, top_k=k)
        results = reranked or results[:k]
    except Exception:
        results = results[:k]

    if not results:
        return json.dumps(
            {"query": q, "evidence": [], "note": "No documents found."},
            indent=2,
        )

    return _render_evidence(q, results)


register(rag_evidence, category="rag")
