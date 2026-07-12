"""Tests for the Sentinel RAG eval runner (T1.11)."""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import patch

import pytest

from eval.run import (
    compute_recall_at_k,
    format_metrics_table,
    load_golden,
    main,
    run_eval,
)
from sentinel_rag.retrieve import RetrievedPoint

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_point(
    chunk_id: str = "a",
    path: str = "api/main.py",
    score: float = 0.9,
) -> RetrievedPoint:
    return RetrievedPoint(
        chunk_id=chunk_id,
        text="fake text",
        path=path,
        line_start=1,
        line_end=2,
        source_type="code",
        score=score,
    )


def _write_golden(entries: list[dict], dir_path: str) -> str:
    """Write a golden.jsonl file and return its path."""
    path = os.path.join(dir_path, "golden.jsonl")
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    return path


def _sample_entry(
    qid: str = "q01",
    question: str = "What is this?",
    answer: str = "An answer.",
    sources: list[str] | None = None,
) -> dict:
    return {
        "id": qid,
        "question": question,
        "answer": answer,
        "sources": sources or ["api/main.py"],
    }


# ---------------------------------------------------------------------------
# load_golden
# ---------------------------------------------------------------------------


class TestLoadGolden:
    """Tests for load_golden()."""

    def test_loads_valid_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_golden([_sample_entry("q01")], tmp)
            entries = load_golden(path)
            assert len(entries) == 1
            assert entries[0]["id"] == "q01"

    def test_loads_multiple_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_golden([_sample_entry(f"q{i:02d}") for i in range(1, 17)], tmp)
            entries = load_golden(path)
            assert len(entries) == 16

    def test_skips_blank_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "golden.jsonl")
            with open(path, "w") as f:
                f.write(json.dumps(_sample_entry("q01")) + "\n")
                f.write("\n")
                f.write(json.dumps(_sample_entry("q02")) + "\n")
                f.write("   \n")
            entries = load_golden(path)
            assert len(entries) == 2

    def test_file_not_found_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_golden("/nonexistent/golden.jsonl")

    def test_invalid_json_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "golden.jsonl")
            with open(path, "w") as f:
                f.write('{"id": "q01", broken\n')
            with pytest.raises(ValueError, match="Invalid JSON"):
                load_golden(path)

    def test_missing_required_keys_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_golden([{"id": "q01", "question": "Q?"}], tmp)
            with pytest.raises(ValueError, match="missing keys"):
                load_golden(path)

    def test_empty_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_golden([], tmp)
            with pytest.raises(ValueError, match="No valid entries"):
                load_golden(path)

    def test_default_path_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_golden([_sample_entry("q01")], tmp)
            with patch.dict(os.environ, {"EVAL_GOLDEN_PATH": path}):
                entries = load_golden()
                assert len(entries) == 1

    def test_all_fields_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entry = {
                "id": "q01",
                "question": "What?",
                "answer": "The answer.",
                "sources": ["a.py", "b.py"],
                "extra": "ignored",
            }
            path = _write_golden([entry], tmp)
            entries = load_golden(path)
            assert entries[0]["id"] == "q01"
            assert entries[0]["question"] == "What?"
            assert entries[0]["answer"] == "The answer."
            assert entries[0]["sources"] == ["a.py", "b.py"]


# ---------------------------------------------------------------------------
# compute_recall_at_k
# ---------------------------------------------------------------------------


class TestComputeRecallAtK:
    """Tests for compute_recall_at_k()."""

    def test_hit_when_expected_path_in_top_k(self) -> None:
        results = [
            _fake_point("a", path="other.py"),
            _fake_point("b", path="api/main.py"),
            _fake_point("c", path="unrelated.py"),
        ]
        hit, detail = compute_recall_at_k(results, ["api/main.py"], k=3)
        assert hit is True
        assert detail == "api/main.py"

    def test_miss_when_expected_path_not_in_top_k(self) -> None:
        results = [
            _fake_point("a", path="other.py"),
            _fake_point("b", path="unrelated.py"),
        ]
        hit, detail = compute_recall_at_k(results, ["api/main.py"], k=5)
        assert hit is False
        assert detail == "api/main.py"

    def test_hit_with_multiple_expected_sources(self) -> None:
        results = [
            _fake_point("a", path="unrelated.py"),
            _fake_point("b", path="rag/embed.py"),
        ]
        hit, detail = compute_recall_at_k(results, ["api/main.py", "rag/embed.py"], k=5)
        assert hit is True
        assert detail == "rag/embed.py"

    def test_miss_when_result_beyond_k(self) -> None:
        results = [
            _fake_point("a", path="a.py"),
            _fake_point("b", path="b.py"),
            _fake_point("c", path="api/main.py"),
        ]
        hit, _detail = compute_recall_at_k(results, ["api/main.py"], k=2)
        assert hit is False

    def test_k_larger_than_results(self) -> None:
        results = [_fake_point("a", path="api/main.py")]
        hit, _detail = compute_recall_at_k(results, ["api/main.py"], k=10)
        assert hit is True

    def test_empty_results(self) -> None:
        hit, detail = compute_recall_at_k([], ["api/main.py"], k=5)
        assert hit is False
        assert detail == "api/main.py"

    def test_empty_expected_sources(self) -> None:
        results = [_fake_point("a", path="api/main.py")]
        hit, detail = compute_recall_at_k(results, [], k=5)
        assert hit is False
        assert detail == "(no sources)"

    def test_first_match_returned_when_multiple_match(self) -> None:
        results = [
            _fake_point("a", path="a.py"),
            _fake_point("b", path="b.py"),
        ]
        hit, detail = compute_recall_at_k(results, ["a.py", "b.py"], k=5)
        assert hit is True
        # First alphabetically when both match
        assert detail == "a.py"


# ---------------------------------------------------------------------------
# run_eval
# ---------------------------------------------------------------------------


class TestRunEval:
    """Tests for run_eval()."""

    def test_all_hits(self) -> None:
        golden = [
            _sample_entry("q01", question="What is A?", sources=["a.py"]),
            _sample_entry("q02", question="What is B?", sources=["b.py"]),
            _sample_entry("q03", question="What is C?", sources=["c.py"]),
        ]

        def retriever_fn(query: str) -> list[RetrievedPoint]:
            mapping = {"What is A?": "a.py", "What is B?": "b.py", "What is C?": "c.py"}
            return [_fake_point("x", path=mapping.get(query, "unknown"))]

        metrics = run_eval(golden, retriever_fn=retriever_fn, k=5)
        assert metrics["total"] == 3
        assert metrics["hits"] == 3
        assert metrics["recall_at_k"] == 1.0
        assert metrics["k"] == 5
        assert len(metrics["per_question"]) == 3
        assert metrics["errors"] == []

    def test_all_misses(self) -> None:
        golden = [
            _sample_entry("q01", sources=["target.py"]),
            _sample_entry("q02", sources=["target.py"]),
        ]

        def retriever_fn(query: str) -> list[RetrievedPoint]:
            return [_fake_point("x", path="wrong.py")]

        metrics = run_eval(golden, retriever_fn=retriever_fn, k=5)
        assert metrics["hits"] == 0
        assert metrics["recall_at_k"] == 0.0

    def test_mixed_hits_and_misses(self) -> None:
        golden = [
            _sample_entry("q01", question="What is A?", sources=["a.py"]),
            _sample_entry("q02", question="What is B?", sources=["b.py"]),
        ]

        def retriever_fn(query: str) -> list[RetrievedPoint]:
            # q01 hits, q02 misses
            if "A" in query:
                return [_fake_point("x", path="a.py")]
            return [_fake_point("x", path="wrong.py")]

        metrics = run_eval(golden, retriever_fn=retriever_fn, k=5)
        assert metrics["hits"] == 1
        assert metrics["recall_at_k"] == 0.5
        assert metrics["per_question"][0]["hit"] is True
        assert metrics["per_question"][1]["hit"] is False

    def test_errors_captured(self) -> None:
        golden = [
            _sample_entry("q01", question="What is A?", sources=["a.py"]),
            _sample_entry("q02", question="What is B?", sources=["b.py"]),
        ]

        def retriever_fn(query: str) -> list[RetrievedPoint]:
            if "B" in query:
                raise RuntimeError("boom")
            return [_fake_point("x", path="a.py")]

        metrics = run_eval(golden, retriever_fn=retriever_fn, k=5)
        assert metrics["hits"] == 1
        assert len(metrics["errors"]) == 1
        assert metrics["errors"][0][0] == "q02"
        assert "boom" in metrics["errors"][0][1]

    def test_per_question_has_retrieved_paths(self) -> None:
        golden = [_sample_entry("q01", sources=["a.py"])]

        def retriever_fn(query: str) -> list[RetrievedPoint]:
            return [
                _fake_point("x", path="a.py"),
                _fake_point("y", path="b.py"),
                _fake_point("z", path="c.py"),
            ]

        metrics = run_eval(golden, retriever_fn=retriever_fn, k=2)
        assert metrics["per_question"][0]["retrieved"] == ["a.py", "b.py"]

    def test_respects_k_parameter(self) -> None:
        golden = [_sample_entry("q01", sources=["target.py"])]

        def retriever_fn(query: str) -> list[RetrievedPoint]:
            return [
                _fake_point("1", path="a.py"),
                _fake_point("2", path="b.py"),
                _fake_point("3", path="target.py"),
            ]

        # k=2: target is at position 3, so miss
        metrics_k2 = run_eval(golden, retriever_fn=retriever_fn, k=2)
        assert metrics_k2["hits"] == 0

        # k=3: target is at position 3, so hit
        metrics_k3 = run_eval(golden, retriever_fn=retriever_fn, k=3)
        assert metrics_k3["hits"] == 1

    def test_empty_golden(self) -> None:
        metrics = run_eval([], k=5)
        assert metrics["total"] == 0
        assert metrics["recall_at_k"] == 0.0


# ---------------------------------------------------------------------------
# format_metrics_table
# ---------------------------------------------------------------------------


class TestFormatMetricsTable:
    """Tests for format_metrics_table()."""

    def test_includes_recall_header(self) -> None:
        metrics = {
            "total": 2,
            "hits": 1,
            "recall_at_k": 0.5,
            "k": 5,
            "per_question": [
                {
                    "id": "q01",
                    "question": "Q1",
                    "hit": True,
                    "detail": "a.py",
                    "retrieved": ["a.py"],
                },
                {
                    "id": "q02",
                    "question": "Q2",
                    "hit": False,
                    "detail": "b.py",
                    "retrieved": ["wrong.py"],
                },
            ],
            "errors": [],
        }
        table = format_metrics_table(metrics)
        assert "Recall @ 5" in table
        assert "q01" in table
        assert "q02" in table
        assert "HIT" in table
        assert "MISS" in table
        assert "1 / 2" in table
        assert "50.0%" in table

    def test_pass_when_above_threshold(self) -> None:
        with patch.dict(os.environ, {"EVAL_THRESHOLD": "0.6"}):
            metrics = {
                "total": 10,
                "hits": 8,
                "recall_at_k": 0.8,
                "k": 5,
                "per_question": [],
                "errors": [],
            }
            table = format_metrics_table(metrics)
            assert "PASS" in table

    def test_fail_when_below_threshold(self) -> None:
        with patch.dict(os.environ, {"EVAL_THRESHOLD": "0.9"}):
            metrics = {
                "total": 10,
                "hits": 8,
                "recall_at_k": 0.8,
                "k": 5,
                "per_question": [],
                "errors": [],
            }
            table = format_metrics_table(metrics)
            assert "FAIL" in table

    def test_errors_shown(self) -> None:
        metrics = {
            "total": 1,
            "hits": 0,
            "recall_at_k": 0.0,
            "k": 5,
            "per_question": [],
            "errors": [("q01", "something broke")],
        }
        table = format_metrics_table(metrics)
        assert "ERR" in table
        assert "something broke" in table


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    """Tests for the eval CLI main() function."""

    def _make_golden_file(self, tmp: str) -> str:
        return _write_golden(
            [
                _sample_entry("q01", "What is X?", sources=["a.py"]),
                _sample_entry("q02", "What is Y?", sources=["b.py"]),
            ],
            tmp,
        )

    def test_runs_and_exits_zero_when_passing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            golden_path = self._make_golden_file(tmp)

            def fake_retrieve(query: str) -> list[RetrievedPoint]:
                if "X" in query:
                    return [_fake_point("x", path="a.py")]
                return [_fake_point("y", path="b.py")]

            with patch("eval.run.run_eval") as m_run:
                m_run.return_value = {
                    "total": 2,
                    "hits": 2,
                    "recall_at_k": 1.0,
                    "k": 5,
                    "per_question": [
                        {
                            "id": "q01",
                            "question": "What is X?",
                            "hit": True,
                            "detail": "a.py",
                            "retrieved": ["a.py"],
                        },
                        {
                            "id": "q02",
                            "question": "What is Y?",
                            "hit": True,
                            "detail": "b.py",
                            "retrieved": ["b.py"],
                        },
                    ],
                    "errors": [],
                }
                exit_code = main(["-g", golden_path, "-t", "0.7"])
                assert exit_code == 0

    def test_exits_nonzero_when_failing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            golden_path = self._make_golden_file(tmp)

            with patch("eval.run.run_eval") as m_run:
                m_run.return_value = {
                    "total": 2,
                    "hits": 0,
                    "recall_at_k": 0.0,
                    "k": 5,
                    "per_question": [],
                    "errors": [],
                }
                exit_code = main(["-g", golden_path, "-t", "0.7"])
                assert exit_code == 1

    def test_exits_2_when_golden_missing(self) -> None:
        exit_code = main(["-g", "/nonexistent/golden.jsonl"])
        assert exit_code == 2

    def test_exits_3_when_eval_crashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            golden_path = self._make_golden_file(tmp)

            with patch("eval.run.run_eval", side_effect=RuntimeError("crash")):
                exit_code = main(["-g", golden_path])
                assert exit_code == 3

    def test_json_flag_outputs_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            golden_path = self._make_golden_file(tmp)

            with patch("eval.run.run_eval") as m_run:
                m_run.return_value = {
                    "total": 1,
                    "hits": 1,
                    "recall_at_k": 1.0,
                    "k": 5,
                    "per_question": [
                        {
                            "id": "q01",
                            "question": "Q?",
                            "hit": True,
                            "detail": "a.py",
                            "retrieved": ["a.py"],
                        }
                    ],
                    "errors": [],
                }
                exit_code = main(["-g", golden_path, "--json"])
                captured = capsys.readouterr()
                assert exit_code == 0
                data = json.loads(captured.out)
                assert data["total"] == 1
                assert data["recall_at_k"] == 1.0

    def test_k_flag_passed_through(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            golden_path = self._make_golden_file(tmp)

            with patch("eval.run.run_eval") as m_run:
                m_run.return_value = {
                    "total": 2,
                    "hits": 1,
                    "recall_at_k": 0.5,
                    "k": 3,
                    "per_question": [],
                    "errors": [],
                }
                main(["-g", golden_path, "-k", "3"])
                call_kwargs = m_run.call_args[1]
                assert call_kwargs["k"] == 3

    def test_threshold_flag_respected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            golden_path = self._make_golden_file(tmp)

            with patch("eval.run.run_eval") as m_run:
                m_run.return_value = {
                    "total": 2,
                    "hits": 1,
                    "recall_at_k": 0.5,
                    "k": 5,
                    "per_question": [],
                    "errors": [],
                }
                # recall=0.5, threshold=0.4 → PASS
                exit_code = main(["-g", golden_path, "-t", "0.4"])
                assert exit_code == 0

    def test_human_readable_output_to_stdout(self, capsys: pytest.CaptureFixture[str]) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            golden_path = self._make_golden_file(tmp)

            with patch("eval.run.run_eval") as m_run:
                m_run.return_value = {
                    "total": 2,
                    "hits": 2,
                    "recall_at_k": 1.0,
                    "k": 5,
                    "per_question": [
                        {
                            "id": "q01",
                            "question": "Q1",
                            "hit": True,
                            "detail": "a.py",
                            "retrieved": ["a.py"],
                        },
                    ],
                    "errors": [],
                }
                main(["-g", golden_path])
                captured = capsys.readouterr()
                assert "Recall @ 5" in captured.out
                assert "PASS" in captured.out
