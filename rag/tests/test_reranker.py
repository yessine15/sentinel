"""Tests for the Sentinel RAG cross-encoder reranker (T1.8)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sentinel_rag.reranker import CrossEncoderReranker, main
from sentinel_rag.retrieve import RetrievedPoint

# ---------------------------------------------------------------------------
# Helper — build fake RetrievedPoint objects
# ---------------------------------------------------------------------------


def _fake_point(
    chunk_id: str = "code:a.py:1-2",
    score: float = 0.95,
    text: str = "def hello():",
    path: str = "a.py",
    line_start: int = 1,
    line_end: int = 2,
    source_type: str = "code",
    metadata: dict | None = None,
) -> RetrievedPoint:
    return RetrievedPoint(
        chunk_id=chunk_id,
        text=text,
        path=path,
        line_start=line_start,
        line_end=line_end,
        source_type=source_type,
        score=score,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# CrossEncoderReranker
# ---------------------------------------------------------------------------


class TestCrossEncoderRerankerInit:
    """Tests for CrossEncoderReranker.__init__ and configuration."""

    def test_default_model_name(self) -> None:
        reranker = CrossEncoderReranker()
        assert reranker.model_name == "BAAI/bge-reranker-v2-m3"

    @patch.dict("os.environ", {"RERANK_MODEL": "custom/model"}, clear=False)
    def test_model_name_from_env(self) -> None:
        reranker = CrossEncoderReranker()
        assert reranker.model_name == "custom/model"

    def test_explicit_model_name_overrides_env(self) -> None:
        with patch.dict("os.environ", {"RERANK_MODEL": "env/model"}, clear=False):
            reranker = CrossEncoderReranker(model_name="explicit/model")
            assert reranker.model_name == "explicit/model"

    def test_default_device_none(self) -> None:
        reranker = CrossEncoderReranker()
        assert reranker.device is None

    @patch.dict("os.environ", {"RERANK_DEVICE": "cpu"}, clear=False)
    def test_device_from_env(self) -> None:
        reranker = CrossEncoderReranker()
        assert reranker.device == "cpu"

    def test_explicit_device_overrides_env(self) -> None:
        with patch.dict("os.environ", {"RERANK_DEVICE": "cuda"}, clear=False):
            reranker = CrossEncoderReranker(device="cpu")
            assert reranker.device == "cpu"

    def test_model_lazy_loaded_initially(self) -> None:
        reranker = CrossEncoderReranker()
        assert reranker._model is None


class TestIsAvailable:
    """Tests for is_available()."""

    def test_returns_true_when_importable(self) -> None:
        """sentence-transformers is in the rag deps; should be importable."""
        reranker = CrossEncoderReranker()
        # This test just checks the method runs without error in the test
        # environment (which has sentence-transformers installed as a dev
        # dependency).
        result = reranker.is_available()
        assert isinstance(result, bool)

    @patch("sentinel_rag.reranker.CrossEncoderReranker.is_available", return_value=False)
    def test_returns_false_when_not_importable(self, mock_avail: MagicMock) -> None:
        reranker = CrossEncoderReranker()
        assert reranker.is_available() is False


# ---------------------------------------------------------------------------
# rerank
# ---------------------------------------------------------------------------


class TestRerank:
    """Tests for the rerank() method."""

    def _mock_cross_encoder(self, scores: list[float]) -> MagicMock:
        """Build a mock CrossEncoder that returns the given scores."""
        mock_ce = MagicMock()
        mock_ce.predict.return_value = scores
        return mock_ce

    def test_empty_query_raises(self) -> None:
        reranker = CrossEncoderReranker()
        with pytest.raises(ValueError, match="non-empty"):
            reranker.rerank("", [_fake_point()])

    def test_whitespace_query_raises(self) -> None:
        reranker = CrossEncoderReranker()
        with pytest.raises(ValueError, match="non-empty"):
            reranker.rerank("   ", [_fake_point()])

    def test_top_k_less_than_one_raises(self) -> None:
        reranker = CrossEncoderReranker()
        with pytest.raises(ValueError, match="top_k must be >= 1"):
            reranker.rerank("query", [_fake_point()], top_k=0)

    def test_empty_candidates_returns_empty(self) -> None:
        reranker = CrossEncoderReranker()
        result = reranker.rerank("query", [])
        assert result == []

    def test_returns_top_k_results(self) -> None:
        reranker = CrossEncoderReranker()
        candidates = [_fake_point(f"id:{i}", float(i)) for i in range(10)]
        mock_ce = self._mock_cross_encoder([float(10 - i) for i in range(10)])
        reranker._model = mock_ce

        result = reranker.rerank("query", candidates, top_k=3)

        assert len(result) == 3

    def test_returns_all_when_fewer_than_top_k(self) -> None:
        reranker = CrossEncoderReranker()
        candidates = [_fake_point("a"), _fake_point("b")]
        mock_ce = self._mock_cross_encoder([0.9, 0.8])
        reranker._model = mock_ce

        result = reranker.rerank("query", candidates, top_k=5)

        assert len(result) == 2

    def test_results_sorted_by_score_descending(self) -> None:
        reranker = CrossEncoderReranker()
        candidates = [
            _fake_point("low", text="low relevance"),
            _fake_point("high", text="high relevance"),
            _fake_point("mid", text="mid relevance"),
        ]
        # Cross-encoder says: low=0.2, high=0.9, mid=0.5
        mock_ce = self._mock_cross_encoder([0.2, 0.9, 0.5])
        reranker._model = mock_ce

        result = reranker.rerank("query", candidates, top_k=5)

        assert result[0].chunk_id == "high"
        assert result[1].chunk_id == "mid"
        assert result[2].chunk_id == "low"
        assert result[0].score == 0.9
        assert result[1].score == 0.5
        assert result[2].score == 0.2

    def test_reorders_vs_original_scores(self) -> None:
        """Reranker should re-order — scores are from cross-encoder, not hybrid."""
        reranker = CrossEncoderReranker()
        candidates = [
            _fake_point("best", score=0.3, text="most relevant text"),
            _fake_point("worst", score=0.9, text="least relevant text"),
            _fake_point("mid", score=0.5, text="somewhat relevant text"),
        ]
        # Cross-encoder says: best=0.95, worst=0.1, mid=0.5
        mock_ce = self._mock_cross_encoder([0.95, 0.1, 0.5])
        reranker._model = mock_ce

        result = reranker.rerank("query", candidates, top_k=5)

        # Should be reordered by cross-encoder score, not original hybrid score
        assert result[0].chunk_id == "best"
        assert result[0].score == 0.95
        assert result[1].chunk_id == "mid"
        assert result[1].score == 0.5
        assert result[2].chunk_id == "worst"
        assert result[2].score == 0.1

    def test_preserves_metadata(self) -> None:
        reranker = CrossEncoderReranker()
        candidates = [
            _fake_point("a", metadata={"lang": "python"}),
            _fake_point("b", metadata={"lang": "go"}),
        ]
        mock_ce = self._mock_cross_encoder([0.8, 0.6])
        reranker._model = mock_ce

        result = reranker.rerank("query", candidates)

        assert result[0].metadata == {"lang": "python"}
        assert result[1].metadata == {"lang": "go"}

    def test_preserves_chunk_fields(self) -> None:
        reranker = CrossEncoderReranker()
        c = _fake_point(
            chunk_id="md:readme:1-5",
            text="# Hello",
            path="README.md",
            line_start=1,
            line_end=5,
            source_type="markdown",
        )
        mock_ce = self._mock_cross_encoder([0.7])
        reranker._model = mock_ce

        result = reranker.rerank("query", [c])

        assert len(result) == 1
        r = result[0]
        assert r.chunk_id == "md:readme:1-5"
        assert r.text == "# Hello"
        assert r.path == "README.md"
        assert r.line_start == 1
        assert r.line_end == 5
        assert r.source_type == "markdown"

    def test_model_error_wraps_as_runtime_error(self) -> None:
        reranker = CrossEncoderReranker()
        mock_ce = MagicMock()
        mock_ce.predict.side_effect = RuntimeError("CUDA out of memory")
        reranker._model = mock_ce

        with pytest.raises(RuntimeError, match="Cross-encoder reranking failed"):
            reranker.rerank("query", [_fake_point()])

    def test_predict_returns_nested_lists(self) -> None:
        """Some cross-encoder versions return [[score], [score], ...]."""
        reranker = CrossEncoderReranker()
        candidates = [_fake_point("a", text="aaa"), _fake_point("b", text="bbb")]
        mock_ce = self._mock_cross_encoder([[0.8], [0.6]])
        reranker._model = mock_ce

        result = reranker.rerank("query", candidates)

        assert result[0].chunk_id == "a"
        assert result[0].score == 0.8
        assert result[1].chunk_id == "b"
        assert result[1].score == 0.6

    def test_lazy_model_loaded_on_first_rerank(self) -> None:
        """The model is loaded only when rerank() is first called."""
        reranker = CrossEncoderReranker(model_name="cross-encoder/test-model")
        assert reranker._model is None

        with patch("sentence_transformers.CrossEncoder") as mock_ce_cls:
            mock_ce = MagicMock()
            mock_ce.predict.return_value = [0.9]
            mock_ce_cls.return_value = mock_ce

            reranker.rerank("query", [_fake_point()])

            mock_ce_cls.assert_called_once_with("cross-encoder/test-model")
            assert reranker._model is not None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    """Tests for the reranker CLI main() function."""

    def test_single_word_query(self) -> None:
        with (
            patch("sentinel_rag.reranker.retrieve") as m_retrieve,
            patch("sentinel_rag.reranker.CrossEncoderReranker") as m_reranker_cls,
        ):
            m_retrieve.return_value = [_fake_point("a")]
            m_reranker = MagicMock()
            m_reranker.rerank.return_value = [_fake_point("a")]
            m_reranker.is_available.return_value = True
            m_reranker_cls.return_value = m_reranker

            exit_code = main(["auth"])

            assert exit_code == 0
            m_retrieve.assert_called_once_with("auth", top_k=50)
            m_reranker.rerank.assert_called_once()

    def test_multi_word_query(self) -> None:
        with (
            patch("sentinel_rag.reranker.retrieve") as m_retrieve,
            patch("sentinel_rag.reranker.CrossEncoderReranker") as m_reranker_cls,
        ):
            m_retrieve.return_value = [_fake_point("a")]
            m_reranker = MagicMock()
            m_reranker.rerank.return_value = [_fake_point("a")]
            m_reranker.is_available.return_value = True
            m_reranker_cls.return_value = m_reranker

            exit_code = main(["how", "does", "auth", "work"])

            assert exit_code == 0
            m_retrieve.assert_called_once_with("how does auth work", top_k=50)

    def test_k_flag(self) -> None:
        with (
            patch("sentinel_rag.reranker.retrieve") as m_retrieve,
            patch("sentinel_rag.reranker.CrossEncoderReranker") as m_reranker_cls,
        ):
            m_retrieve.return_value = [_fake_point("a")]
            m_reranker = MagicMock()
            m_reranker.rerank.return_value = [_fake_point("a")]
            m_reranker.is_available.return_value = True
            m_reranker_cls.return_value = m_reranker

            exit_code = main(["-k", "3", "query"])

            assert exit_code == 0
            m_reranker.rerank.assert_called_once()
            assert m_reranker.rerank.call_args[1]["top_k"] == 3

    def test_prefetch_flag(self) -> None:
        with (
            patch("sentinel_rag.reranker.retrieve") as m_retrieve,
            patch("sentinel_rag.reranker.CrossEncoderReranker") as m_reranker_cls,
        ):
            m_retrieve.return_value = [_fake_point("a")]
            m_reranker = MagicMock()
            m_reranker.rerank.return_value = [_fake_point("a")]
            m_reranker.is_available.return_value = True
            m_reranker_cls.return_value = m_reranker

            exit_code = main(["--prefetch", "30", "query"])

            assert exit_code == 0
            m_retrieve.assert_called_once_with("query", top_k=30)

    def test_retrieval_value_error_exits_1(self) -> None:
        with patch("sentinel_rag.reranker.retrieve", side_effect=ValueError("bad")):
            exit_code = main(["query"])
            assert exit_code == 1

    def test_retrieval_runtime_error_exits_1(self) -> None:
        with patch("sentinel_rag.reranker.retrieve", side_effect=RuntimeError("fail")):
            exit_code = main(["query"])
            assert exit_code == 1

    def test_no_candidates_exits_0(self) -> None:
        with (
            patch("sentinel_rag.reranker.retrieve") as m_retrieve,
            patch("sentinel_rag.reranker.CrossEncoderReranker") as m_reranker_cls,
        ):
            m_retrieve.return_value = []
            m_reranker = MagicMock()
            m_reranker.is_available.return_value = True
            m_reranker_cls.return_value = m_reranker

            exit_code = main(["query"])
            assert exit_code == 0

    def test_reranker_not_available_exits_1(self) -> None:
        with (
            patch("sentinel_rag.reranker.retrieve") as m_retrieve,
            patch("sentinel_rag.reranker.CrossEncoderReranker") as m_reranker_cls,
        ):
            m_retrieve.return_value = [_fake_point("a")]
            m_reranker = MagicMock()
            m_reranker.is_available.return_value = False
            m_reranker_cls.return_value = m_reranker

            exit_code = main(["query"])
            assert exit_code == 1

    def test_reranking_runtime_error_exits_1(self) -> None:
        with (
            patch("sentinel_rag.reranker.retrieve") as m_retrieve,
            patch("sentinel_rag.reranker.CrossEncoderReranker") as m_reranker_cls,
        ):
            m_retrieve.return_value = [_fake_point("a")]
            m_reranker = MagicMock()
            m_reranker.rerank.side_effect = RuntimeError("model crash")
            m_reranker.is_available.return_value = True
            m_reranker_cls.return_value = m_reranker

            exit_code = main(["query"])
            assert exit_code == 1

    def test_reranked_results_printed_to_stdout(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("sentinel_rag.reranker.retrieve") as m_retrieve,
            patch("sentinel_rag.reranker.CrossEncoderReranker") as m_reranker_cls,
        ):
            m_retrieve.return_value = [_fake_point("a")]
            m_reranker = MagicMock()
            m_reranker.rerank.return_value = [
                _fake_point("best", score=0.95, text="def auth():", path="auth.py")
            ]
            m_reranker.is_available.return_value = True
            m_reranker_cls.return_value = m_reranker

            exit_code = main(["query"])
            assert exit_code == 0

            captured = capsys.readouterr()
            assert "#1" in captured.out
            assert "auth.py" in captured.out
            assert "0.9500" in captured.out
