"""Tests for the Sentinel RAG source connectors (T1.3).

Each connector is exercised against a small fixture written into a tmp_path
so the tests are self-contained and do not depend on the live repo layout or
a running Postgres.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sentinel_rag.sources import Document, SourceConnector
from sentinel_rag.sources.code import CodeConnector
from sentinel_rag.sources.markdown import MarkdownConnector
from sentinel_rag.sources.postgres_incident import PostgresIncidentConnector
from sentinel_rag.sources.runbook import RunbookConnector

if TYPE_CHECKING:
    from pathlib import Path


# --------------------------------------------------------------------------- #
# base.Document
# --------------------------------------------------------------------------- #
def test_document_is_frozen_with_defaults():
    d = Document(
        doc_id="md:x",
        source_type="markdown",
        path="x",
        line_start=1,
        line_end=2,
        text="hi",
    )
    assert d.metadata == {}
    # Frozen dataclass — attribute assignment must raise.
    try:
        d.doc_id = "other"  # type: ignore[misc]
    except Exception:
        pass
    else:  # pragma: no cover
        raise AssertionError("Document should be frozen")


# --------------------------------------------------------------------------- #
# MarkdownConnector
# --------------------------------------------------------------------------- #
def test_markdown_connector_loads_files(tmp_path: Path):
    (tmp_path / "a.md").write_text("# Title A\n\nbody", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.md").write_text("no title here", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("not md", encoding="utf-8")

    docs = MarkdownConnector(tmp_path).load()
    ids = {d.doc_id for d in docs}
    assert ids == {"md:a.md", "md:sub/b.md"}
    a = next(d for d in docs if d.doc_id == "md:a.md")
    assert a.source_type == "markdown"
    assert a.line_start == 1
    assert a.line_end == 3
    assert a.metadata["title"] == "Title A"
    assert a.text.startswith("# Title A")


def test_markdown_connector_empty_dir(tmp_path: Path):
    assert MarkdownConnector(tmp_path).load() == []


def test_markdown_connector_missing_dir(tmp_path: Path):
    assert MarkdownConnector(tmp_path / "does-not-exist").load() == []


# --------------------------------------------------------------------------- #
# RunbookConnector
# --------------------------------------------------------------------------- #
def test_runbook_connector_parses_front_matter(tmp_path: Path):
    (tmp_path / "pod-crash.md").write_text(
        "---\ntitle: Pod Crash\nalert: PodCrash\nseverity: warning\nowner: sre\n---\n"
        "# Runbook: Pod Crash\n\nbody\n",
        encoding="utf-8",
    )
    docs = RunbookConnector(tmp_path).load()
    assert len(docs) == 1
    d = docs[0]
    assert d.source_type == "runbook"
    assert d.doc_id == "runbook:pod-crash.md"
    assert d.metadata["title"] == "Pod Crash"
    assert d.metadata["alert"] == "PodCrash"
    assert d.metadata["severity"] == "warning"
    assert d.metadata["owner"] == "sre"
    # Front matter stripped from text, body preserved.
    assert "---" not in d.text
    assert d.text.startswith("# Runbook: Pod Crash")


def test_runbook_connector_no_front_matter(tmp_path: Path):
    (tmp_path / "bare.md").write_text("# Bare runbook\n\nbody", encoding="utf-8")
    docs = RunbookConnector(tmp_path).load()
    assert len(docs) == 1
    assert docs[0].metadata["title"] == "bare"  # falls back to stem


def test_runbook_connector_missing_dir(tmp_path: Path):
    assert RunbookConnector(tmp_path / "nope").load() == []


# --------------------------------------------------------------------------- #
# CodeConnector
# --------------------------------------------------------------------------- #
def test_code_connector_filters_by_extension(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.go").write_text("package x\n", encoding="utf-8")
    (tmp_path / "c.ts").write_text("const x = 1\n", encoding="utf-8")
    (tmp_path / "ignore.txt").write_text("nope\n", encoding="utf-8")

    docs = CodeConnector(tmp_path).load()
    langs = {d.metadata["language"] for d in docs}
    assert langs == {"python", "go", "typescript"}
    paths = {d.path for d in docs}
    assert paths == {"a.py", "b.go", "c.ts"}
    assert all(d.source_type == "code" for d in docs)


def test_code_connector_skips_noise_dirs(tmp_path: Path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.min.js").write_text("minified", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.pyc").write_text("cache", encoding="utf-8")
    (tmp_path / "good.py").write_text("ok\n", encoding="utf-8")

    docs = CodeConnector(tmp_path).load()
    assert {d.path for d in docs} == {"good.py"}


def test_code_connector_multiple_roots(tmp_path: Path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "a.py").write_text("1\n", encoding="utf-8")
    (b / "b.py").write_text("2\n", encoding="utf-8")
    docs = CodeConnector(a, b).load()
    assert {d.path for d in docs} == {"a.py", "b.py"}


def test_code_connector_requires_root():
    try:
        CodeConnector()
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("CodeConnector() should require at least one root")


# --------------------------------------------------------------------------- #
# PostgresIncidentConnector
# --------------------------------------------------------------------------- #
def test_postgres_incident_sample_fallback(tmp_path: Path):
    sample = tmp_path / "incidents.json"
    sample.write_text(
        '[{"id": 1, "title": "OOM", "summary": "pod oom", '
        '"severity": "warning", "service": "api", "status": "resolved", '
        '"created_at": "2026-07-10T00:00:00Z"}]',
        encoding="utf-8",
    )
    # DSN points at a closed port; the connector must fall back to the sample.
    docs = PostgresIncidentConnector(
        dsn="postgresql://nobody:nobody@localhost:1/none",
        sample_path=sample,
    ).load()
    assert len(docs) == 1
    d = docs[0]
    assert d.source_type == "postgres_incident"
    assert d.doc_id == "incident:1"
    assert d.path == "postgres://postgres/incidents/1"
    assert d.metadata["severity"] == "warning"
    assert d.metadata["service"] == "api"
    assert "OOM" in d.text


def test_postgres_incident_no_sample_raises(tmp_path: Path):
    try:
        PostgresIncidentConnector(
            dsn="postgresql://nobody:nobody@localhost:1/none",
            sample_path=None,
        ).load()
    except Exception:
        pass
    else:  # pragma: no cover
        raise AssertionError("load() should raise when DB is down and no sample")


# --------------------------------------------------------------------------- #
# SourceConnector abstractness
# --------------------------------------------------------------------------- #
def test_source_connector_is_abstract():
    try:
        SourceConnector()  # type: ignore[abstract]
    except TypeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("SourceConnector must be abstract")
