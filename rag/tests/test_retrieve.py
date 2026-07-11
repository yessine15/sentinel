"""Tests for the Sentinel RAG hybrid retriever (T1.7)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from qdrant_client.http.models.models import Fusion
from qdrant_client.models import FusionQuery, Prefetch, ScoredPoint

from sentinel_rag.ingest import DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME
from sentinel_rag.retrieve import (
    RetrievedPoint,
    _format_results,
    main,
    retrieve,
)
from sentinel_rag.sparse import sparse_query_vector

# ---------------------------------------------------------------------------
# Helper — fake embedder that returns dummy vectors (no HTTP calls)
# ---------------------------------------------------------------------------


class _FakeEmbedder:
    """Minimal fake embedder for retriever tests."""

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim

    @property
    def dimension(self) -> int:
        return self.dim

    def embed(self, text: str) -> list[float]:
        return [0.1] * self.dim


# ---------------------------------------------------------------------------
# Helper — build fake ScoredPoint objects mimicking Qdrant responses
# ---------------------------------------------------------------------------


def _fake_scored_point(
    chunk_id: str = "code:test.py:1-2",
    score: float = 0.95,
    text: str = "def hello():",
    path: str = "test.py",
    line_start: int = 1,
    line_end: int = 2,
    source_type: str = "code",
    metadata: dict | None = None,
) -> ScoredPoint:
    payload: dict = {
        "text": text,
        "path": path,
        "line_start": line_start,
        "line_end": line_end,
        "source_type": source_type,
        "parent_doc_id": "code:test.py",
    }
    if metadata:
        payload.update(metadata)
    return ScoredPoint(id=chunk_id, version=0, score=score, payload=payload)


# ---------------------------------------------------------------------------
# retrieve
# ---------------------------------------------------------------------------


class TestRetrieve:
    """Tests for the core retrieve() function."""

    def test_empty_query_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            retrieve("")

    def test_whitespace_query_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            retrieve("   ")

    def test_returns_empty_list_when_no_results(self) -> None:
        embedder = _FakeEmbedder()
        mock_client = MagicMock()
        mock_client.query_points.return_value.points = []

        results = retrieve("some query", embedder=embedder, client=mock_client)

        assert results == []
        mock_client.query_points.assert_called_once()

    def test_returns_retrieved_points(self) -> None:
        embedder = _FakeEmbedder()
        mock_client = MagicMock()
        mock_client.query_points.return_value.points = [
            _fake_scored_point("code:a.py:1-2", 0.95, "def a():", path="a.py"),
            _fake_scored_point("code:b.py:1-2", 0.80, "def b():", path="b.py"),
        ]

        results = retrieve("query", embedder=embedder, client=mock_client)

        assert len(results) == 2
        assert isinstance(results[0], RetrievedPoint)
        assert results[0].chunk_id == "code:a.py:1-2"
        assert results[0].score == 0.95
        assert results[0].text == "def a():"
        assert results[0].path == "a.py"
        assert results[0].source_type == "code"

    def test_respects_top_k(self) -> None:
        embedder = _FakeEmbedder()
        mock_client = MagicMock()
        mock_client.query_points.return_value.points = [
            _fake_scored_point(f"id:{i}", float(1.0 - i * 0.1)) for i in range(5)
        ]

        results = retrieve("query", embedder=embedder, client=mock_client, top_k=5)

        assert len(results) == 5
        # Verify limit was passed through
        call_kwargs = mock_client.query_points.call_args[1]
        assert call_kwargs["limit"] == 5

    def test_prefetch_limit_configurable(self) -> None:
        embedder = _FakeEmbedder()
        mock_client = MagicMock()
        mock_client.query_points.return_value.points = []

        retrieve("query", embedder=embedder, client=mock_client, prefetch_limit=200)

        call = mock_client.query_points.call_args
        assert call[1]["prefetch"][0].limit == 200
        assert call[1]["prefetch"][1].limit == 200

    def test_rrf_fusion_used(self) -> None:
        """Verify the query uses RRF fusion (not a plain nearest-neighbor)."""
        embedder = _FakeEmbedder()
        mock_client = MagicMock()
        mock_client.query_points.return_value.points = []

        retrieve("query", embedder=embedder, client=mock_client)

        call = mock_client.query_points.call_args
        fusion_query = call[1]["query"]
        assert isinstance(fusion_query, FusionQuery)
        assert fusion_query.fusion == Fusion.RRF

    def test_two_prefetch_queries(self) -> None:
        """Verify both dense and sparse prefetch queries are issued."""
        embedder = _FakeEmbedder()
        mock_client = MagicMock()
        mock_client.query_points.return_value.points = []

        retrieve("hello", embedder=embedder, client=mock_client)

        call = mock_client.query_points.call_args
        prefetches: list[Prefetch] = call[1]["prefetch"]
        assert len(prefetches) == 2
        # First prefetch: dense
        assert prefetches[0].using == DENSE_VECTOR_NAME
        assert prefetches[0].query == [0.1] * 8
        # Second prefetch: sparse (qdrant-client may normalise to SparseVector)
        assert prefetches[1].using == SPARSE_VECTOR_NAME
        sv = prefetches[1].query
        # Accept both dict and SparseVector model (pydantic coercion)
        if isinstance(sv, dict):
            assert "indices" in sv and "values" in sv
        else:
            assert hasattr(sv, "indices") and hasattr(sv, "values")

    def test_sparse_query_matches_sparse_query_vector(self) -> None:
        """The sparse prefetch uses the same encoding as sparse_query_vector."""
        query_text = "hello world test"
        embedder = _FakeEmbedder()
        mock_client = MagicMock()
        mock_client.query_points.return_value.points = []

        retrieve(query_text, embedder=embedder, client=mock_client)

        call = mock_client.query_points.call_args
        actual_sparse = call[1]["prefetch"][1].query
        expected_sparse = sparse_query_vector(query_text)
        # qdrant-client may have auto-converted dict to SparseVector model
        if hasattr(actual_sparse, "indices"):
            assert actual_sparse.indices == expected_sparse["indices"]  # type: ignore[union-attr]
            assert actual_sparse.values == expected_sparse["values"]  # type: ignore[union-attr]
        else:
            assert actual_sparse == expected_sparse

    def test_payload_metadata_stripped_correctly(self) -> None:
        """Metadata excludes fields that are top-level RetrievedPoint attrs."""
        embedder = _FakeEmbedder()
        mock_client = MagicMock()
        mock_client.query_points.return_value.points = [
            _fake_scored_point(
                chunk_id="code:main.py:5-10",
                metadata={"language": "python", "node_type": "function_definition"},
            )
        ]

        results = retrieve("query", embedder=embedder, client=mock_client)
        r = results[0]
        assert r.metadata == {"language": "python", "node_type": "function_definition"}
        # These should not be in metadata
        assert "text" not in r.metadata
        assert "path" not in r.metadata
        assert "parent_doc_id" not in r.metadata

    def test_qdrant_error_wraps_as_runtime_error(self) -> None:
        embedder = _FakeEmbedder()
        mock_client = MagicMock()
        mock_client.query_points.side_effect = ConnectionError("no route to host")

        with pytest.raises(RuntimeError, match="Qdrant hybrid query failed"):
            retrieve("query", embedder=embedder, client=mock_client)

    def test_default_params_use_factories(self) -> None:
        """When embedder/client are None, factories are called."""
        with (
            patch("sentinel_rag.retrieve.get_embedder") as m_ge,
            patch("sentinel_rag.retrieve._get_qdrant_client") as m_gqc,
        ):
            m_ge.return_value = _FakeEmbedder()
            m_gqc.return_value = MagicMock()
            m_gqc.return_value.query_points.return_value.points = []

            retrieve("test query")

            m_ge.assert_called_once()
            m_gqc.assert_called_once()


# ---------------------------------------------------------------------------
# RetrievedPoint dataclass
# ---------------------------------------------------------------------------


class TestRetrievedPoint:
    def test_default_metadata_empty(self) -> None:
        rp = RetrievedPoint(
            chunk_id="x",
            text="t",
            path="p",
            line_start=1,
            line_end=2,
            source_type="code",
            score=0.5,
        )
        assert rp.metadata == {}

    def test_all_fields_accessible(self) -> None:
        rp = RetrievedPoint(
            chunk_id="md:readme.md:1-10",
            text="# Hello",
            path="readme.md",
            line_start=1,
            line_end=10,
            source_type="markdown",
            score=0.99,
            metadata={"title": "README"},
        )
        assert rp.chunk_id == "md:readme.md:1-10"
        assert rp.text == "# Hello"
        assert rp.path == "readme.md"
        assert rp.line_start == 1
        assert rp.line_end == 10
        assert rp.source_type == "markdown"
        assert rp.score == 0.99
        assert rp.metadata == {"title": "README"}


# ---------------------------------------------------------------------------
# _format_results
# ---------------------------------------------------------------------------


class TestFormatResults:
    def test_empty_results_prints_to_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        _format_results([])
        captured = capsys.readouterr()
        assert "No results found" in captured.err

    def test_non_empty_results_prints_to_stdout(self, capsys: pytest.CaptureFixture[str]) -> None:
        results = [
            RetrievedPoint(
                chunk_id="code:a.py:1-2",
                text="def a():",
                path="a.py",
                line_start=1,
                line_end=2,
                source_type="code",
                score=0.95,
            )
        ]
        _format_results(results)
        captured = capsys.readouterr()
        assert "a.py:1-2" in captured.out
        assert "0.9500" in captured.out
        assert "def a():" in captured.out


# ---------------------------------------------------------------------------
# CLI (main)
# ---------------------------------------------------------------------------


class TestCLI:
    def test_main_single_word_query(self) -> None:
        with patch("sentinel_rag.retrieve.retrieve") as mock_retrieve:
            mock_retrieve.return_value = []
            result = main(["hello"])
            mock_retrieve.assert_called_once_with("hello", top_k=50)
            assert result == 0

    def test_main_multi_word_query(self) -> None:
        with patch("sentinel_rag.retrieve.retrieve") as mock_retrieve:
            mock_retrieve.return_value = []
            result = main(["how", "does", "auth", "work"])
            mock_retrieve.assert_called_once_with("how does auth work", top_k=50)
            assert result == 0

    def test_main_top_k_flag(self) -> None:
        with patch("sentinel_rag.retrieve.retrieve") as mock_retrieve:
            mock_retrieve.return_value = []
            result = main(["-k", "10", "test query"])
            mock_retrieve.assert_called_once_with("test query", top_k=10)
            assert result == 0

    def test_main_value_error_returns_1(self) -> None:
        with patch("sentinel_rag.retrieve.retrieve") as mock_retrieve:
            mock_retrieve.side_effect = ValueError("bad query")
            result = main([" "])
            assert result == 1

    def test_main_runtime_error_returns_1(self) -> None:
        with patch("sentinel_rag.retrieve.retrieve") as mock_retrieve:
            mock_retrieve.side_effect = RuntimeError("Qdrant down")
            result = main(["test"])
            assert result == 1
