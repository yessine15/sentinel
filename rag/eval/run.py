"""Evaluation runner for Sentinel RAG (T1.11).

Loads the golden Q&A dataset (T1.10) and runs the retrieval pipeline
against each question, computing recall@k and (optionally) faithfulness
metrics.

Usage::

    python -m sentinel_rag.eval.run

The script expects Qdrant + Ollama (or OpenAI) to be available and the
``sentinel_kb`` collection to be populated.  Set ``QDRANT_URL`` and
``EMBED_PROVIDER`` as needed.

Environment variables
---------------------

======================  =======  ==============================================
Variable                Default  Description
======================  =======  ==============================================
``EVAL_GOLDEN_PATH``    rag/eval/golden.jsonl
                                 Path to the golden Q&A dataset.
``EVAL_K``              5        Number of top results to consider for recall.
``EVAL_THRESHOLD``      0.7      Minimum recall@k to pass (0.0-1.0).
``EVAL_SKIP_RERANKER``  0        Set to ``1`` to skip the cross-encoder
                                 reranker (faster, lower quality).
======================  =======  ==============================================

Output (example)::

    Recall @ 5
    ========================================
      q01  HIT   api/sentinel_api/main.py
      q02  HIT   rag/sentinel_rag/embed.py
      q03  MISS  (expected: rag/sentinel_rag/chunkers/prose.py)
    ...
    ----------------------------------------
    Recall@5:  12 / 16  (75.0%)   PASS  (threshold: 70.0%)
"""

from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from sentinel_rag.retrieve import RetrievedPoint, retrieve

# ---------------------------------------------------------------------------
# Golden dataset loader
# ---------------------------------------------------------------------------


def load_golden(path: str | None = None) -> list[dict[str, Any]]:
    """Load the golden Q&A dataset from a JSONL file.

    Args:
        path: Path to the golden dataset.  If ``None``, reads the
            ``EVAL_GOLDEN_PATH`` env var, defaulting to
            ``rag/eval/golden.jsonl`` relative to the repo root.

    Returns:
        A list of dicts, each with keys ``id``, ``question``, ``answer``,
        and ``sources``.

    Raises:
        FileNotFoundError: If the golden file does not exist.
        ValueError: If any entry is missing required keys.
    """
    if path is None:
        path = os.getenv("EVAL_GOLDEN_PATH", "rag/eval/golden.jsonl")

    if not os.path.isfile(path):
        raise FileNotFoundError(f"Golden dataset not found at {path}")

    entries: list[dict[str, Any]] = []
    required_keys = {"id", "question", "answer", "sources"}

    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_no}: {exc}") from exc

            missing = required_keys - entry.keys()
            if missing:
                raise ValueError(
                    f"Entry at {path}:{line_no} is missing keys: {', '.join(sorted(missing))}"
                )

            entries.append(entry)

    if not entries:
        raise ValueError(f"No valid entries found in {path}")

    return entries


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def compute_recall_at_k(
    results: list[RetrievedPoint],
    expected_sources: list[str],
    k: int,
) -> tuple[bool, str]:
    """Check whether any expected source appears in the top-*k* retrieved paths.

    Args:
        results: Ranked retrieval results (best first).
        expected_sources: List of expected source file paths (relative to
            repo root, e.g. ``"api/sentinel_api/main.py"``).
        k: Consider only the top *k* results.

    Returns:
        A ``(hit, detail)`` tuple.  *hit* is ``True`` when at least one
        expected source path appears in the top-*k* results.  *detail* is
        a human-readable summary (the matched path, or the expected path
        when missed).
    """
    top_paths: set[str] = {r.path for r in results[:k]}
    expected_set = set(expected_sources)

    matched = top_paths & expected_set
    if matched:
        return True, sorted(matched)[0]
    return False, expected_sources[0] if expected_sources else "(no sources)"


# ---------------------------------------------------------------------------
# Eval runner
# ---------------------------------------------------------------------------


def run_eval(
    golden: list[dict[str, Any]],
    retriever_fn: Callable[[str], list[RetrievedPoint]] | None = None,
    k: int = 5,
) -> dict[str, Any]:
    """Run the golden dataset through the retrieval pipeline.

    Args:
        golden: Loaded golden Q&A entries (from :func:`load_golden`).
        retriever_fn: A callable ``(query: str) -> list[RetrievedPoint]``.
            If ``None``, uses :func:`~sentinel_rag.retrieve.retrieve`
            directly.
        k: Top-*k* cutoff for recall computation.

    Returns:
        A dict with keys:

        - ``"total"`` — total number of questions evaluated.
        - ``"hits"`` — number of questions where recall@k succeeded.
        - ``"recall_at_k"`` — recall@k as a float (0.0-1.0).
        - ``"k"`` — the *k* value used.
        - ``"per_question"`` — list of per-question result dicts, each
          with ``id``, ``question``, ``hit``, ``detail``, and
          ``retrieved`` (top-*k* file paths).
        - ``"errors"`` — list of ``(id, error_message)`` tuples for
          questions that failed during retrieval.
    """
    if retriever_fn is None:
        # Default: use the real retrieve(), optionally with no reranker
        import os as _os

        skip_reranker = _os.getenv("EVAL_SKIP_RERANKER", "0") == "1"

        def _retrieve(query: str) -> list[RetrievedPoint]:
            if skip_reranker:
                return retrieve(query, top_k=k)
            from sentinel_rag.reranker import CrossEncoderReranker

            reranker = CrossEncoderReranker()
            candidates = retrieve(query, top_k=max(50, k))
            return reranker.rerank(query, candidates, top_k=k)

        retriever_fn = _retrieve

    per_question: list[dict[str, Any]] = []
    errors: list[tuple[str, str]] = []
    hits = 0

    for entry in golden:
        qid = entry["id"]
        question = entry["question"]
        expected = entry["sources"]

        try:
            results = retriever_fn(question)
        except Exception as exc:
            errors.append((qid, str(exc)))
            per_question.append(
                {
                    "id": qid,
                    "question": question,
                    "hit": False,
                    "detail": f"ERROR: {exc}",
                    "retrieved": [],
                }
            )
            continue

        hit, detail = compute_recall_at_k(results, expected, k)
        if hit:
            hits += 1

        per_question.append(
            {
                "id": qid,
                "question": question,
                "hit": hit,
                "detail": detail,
                "retrieved": [r.path for r in results[:k]],
            }
        )

    total = len(golden)
    recall = hits / total if total > 0 else 0.0

    return {
        "total": total,
        "hits": hits,
        "recall_at_k": recall,
        "k": k,
        "per_question": per_question,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _hit_marker(hit: bool) -> str:
    """Return a coloured hit/miss marker (ANSI)."""
    if hit:
        return "\033[32m HIT \033[0m"  # green
    return "\033[31mMISS \033[0m"  # red


def format_metrics_table(metrics: dict[str, Any]) -> str:
    """Format evaluation metrics as a human-readable table.

    Args:
        metrics: The dict returned by :func:`run_eval`.

    Returns:
        A multi-line string suitable for printing to a terminal.
    """
    k = metrics["k"]
    threshold = float(os.getenv("EVAL_THRESHOLD", "0.7"))
    passed = metrics["recall_at_k"] >= threshold

    lines: list[str] = []
    lines.append(f"Recall @ {k}")
    lines.append("=" * 40)

    # Per-question rows
    for pq in metrics["per_question"]:
        marker = _hit_marker(pq["hit"])
        detail = pq["detail"]
        lines.append(f"  {pq['id']:5s} {marker}  {detail}")

    # Error rows
    for qid, err in metrics["errors"]:
        lines.append(f"  {qid:5s} \033[33m ERR \033[0m  {err}")

    lines.append("-" * 40)

    recall_pct = metrics["recall_at_k"] * 100
    threshold_pct = threshold * 100
    status = "\033[32mPASS\033[0m" if passed else "\033[31mFAIL\033[0m"
    lines.append(
        f"Recall@{k}:  {metrics['hits']} / {metrics['total']}"
        f"  ({recall_pct:.1f}%)   {status}  (threshold: {threshold_pct:.0f}%)"
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m sentinel_rag.eval.run``.

    Loads the golden dataset, runs retrieval for each question, prints
    a metrics table, and exits non-zero if recall@k is below the
    threshold.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the golden Q&A evaluation for Sentinel RAG.",
    )
    parser.add_argument(
        "-g",
        "--golden",
        default=os.getenv("EVAL_GOLDEN_PATH", "rag/eval/golden.jsonl"),
        help="Path to the golden JSONL file (default: rag/eval/golden.jsonl).",
    )
    parser.add_argument(
        "-k",
        type=int,
        default=int(os.getenv("EVAL_K", "5")),
        help="Top-k cutoff for recall (default: 5).",
    )
    parser.add_argument(
        "-t",
        "--threshold",
        type=float,
        default=float(os.getenv("EVAL_THRESHOLD", "0.7")),
        help="Minimum recall@k to pass (default: 0.7).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output the full metrics dict as JSON instead of a table.",
    )
    parser.add_argument(
        "--skip-reranker",
        action="store_true",
        default=os.getenv("EVAL_SKIP_RERANKER", "0") == "1",
        help="Skip the cross-encoder reranker (faster).",
    )
    args = parser.parse_args(argv)

    # 1. Load golden dataset
    try:
        golden = load_golden(args.golden)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error loading golden dataset: {exc}", file=sys.stderr)
        return 2

    # 2. Run evaluation
    # Set env for the default retriever_fn if --skip-reranker was passed
    if args.skip_reranker:
        os.environ["EVAL_SKIP_RERANKER"] = "1"

    try:
        metrics = run_eval(golden, k=args.k)
    except Exception as exc:
        print(f"Error during evaluation: {exc}", file=sys.stderr)
        return 3

    # 3. Output
    if args.json:
        # Strip ANSI codes for JSON output
        json_metrics = {
            "total": metrics["total"],
            "hits": metrics["hits"],
            "recall_at_k": metrics["recall_at_k"],
            "k": metrics["k"],
            "per_question": [
                {
                    "id": pq["id"],
                    "question": pq["question"],
                    "hit": pq["hit"],
                    "detail": pq["detail"],
                    "retrieved": pq["retrieved"],
                }
                for pq in metrics["per_question"]
            ],
            "errors": [{"id": eid, "error": err} for eid, err in metrics["errors"]],
        }
        json.dump(json_metrics, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        print(format_metrics_table(metrics))

    # 4. Exit code
    passed = metrics["recall_at_k"] >= args.threshold
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
