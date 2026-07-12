"""Tests for the Sentinel RAG citation renderer (T1.9)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sentinel_rag.citation import (
    format_source_marker,
    main,
    render_citation_block,
    render_citation_json,
    render_citation_text,
)
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
# format_source_marker
# ---------------------------------------------------------------------------


class TestFormatSourceMarker:
    """Tests for the format_source_marker() helper."""

    def test_basic_marker(self) -> None:
        source = _fake_point(path="api/main.py", line_start=42, line_end=58)
        assert format_source_marker(source) == "[api/main.py:42-58]"

    def test_multi_segment_path(self) -> None:
        source = _fake_point(path="a/b/c/d.py", line_start=10, line_end=20)
        assert format_source_marker(source) == "[a/b/c/d.py:10-20]"

    def test_single_line_chunk(self) -> None:
        source = _fake_point(path="x.py", line_start=5, line_end=5)
        assert format_source_marker(source) == "[x.py:5-5]"

    def test_non_retrieved_point_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="Expected RetrievedPoint"):
            format_source_marker("not a point")  # type: ignore[arg-type]

    def test_markdown_source(self) -> None:
        source = _fake_point(
            path="docs/runbooks/cpu.md",
            line_start=15,
            line_end=20,
            source_type="markdown",
        )
        assert format_source_marker(source) == "[docs/runbooks/cpu.md:15-20]"


# ---------------------------------------------------------------------------
# render_citation_json
# ---------------------------------------------------------------------------


class TestRenderCitationJson:
    """Tests for render_citation_json()."""

    def test_empty_sources(self) -> None:
        result = render_citation_json("Hello world", [])
        assert result == {"answer": "Hello world", "sources": []}

    def test_single_source(self) -> None:
        sources = [_fake_point(path="a.py", line_start=1, line_end=3, text="def f():")]
        result = render_citation_json("Answer [1]", sources)
        assert result["answer"] == "Answer [1]"
        assert len(result["sources"]) == 1
        assert result["sources"][0] == {
            "path": "a.py",
            "lines": "1-3",
            "snippet": "def f():",
        }

    def test_multiple_sources(self) -> None:
        sources = [
            _fake_point("a", path="x.py", line_start=1, line_end=2, text="aaa"),
            _fake_point("b", path="y.py", line_start=5, line_end=10, text="bbb"),
            _fake_point("c", path="z.py", line_start=20, line_end=25, text="ccc"),
        ]
        result = render_citation_json("Answer [1] [2] [3]", sources)
        assert len(result["sources"]) == 3
        assert result["sources"][0]["path"] == "x.py"
        assert result["sources"][0]["lines"] == "1-2"
        assert result["sources"][0]["snippet"] == "aaa"
        assert result["sources"][1]["path"] == "y.py"
        assert result["sources"][1]["lines"] == "5-10"
        assert result["sources"][2]["path"] == "z.py"
        assert result["sources"][2]["lines"] == "20-25"

    def test_answer_preserved_verbatim(self) -> None:
        answer = "The `/ping` endpoint [1] returns a 200 OK status [2]."
        sources = [
            _fake_point("a", text="def ping(): return 200"),
            _fake_point("b", text="status codes in api"),
        ]
        result = render_citation_json(answer, sources)
        assert result["answer"] == answer

    def test_lines_format(self) -> None:
        sources = [_fake_point("a", path="f.py", line_start=100, line_end=150)]
        result = render_citation_json("x", sources)
        assert result["sources"][0]["lines"] == "100-150"

    def test_non_string_answer_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="Expected str for answer"):
            render_citation_json(42, [])  # type: ignore[arg-type]

    def test_snippet_is_full_text(self) -> None:
        """The JSON snippet field preserves the full text (no truncation)."""
        long_text = "def this_is_a_very_long_function_name_that_goes_on():\n    pass\n"
        sources = [_fake_point("a", text=long_text)]
        result = render_citation_json("A", sources)
        assert result["sources"][0]["snippet"] == long_text


# ---------------------------------------------------------------------------
# render_citation_block
# ---------------------------------------------------------------------------


class TestRenderCitationBlock:
    """Tests for render_citation_block()."""

    def test_empty_sources_returns_empty_string(self) -> None:
        assert render_citation_block([]) == ""

    def test_single_source(self) -> None:
        sources = [_fake_point(path="a.py", line_start=1, line_end=3, text="def f():")]
        result = render_citation_block(sources)
        assert "**Sources:**" in result
        assert "[1] [a.py:1-3]" in result
        assert "def f():" in result

    def test_multiple_sources_numbered(self) -> None:
        sources = [
            _fake_point("a", path="x.py", line_start=1, line_end=2, text="first"),
            _fake_point("b", path="y.py", line_start=3, line_end=4, text="second"),
            _fake_point("c", path="z.py", line_start=5, line_end=6, text="third"),
        ]
        result = render_citation_block(sources)
        lines = result.split("\n")
        source_lines = [line for line in lines if line.startswith("[")]
        assert len(source_lines) == 3
        assert source_lines[0].startswith("[1]")
        assert source_lines[1].startswith("[2]")
        assert source_lines[2].startswith("[3]")

    def test_long_snippet_truncated(self) -> None:
        long_text = "x" * 200
        sources = [_fake_point("a", path="a.py", line_start=1, line_end=1, text=long_text)]
        result = render_citation_block(sources)
        # Should be truncated to 150 chars + …
        assert "…" in result
        assert len(long_text) > 150
        # The truncated snippet should be in the output (allow for flattening)
        assert "x" * 150 + "…" in result

    def test_newlines_flattened(self) -> None:
        text = "line1\nline2\nline3"
        sources = [_fake_point("a", text=text)]
        result = render_citation_block(sources)
        assert "\n" not in result.split(" — ")[1] if " — " in result else True
        # The snippet part should have ↵ instead of literal newlines
        snippet_part = result.split(" — ")[1] if " — " in result else ""
        assert " ↵ " in snippet_part

    def test_starts_with_thematic_break(self) -> None:
        sources = [_fake_point("a")]
        result = render_citation_block(sources)
        assert result.startswith("---")

    def test_non_retrieved_point_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="Expected RetrievedPoint"):
            render_citation_block(["not a point"])  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# render_citation_text
# ---------------------------------------------------------------------------


class TestRenderCitationText:
    """Tests for render_citation_text()."""

    def test_empty_sources_returns_just_answer(self) -> None:
        result = render_citation_text("Hello world", [])
        assert result == "Hello world"

    def test_single_source_appends_block(self) -> None:
        sources = [_fake_point("a", path="a.py", line_start=1, line_end=2, text="code")]
        result = render_citation_text("Answer [1]", sources)
        assert result.startswith("Answer [1]\n\n---")
        assert "**Sources:**" in result

    def test_multiple_sources(self) -> None:
        sources = [
            _fake_point("a", path="a.py", line_start=1, line_end=2, text="first"),
            _fake_point("b", path="b.py", line_start=3, line_end=4, text="second"),
        ]
        result = render_citation_text("Answer [1] and [2]", sources)
        assert result.startswith("Answer [1] and [2]\n\n---")
        assert "[1]" in result
        assert "[2]" in result

    def test_non_string_answer_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="Expected str for answer"):
            render_citation_text(42, [])  # type: ignore[arg-type]

    def test_answer_unchanged_when_no_sources(self) -> None:
        """With no sources, the answer is returned exactly as-is."""
        answer = "This is a complete answer without citations."
        result = render_citation_text(answer, [])
        assert result == answer

    def test_block_separated_by_blank_line(self) -> None:
        sources = [_fake_point("a")]
        result = render_citation_text("Answer", sources)
        assert "\n\n---" in result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    """Tests for the citation CLI main() function."""

    def test_single_word_query(self) -> None:
        with (
            patch("sentinel_rag.citation.retrieve") as m_retrieve,
            patch("sentinel_rag.citation.CrossEncoderReranker") as m_reranker_cls,
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
            patch("sentinel_rag.citation.retrieve") as m_retrieve,
            patch("sentinel_rag.citation.CrossEncoderReranker") as m_reranker_cls,
        ):
            m_retrieve.return_value = [_fake_point("a")]
            m_reranker = MagicMock()
            m_reranker.rerank.return_value = [_fake_point("a")]
            m_reranker.is_available.return_value = True
            m_reranker_cls.return_value = m_reranker

            exit_code = main(["how", "does", "auth", "work"])

            assert exit_code == 0
            m_retrieve.assert_called_once_with("how does auth work", top_k=50)

    def test_json_flag(self) -> None:
        with (
            patch("sentinel_rag.citation.retrieve") as m_retrieve,
            patch("sentinel_rag.citation.CrossEncoderReranker") as m_reranker_cls,
        ):
            m_retrieve.return_value = [
                _fake_point("a", path="x.py", line_start=1, line_end=2, text="code")
            ]
            m_reranker = MagicMock()
            m_reranker.rerank.return_value = [
                _fake_point("a", path="x.py", line_start=1, line_end=2, text="code")
            ]
            m_reranker.is_available.return_value = True
            m_reranker_cls.return_value = m_reranker

            exit_code = main(["--json", "query"])

            assert exit_code == 0

    def test_k_flag(self) -> None:
        with (
            patch("sentinel_rag.citation.retrieve") as m_retrieve,
            patch("sentinel_rag.citation.CrossEncoderReranker") as m_reranker_cls,
        ):
            m_retrieve.return_value = [_fake_point("a")]
            m_reranker = MagicMock()
            m_reranker.rerank.return_value = [_fake_point("a")]
            m_reranker.is_available.return_value = True
            m_reranker_cls.return_value = m_reranker

            exit_code = main(["-k", "3", "query"])

            assert exit_code == 0
            assert m_reranker.rerank.call_args[1]["top_k"] == 3

    def test_prefetch_flag(self) -> None:
        with (
            patch("sentinel_rag.citation.retrieve") as m_retrieve,
            patch("sentinel_rag.citation.CrossEncoderReranker") as m_reranker_cls,
        ):
            m_retrieve.return_value = [_fake_point("a")]
            m_reranker = MagicMock()
            m_reranker.rerank.return_value = [_fake_point("a")]
            m_reranker.is_available.return_value = True
            m_reranker_cls.return_value = m_reranker

            exit_code = main(["--prefetch", "30", "query"])

            assert exit_code == 0
            m_retrieve.assert_called_once_with("query", top_k=30)

    def test_retrieval_error_exit_1(self) -> None:
        with patch("sentinel_rag.citation.retrieve", side_effect=ValueError("bad query")):
            exit_code = main(["query"])
            assert exit_code == 1

    def test_runtime_error_exit_1(self) -> None:
        with patch("sentinel_rag.citation.retrieve", side_effect=RuntimeError("boom")):
            exit_code = main(["query"])
            assert exit_code == 1

    def test_no_candidates_exit_0(self) -> None:
        with (
            patch("sentinel_rag.citation.retrieve") as m_retrieve,
        ):
            m_retrieve.return_value = []
            exit_code = main(["query"])
            assert exit_code == 0

    def test_reranker_not_available_exit_1(self) -> None:
        with (
            patch("sentinel_rag.citation.retrieve") as m_retrieve,
            patch("sentinel_rag.citation.CrossEncoderReranker") as m_reranker_cls,
        ):
            m_retrieve.return_value = [_fake_point("a")]
            m_reranker = MagicMock()
            m_reranker.is_available.return_value = False
            m_reranker_cls.return_value = m_reranker

            exit_code = main(["query"])
            assert exit_code == 1

    def test_reranking_error_exit_1(self) -> None:
        with (
            patch("sentinel_rag.citation.retrieve") as m_retrieve,
            patch("sentinel_rag.citation.CrossEncoderReranker") as m_reranker_cls,
        ):
            m_retrieve.return_value = [_fake_point("a")]
            m_reranker = MagicMock()
            m_reranker.rerank.side_effect = RuntimeError("rerank failed")
            m_reranker.is_available.return_value = True
            m_reranker_cls.return_value = m_reranker

            exit_code = main(["query"])
            assert exit_code == 1

    def test_human_readable_output_to_stdout(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("sentinel_rag.citation.retrieve") as m_retrieve,
            patch("sentinel_rag.citation.CrossEncoderReranker") as m_reranker_cls,
        ):
            m_retrieve.return_value = [
                _fake_point("a", path="x.py", line_start=1, line_end=2, text="code")
            ]
            m_reranker = MagicMock()
            m_reranker.rerank.return_value = [
                _fake_point("a", path="x.py", line_start=1, line_end=2, text="code")
            ]
            m_reranker.is_available.return_value = True
            m_reranker_cls.return_value = m_reranker

            exit_code = main(["query"])
            captured = capsys.readouterr()

            assert exit_code == 0
            assert "Placeholder answer" in captured.out
            assert "Sources:" in captured.out

    def test_json_output_to_stdout(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("sentinel_rag.citation.retrieve") as m_retrieve,
            patch("sentinel_rag.citation.CrossEncoderReranker") as m_reranker_cls,
        ):
            m_retrieve.return_value = [
                _fake_point("a", path="x.py", line_start=1, line_end=2, text="code")
            ]
            m_reranker = MagicMock()
            m_reranker.rerank.return_value = [
                _fake_point("a", path="x.py", line_start=1, line_end=2, text="code")
            ]
            m_reranker.is_available.return_value = True
            m_reranker_cls.return_value = m_reranker

            exit_code = main(["--json", "query"])
            captured = capsys.readouterr()

            assert exit_code == 0
            assert '"answer"' in captured.out
            assert '"sources"' in captured.out
            assert '"path"' in captured.out

    def test_no_results_after_rerank_exit_0(self) -> None:
        with (
            patch("sentinel_rag.citation.retrieve") as m_retrieve,
            patch("sentinel_rag.citation.CrossEncoderReranker") as m_reranker_cls,
        ):
            m_retrieve.return_value = [_fake_point("a")]
            m_reranker = MagicMock()
            m_reranker.rerank.return_value = []
            m_reranker.is_available.return_value = True
            m_reranker_cls.return_value = m_reranker

            exit_code = main(["query"])
            assert exit_code == 0
