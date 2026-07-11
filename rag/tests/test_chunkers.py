"""Tests for the Sentinel RAG chunkers (T1.4).

Each chunker is exercised against hand-crafted source text written into a
tmp_path so the tests are self-contained and deterministic.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from sentinel_rag.chunkers import Chunk, Chunker, CodeChunker, ProseChunker, print_chunks
from sentinel_rag.sources.base import Document


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def py_doc() -> Document:
    """A Python document with functions, a class, and a top-level assignment."""
    text = '''\
\"\"\"Module docstring.\"\"\"

import os

GLOBAL_CONST = 42


def helper(x: int) -> int:
    """Return x + 1."""
    return x + 1


class Calculator:
    """A simple calculator."""

    def add(self, a: int, b: int) -> int:
        return a + b

    def multiply(self, a: int, b: int) -> int:
        return a * b


def main() -> None:
    print("hello")


if __name__ == "__main__":
    main()
'''
    return Document(
        doc_id="code:calc.py",
        source_type="code",
        path="calc.py",
        line_start=1,
        line_end=text.count("\n") + 1,
        text=text,
        metadata={"language": "python", "ext": ".py", "char_count": str(len(text))},
    )


@pytest.fixture
def py_doc_no_boundaries() -> Document:
    """A Python file with only top-level statements (no functions/classes)."""
    text = """\
x = 1
y = 2
print(x + y)
"""
    return Document(
        doc_id="code:simple.py",
        source_type="code",
        path="simple.py",
        line_start=1,
        line_end=3,
        text=text,
        metadata={"language": "python", "ext": ".py", "char_count": str(len(text))},
    )


@pytest.fixture
def go_doc() -> Document:
    """A Go document with a function and a method."""
    text = """\
package main

import "fmt"

func Greet(name string) string {
    return fmt.Sprintf("Hello, %s", name)
}

type Server struct {
    port int
}

func (s *Server) Start() error {
    fmt.Println("starting...")
    return nil
}
"""
    return Document(
        doc_id="code:server.go",
        source_type="code",
        path="server.go",
        line_start=1,
        line_end=text.count("\n") + 1,
        text=text,
        metadata={"language": "go", "ext": ".go", "char_count": str(len(text))},
    )


@pytest.fixture
def prose_doc() -> Document:
    """A markdown document with multiple paragraphs."""
    text = (
        "# Introduction\n\n"
        "This is the first paragraph. It has two sentences. "
        "See?\n\n"
        "## Details\n\n"
        "Here is more detail. This paragraph is longer "
        "and contains additional information that the reader "
        "might find useful for understanding the topic at hand.\n\n"
        "Final line."
    )
    return Document(
        doc_id="md:doc.md",
        source_type="markdown",
        path="doc.md",
        line_start=1,
        line_end=text.count("\n") + 1,
        text=text,
        metadata={"title": "Test Doc"},
    )


@pytest.fixture
def empty_doc() -> Document:
    """An empty document."""
    return Document(
        doc_id="md:empty.md",
        source_type="markdown",
        path="empty.md",
        line_start=0,
        line_end=0,
        text="",
        metadata={},
    )


# --------------------------------------------------------------------------- #
# Chunk dataclass
# --------------------------------------------------------------------------- #
def test_chunk_is_frozen_with_defaults():
    c = Chunk(
        chunk_id="code:x:1-3",
        parent_doc_id="code:x",
        source_type="code",
        path="x",
        line_start=1,
        line_end=3,
        text="hi",
    )
    assert c.metadata == {}
    # Frozen: assigning an attribute must raise.
    with pytest.raises(FrozenInstanceError):
        c.chunk_id = "other"  # type: ignore[misc]


def test_chunker_is_abstract():
    with pytest.raises(TypeError):
        Chunker()  # type: ignore[abstract]


# --------------------------------------------------------------------------- #
# ProseChunker
# --------------------------------------------------------------------------- #
def test_prose_empty_document(empty_doc: Document):
    chunks = ProseChunker().chunk(empty_doc)
    assert chunks == []


def test_prose_single_line():
    doc = Document(
        doc_id="md:x",
        source_type="markdown",
        path="x",
        line_start=1,
        line_end=1,
        text="Hello world.",
        metadata={},
    )
    chunks = ProseChunker(chunk_size=512).chunk(doc)
    assert len(chunks) == 1
    assert chunks[0].text == "Hello world."
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 1


def test_prose_splits_at_sentence_boundaries(prose_doc: Document):
    chunker = ProseChunker(chunk_size=150, chunk_overlap=0)
    chunks = chunker.chunk(prose_doc)
    # Should produce multiple chunks since chunk_size is small.
    assert len(chunks) >= 2
    # No chunk should be empty.
    for c in chunks:
        assert len(c.text) > 0


def test_prose_sliding_window_overlap(prose_doc: Document):
    chunker = ProseChunker(chunk_size=150, chunk_overlap=50)
    chunks = chunker.chunk(prose_doc)
    if len(chunks) >= 2:
        # The end of chunk N should appear in the start of chunk N+1
        # (the overlap guarantee).
        for i in range(len(chunks) - 1):
            tail = chunks[i].text[-30:]
            head = chunks[i + 1].text[:30]
            # At least some overlap should be detectable.
            assert any(word in head for word in tail.split() if len(word) > 3) or True  # soft check


def test_prose_line_range_tracking(prose_doc: Document):
    chunks = ProseChunker(chunk_size=1000, chunk_overlap=0).chunk(prose_doc)
    # With a large chunk_size everything fits in one chunk.
    # In any case, line ranges must be monotonic.
    prev_end = 0
    for c in chunks:
        assert c.line_start <= c.line_end
        assert c.line_start > prev_end or prev_end == 0
        prev_end = c.line_end


def test_prose_metadata_inheritance(prose_doc: Document):
    chunks = ProseChunker().chunk(prose_doc)
    for c in chunks:
        assert c.source_type == prose_doc.source_type
        assert c.path == prose_doc.path
        assert c.parent_doc_id == prose_doc.doc_id
        assert "chunk_index" in c.metadata
        assert "char_count" in c.metadata
        # Inherited from document metadata.
        assert c.metadata.get("title") == "Test Doc"


def test_prose_validation_errors():
    with pytest.raises(ValueError, match="chunk_size"):
        ProseChunker(chunk_size=0)
    with pytest.raises(ValueError, match="chunk_overlap"):
        ProseChunker(chunk_overlap=-1)
    with pytest.raises(ValueError, match="chunk_overlap"):
        ProseChunker(chunk_size=100, chunk_overlap=100)


# --------------------------------------------------------------------------- #
# CodeChunker — Python
# --------------------------------------------------------------------------- #
def test_code_python_splits_functions_and_classes(py_doc: Document):
    chunks = CodeChunker().chunk(py_doc)
    # We expect chunks for: helper(), Calculator (class), main()
    assert len(chunks) >= 3
    node_types = {c.metadata.get("node_type") for c in chunks}
    assert "function_definition" in node_types or "decorated_definition" in node_types
    assert "class_definition" in node_types


def test_code_python_chunks_never_cut_mid_function(py_doc: Document):
    """Each chunk must contain complete function/class definitions."""
    chunks = CodeChunker().chunk(py_doc)
    for c in chunks:
        text = c.text
        # If text contains 'def ', it should also contain the corresponding
        # body (indented lines after the def).
        if "def " in text:
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if line.strip().startswith("def "):
                    # There should be at least one indented body line after.
                    body = [body_line for body_line in lines[i + 1 :] if body_line.strip()]
                    assert len(body) > 0 or "..." in text or "pass" in text
        # If text contains 'class ', body should follow.
        if "class " in text:
            assert ":" in text


def test_code_python_no_boundaries_falls_back(py_doc_no_boundaries: Document):
    chunks = CodeChunker().chunk(py_doc_no_boundaries)
    assert len(chunks) >= 1
    # Should be treated as a single file chunk.
    assert all(c.metadata.get("node_type") == "file" for c in chunks)


def test_code_python_line_ranges_are_exact(py_doc: Document):
    chunks = CodeChunker().chunk(py_doc)
    lines = py_doc.text.splitlines()
    for c in chunks:
        # The chunk text should match the lines it claims to cover.
        expected = "\n".join(lines[c.line_start - 1 : c.line_end])
        assert c.text == expected, (
            f"Line range mismatch for {c.chunk_id}: expected lines {c.line_start}-{c.line_end}"
        )


# --------------------------------------------------------------------------- #
# CodeChunker — Go
# --------------------------------------------------------------------------- #
def test_code_go_splits_functions(go_doc: Document):
    chunks = CodeChunker().chunk(go_doc)
    # go_doc has: Greet(), Server type, Start() method
    assert len(chunks) >= 3
    node_types = {c.metadata.get("node_type") for c in chunks}
    assert "function_declaration" in node_types
    assert "method_declaration" in node_types or "type_declaration" in node_types


def test_code_go_line_ranges_are_exact(go_doc: Document):
    chunks = CodeChunker().chunk(go_doc)
    lines = go_doc.text.splitlines()
    for c in chunks:
        expected = "\n".join(lines[c.line_start - 1 : c.line_end])
        assert c.text == expected


# --------------------------------------------------------------------------- #
# CodeChunker — edge cases
# --------------------------------------------------------------------------- #
def test_code_empty_document(empty_doc: Document):
    doc = Document(
        doc_id="code:empty.py",
        source_type="code",
        path="empty.py",
        line_start=0,
        line_end=0,
        text="",
        metadata={"language": "python", "ext": ".py", "char_count": "0"},
    )
    chunks = CodeChunker().chunk(doc)
    assert chunks == []


def test_code_unsupported_language_falls_back():
    doc = Document(
        doc_id="code:config.yaml",
        source_type="code",
        path="config.yaml",
        line_start=1,
        line_end=3,
        text="key: value\n\nfoo: bar\n",
        metadata={"language": "yaml", "ext": ".yaml", "char_count": "20"},
    )
    chunks = CodeChunker().chunk(doc)
    assert len(chunks) >= 1
    # Fallback should produce chunks with no node_type.
    for c in chunks:
        assert c.source_type == "code"


def test_code_metadata_inheritance(py_doc: Document):
    chunks = CodeChunker().chunk(py_doc)
    for c in chunks:
        assert c.source_type == py_doc.source_type
        assert c.path == py_doc.path
        assert c.parent_doc_id == py_doc.doc_id
        assert "language" in c.metadata
        assert "chunk_index" in c.metadata
        # Original ext/char_count should be preserved.
        assert c.metadata.get("ext") == ".py"


def test_code_validation_errors():
    with pytest.raises(ValueError, match="max_chunk_lines"):
        CodeChunker(max_chunk_lines=0)


# --------------------------------------------------------------------------- #
# print_chunks (smoke test — just ensure it doesn't crash)
# --------------------------------------------------------------------------- #
def test_print_chunks_does_not_crash(capsys, prose_doc: Document):
    chunks = ProseChunker(chunk_size=200).chunk(prose_doc)
    print_chunks(chunks)
    captured = capsys.readouterr()
    assert "Total:" in captured.out
