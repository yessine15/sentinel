"""Citation renderer for Sentinel RAG (T1.9).

Produces attributable, structured output from retrieval results so that
every chunk is traceable back to its source file and line range.

Two output formats are supported:

1. **JSON** — ``{answer, sources: [{path, lines, snippet}]}`` suitable for
   API responses and downstream consumers.
2. **Text** — an answer with numbered inline markers (``[1]``, ``[2]``, …)
   followed by a formatted sources block.

Usage::

    from sentinel_rag.citation import (
        format_source_marker,
        render_citation_block,
        render_citation_json,
        render_citation_text,
    )
    from sentinel_rag.retrieve import retrieve

    results = retrieve("how does auth work?")
    answer = "Auth uses JWT tokens [1] validated by middleware [2]."

    # JSON output for an API
    payload = render_citation_json(answer, results)

    # Human-readable text with a sources block
    print(render_citation_text(answer, results))

Standalone smoke-test (retrieval → formatted sources, no LLM yet)::

    python -m sentinel_rag.citation "your query here"
"""

from __future__ import annotations

import json
import sys
from typing import Any

from sentinel_rag.reranker import CrossEncoderReranker
from sentinel_rag.retrieve import RetrievedPoint, retrieve

# ---------------------------------------------------------------------------
# Source marker helper
# ---------------------------------------------------------------------------


def format_source_marker(source: RetrievedPoint) -> str:
    """Return a ``[path:line_start-line_end]`` marker for a single source.

    Args:
        source: A retrieved chunk with path and line-range metadata.

    Returns:
        A citation marker like ``"api/main.py:42-58"`` wrapped in brackets.

    Raises:
        TypeError: If *source* is not a :class:`RetrievedPoint`.
    """
    if not isinstance(source, RetrievedPoint):
        raise TypeError(f"Expected RetrievedPoint, got {type(source).__name__}")
    return f"[{source.path}:{source.line_start}-{source.line_end}]"


# ---------------------------------------------------------------------------
# JSON citation output
# ---------------------------------------------------------------------------


def render_citation_json(
    answer: str,
    sources: list[RetrievedPoint],
) -> dict[str, Any]:
    """Render answer + sources as a JSON-friendly dict.

    Args:
        answer: The natural-language answer text (may contain ``[N]``
            markers that refer to the numbered sources below).
        sources: Ordered list of source chunks (best first).

    Returns:
        A dict with keys:

        - ``"answer"`` — the answer string.
        - ``"sources"`` — a list of ``{path, lines, snippet}`` dicts,
          one per source in the same order as *sources*.

    Raises:
        TypeError: If *answer* is not a string or any element of
            *sources* is not a :class:`RetrievedPoint`.
    """
    if not isinstance(answer, str):
        raise TypeError(f"Expected str for answer, got {type(answer).__name__}")

    return {
        "answer": answer,
        "sources": [
            {
                "path": s.path,
                "lines": f"{s.line_start}-{s.line_end}",
                "snippet": s.text,
            }
            for s in sources
        ],
    }


# ---------------------------------------------------------------------------
# Text citation output
# ---------------------------------------------------------------------------


def render_citation_block(sources: list[RetrievedPoint]) -> str:
    """Build a numbered sources block for appending to an answer.

    Each source is rendered as::

        [N] path:line_start-line_end — snippet…

    where *N* is the 1-based index in *sources* and the snippet is
    truncated to 150 characters if longer.

    Args:
        sources: Ordered list of source chunks (best first).

    Returns:
        A Markdown-formatted sources block, or an empty string when
        *sources* is empty.  The block always starts with a thematic
        break (``---``) followed by a ``**Sources:**`` heading.

    Raises:
        TypeError: If any element of *sources* is not a
            :class:`RetrievedPoint`.
    """
    if not sources:
        return ""

    lines: list[str] = ["---", "**Sources:**", ""]
    for i, s in enumerate(sources, 1):
        marker = format_source_marker(s)
        snippet = s.text
        if len(snippet) > 150:
            snippet = snippet[:150] + "…"
        # Flatten internal newlines for one-line display
        snippet_flat = snippet.replace("\n", " ↵ ")
        lines.append(f"[{i}] {marker} — {snippet_flat}")

    return "\n".join(lines)


def render_citation_text(answer: str, sources: list[RetrievedPoint]) -> str:
    """Render a full text answer with inline markers and a sources block.

    The *answer* is expected to already contain numbered markers (e.g.
    ``[1]``, ``[2]``) inserted by the LLM.  This function appends a
    formatted sources block that maps each number to a file:lines
    citation with a snippet.

    Args:
        answer: Natural-language answer with optional ``[N]`` markers.
        sources: Ordered list of source chunks (best first).

    Returns:
        The answer followed by the sources block, separated by a blank
        line.

    Raises:
        TypeError: If *answer* is not a string or any element of
            *sources* is not a :class:`RetrievedPoint`.
    """
    if not isinstance(answer, str):
        raise TypeError(f"Expected str for answer, got {type(answer).__name__}")

    block = render_citation_block(sources)
    if not block:
        return answer
    return f"{answer}\n\n{block}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m sentinel_rag.citation``.

    Runs the full retrieval + rerank pipeline against *query* and prints
    the sources in both JSON and human-readable text formats.  A
    placeholder answer is generated so the output structure can be
    inspected end-to-end.

    Usage::

        python -m sentinel_rag.citation "your query here"
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Citation renderer for Sentinel RAG — shows attributable sources.",
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
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Print only the JSON citation payload to stdout.",
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
        sources = reranker.rerank(query, candidates, top_k=args.top_k)
    except RuntimeError as exc:
        print(f"Error during reranking: {exc}", file=sys.stderr)
        return 1

    if not sources:
        print("No results after reranking.", file=sys.stderr)
        return 0

    # 3. Build a placeholder answer for preview
    markers = " ".join(f"[{i}]" for i in range(1, len(sources) + 1))
    placeholder = f"(Placeholder answer — LLM not wired yet.) Relevant sources: {markers}"

    if args.json:
        payload = render_citation_json(placeholder, sources)
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        # Human-readable output
        print(render_citation_text(placeholder, sources))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
