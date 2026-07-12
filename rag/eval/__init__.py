"""Evaluation utilities for Sentinel RAG (M1.4).

Exports the public API of the eval package:

- :func:`load_golden` — load the golden Q&A dataset.
- :func:`compute_recall_at_k` — check if expected sources appear in top-k.
- :func:`run_eval` — run the full evaluation pipeline.
- :func:`format_metrics_table` — pretty-print results.
"""

from eval.run import (
    compute_recall_at_k,
    format_metrics_table,
    load_golden,
    main,
    run_eval,
)

__all__ = [
    "compute_recall_at_k",
    "format_metrics_table",
    "load_golden",
    "main",
    "run_eval",
]
