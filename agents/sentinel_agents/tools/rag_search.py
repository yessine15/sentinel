"""RAG search tool — lets the SRE agent query the Sentinel knowledge base.

The tool calls the hybrid retrieval pipeline (dense + sparse, RRF fusion)
and returns the top results with source citations so the agent can ground
its answers in actual code, runbooks, and past incidents.

Errors are reported inline — the tool never raises, because the agent
will read the error text and adapt its response (e.g. "I couldn't search
the knowledge base right now").
"""

from __future__ import annotations

from langchain_core.tools import tool

from sentinel_agents.tools.base import is_stub, register


# Maximum number of results to return to the agent.  More than this
# bloats the LLM context window without meaningful benefit.
_MAX_RESULTS = 5

# Maximum length of each snippet in the tool response.
_MAX_SNIPPET_CHARS = 300


def _format_search_response(query: str, results: list) -> str:
    """Format retrieval results as an agent-friendly string with citations."""
    if not results:
        return (
            f"No documents found for: \"{query}\"\n\n"
            "The knowledge base may not contain relevant content yet. "
            "Consider using other tools (kubectl, PromQL, LogQL) to "
            "gather information directly."
        )

    lines = [f"Found {len(results)} document(s) for: \"{query}\""]
    lines.append("")

    for i, r in enumerate(results[: _MAX_RESULTS], start=1):
        snippet = r.text[: _MAX_SNIPPET_CHARS].replace("\n", " ")
        if len(r.text) > _MAX_SNIPPET_CHARS:
            snippet += "…"
        lines.append(
            f"[{i}] {r.path}:{r.line_start}-{r.line_end} "
            f"(score: {r.score:.3f}, type: {r.source_type})"
        )
        lines.append(f"    {snippet}")

    if len(results) > _MAX_RESULTS:
        lines.append(
            f"\n(Showing top {_MAX_RESULTS} of {len(results)} results. "
            f"Refine the query for more precise results.)"
        )

    return "\n".join(lines)


@tool
def rag_search(query: str) -> str:
    """Search the Sentinel knowledge base for code, runbooks, and past incidents.

    Use this when the user asks a question that might be answered by
    existing documentation, source code, or runbooks — for example
    "how does authentication work?" or "what is the /health endpoint?"
    or "what runbooks cover OOMKilled pods?".

    The knowledge base contains:
    - Source code from the Sentinel project itself
    - Runbook documents describing incident response procedures
    - Past incident postmortems (when available)

    Args:
        query: A natural-language question or search phrase.

    Returns:
        A formatted list of matching documents with file paths, line
        ranges, and relevance scores.  Use these citations when
        answering the user.
    """
    if not query or not query.strip():
        return "❌ Please provide a non-empty search query."

    # Stub mode — return what *would* be searched (safe for unit tests).
    if is_stub():
        return (
            f"[T2.4 STUB] Would search the KB for: \"{query.strip()}\"\n"
            f"  Index: sentinel_kb (Qdrant hybrid dense+sparse)\n"
            f"  Top-k: 10\n"
            f"  Results would include file paths, line ranges, and snippets."
        )

    try:
        from sentinel_rag.retrieve import retrieve, RetrievedPoint
    except ImportError as exc:
        return (
            f"❌ RAG pipeline not available: {exc}\n\n"
            "The sentinel_rag package is not installed or importable. "
            "Make sure the rag extras are installed "
            "(pip install -e '.[rag]')."
        )

    try:
        results: list[RetrievedPoint] = retrieve(query.strip(), top_k=10)
    except ValueError as exc:
        return f"❌ Invalid query: {exc}"
    except RuntimeError as exc:
        # Qdrant unreachable, collection missing, etc.
        return (
            f"❌ Knowledge base unavailable: {exc}\n\n"
            "Qdrant may be down or the collection may not exist yet. "
            "Try running `python -m sentinel_rag.ingest code ./api` to "
            "populate the knowledge base."
        )
    except Exception as exc:
        return f"❌ Unexpected error during search: {type(exc).__name__}: {exc}"

    return _format_search_response(query.strip(), results)


register(rag_search, category="rag")
