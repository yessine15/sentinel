"""Tests for the Sentinel RAG ingest CLI (T1.6)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from qdrant_client.models import PointStruct, SparseVector

from sentinel_rag.chunkers.base import Chunk
from sentinel_rag.chunkers.code import CodeChunker
from sentinel_rag.chunkers.prose import ProseChunker
from sentinel_rag.embed import OllamaEmbedder
from sentinel_rag.ingest import (
    COLLECTION_NAME,
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    SparseEncoder,
    _build_points,
    _ensure_collection,
    _ensure_collection_if_missing,
    _get_qdrant_client,
    ingest,
    ingest_code,
    ingest_markdown,
    ingest_postmortem,
    ingest_runbook,
    main,
)
from sentinel_rag.sources.base import Document

# ---------------------------------------------------------------------------
# Helpers — fake objects used across tests
# ---------------------------------------------------------------------------


def _fake_document(
    doc_id: str = "code:test.py",
    text: str = "def hello():\n    return 'world'\n",
    source_type: str = "code",
    path: str = "test.py",
) -> Document:
    return Document(
        doc_id=doc_id,
        source_type=source_type,
        path=path,
        line_start=1,
        line_end=len(text.splitlines()),
        text=text,
        metadata={"language": "python", "ext": ".py"},
    )


def _fake_chunk(
    chunk_id: str = "code:test.py:1-2",
    text: str = "def hello():",
    source_type: str = "code",
    path: str = "test.py",
) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        parent_doc_id="code:test.py",
        source_type=source_type,
        path=path,
        line_start=1,
        line_end=2,
        text=text,
        metadata={"language": "python", "node_type": "function_definition"},
    )


class _FakeEmbedder(OllamaEmbedder):
    """Embedder that returns dummy float vectors without hitting HTTP."""

    def __init__(self) -> None:
        super().__init__()
        self._dim = 8  # tiny for tests

    @property
    def dimension(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        return [0.1] * self._dim

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * self._dim for _ in texts]


# ---------------------------------------------------------------------------
# SparseEncoder
# ---------------------------------------------------------------------------


class TestSparseEncoder:
    """Tests for the corpus-wide TF-IDF sparse encoder."""

    def test_fit_builds_vocabulary(self) -> None:
        enc = SparseEncoder()
        enc.fit(["hello world", "hello again"])
        assert "hello" in enc._vocab
        assert "world" in enc._vocab
        assert "again" in enc._vocab

    def test_fit_counts_document_frequencies(self) -> None:
        enc = SparseEncoder()
        enc.fit(["hello world", "hello again", "just world"])
        assert enc._df["hello"] == 2  # appears in doc 0 and 1
        assert enc._df["world"] == 2  # appears in doc 0 and 2
        assert enc._df["again"] == 1
        assert enc._df["just"] == 1
        assert enc._N == 3

    def test_encode_returns_qdrant_format(self) -> None:
        enc = SparseEncoder()
        enc.fit(["hello world", "hello again"])
        result = enc.encode("hello world")
        assert "indices" in result
        assert "values" in result
        assert isinstance(result["indices"], list)
        assert isinstance(result["values"], list)
        assert len(result["indices"]) == len(result["values"])

    def test_encode_empty_text(self) -> None:
        enc = SparseEncoder()
        enc.fit(["hello world"])
        result = enc.encode("")
        assert result == {"indices": [], "values": []}

    def test_encode_whitespace_only(self) -> None:
        enc = SparseEncoder()
        enc.fit(["hello world"])
        result = enc.encode("   \n\t  ")
        assert result == {"indices": [], "values": []}

    def test_encode_out_of_vocab_token_skipped(self) -> None:
        enc = SparseEncoder()
        enc.fit(["hello world"])
        result = enc.encode("hello unknown")
        # "unknown" is OOV — only "hello" should appear
        assert len(result["indices"]) == 1
        # Index is hash-based (crc32), just verify it's a positive int
        assert isinstance(result["indices"][0], int)
        assert result["indices"][0] >= 0

    def test_tokenize_lowercases_and_splits(self) -> None:
        tokens = SparseEncoder._tokenize("Hello, World! 123 foo-bar_baz")
        assert tokens == ["hello", "world", "123", "foo", "bar", "baz"]

    def test_idf_higher_for_rare_tokens(self) -> None:
        enc = SparseEncoder()
        enc.fit(["common common rare", "common common", "common common rare"])
        # "common" appears in all 3 docs, "rare" in 2 of 3 — rare gets higher IDF
        common_result = enc.encode("common")
        rare_result = enc.encode("rare")

        # Both should have exactly one entry since each text has one unique token
        assert len(common_result["indices"]) == 1
        assert len(rare_result["indices"]) == 1
        # Rare token should have higher IDF → higher value
        assert rare_result["values"][0] > common_result["values"][0]


# ---------------------------------------------------------------------------
# _build_points
# ---------------------------------------------------------------------------


class TestBuildPoints:
    """Tests for PointStruct assembly."""

    def test_single_chunk(self) -> None:
        chunks = [_fake_chunk("code:test.py:1-2", "def hello():")]
        vectors = [[0.1] * 8]
        enc = SparseEncoder()
        enc.fit(["def hello():"])

        points = _build_points(chunks, vectors, enc)

        assert len(points) == 1
        p = points[0]
        assert isinstance(p, PointStruct)
        # Chunk IDs are now UUIDs (derived from the original chunk_id).
        import uuid
        assert p.id == str(uuid.uuid5(uuid.NAMESPACE_DNS, "code:test.py:1-2"))
        assert DENSE_VECTOR_NAME in p.vector  # type: ignore[index]
        assert SPARSE_VECTOR_NAME in p.vector  # type: ignore[index]
        assert p.vector[DENSE_VECTOR_NAME] == [0.1] * 8  # type: ignore[index]
        assert p.payload["text"] == "def hello():"  # type: ignore[index]
        assert p.payload["path"] == "test.py"  # type: ignore[index]
        assert p.payload["source_type"] == "code"  # type: ignore[index]
        assert p.payload["language"] == "python"  # type: ignore[index]

    def test_multiple_chunks(self) -> None:
        import uuid

        chunks = [
            _fake_chunk("code:a.py:1-2", "def a():"),
            _fake_chunk("code:b.py:1-2", "def b():"),
        ]
        vectors = [[0.1] * 8, [0.2] * 8]
        enc = SparseEncoder()
        enc.fit(["def a():", "def b():"])

        points = _build_points(chunks, vectors, enc)

        assert len(points) == 2
        assert points[0].id == str(uuid.uuid5(uuid.NAMESPACE_DNS, "code:a.py:1-2"))
        assert points[1].id == str(uuid.uuid5(uuid.NAMESPACE_DNS, "code:b.py:1-2"))

    def test_sparse_vector_present(self) -> None:
        chunks = [_fake_chunk(text="hello world")]
        vectors = [[0.1] * 8]
        enc = SparseEncoder()
        enc.fit(["hello world"])

        points = _build_points(chunks, vectors, enc)
        sparse = points[0].vector[SPARSE_VECTOR_NAME]  # type: ignore[index]
        # qdrant-client converts the dict to a SparseVector model on upsert.
        assert isinstance(sparse, SparseVector)
        assert len(sparse.indices) > 0
        assert len(sparse.values) > 0


# ---------------------------------------------------------------------------
# _get_qdrant_client
# ---------------------------------------------------------------------------


class TestGetQdrantClient:
    """Tests for QdrantClient construction."""

    def test_default_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("QDRANT_URL", raising=False)
        monkeypatch.delenv("QDRANT_API_KEY", raising=False)
        with patch("sentinel_rag.ingest.QdrantClient") as mock_client:
            _get_qdrant_client()
            # In-cluster default: k8s service DNS (matches the deployed
            # Qdrant; local dev overrides via QDRANT_URL).
            mock_client.assert_called_once_with(url="http://qdrant.qdrant.svc:6333")

    def test_custom_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
        monkeypatch.delenv("QDRANT_API_KEY", raising=False)
        with patch("sentinel_rag.ingest.QdrantClient") as mock_client:
            _get_qdrant_client()
            mock_client.assert_called_once_with(url="http://qdrant:6333")

    def test_with_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QDRANT_URL", "https://cloud.qdrant.io")
        monkeypatch.setenv("QDRANT_API_KEY", "secret-123")
        with patch("sentinel_rag.ingest.QdrantClient") as mock_client:
            _get_qdrant_client()
            mock_client.assert_called_once_with(url="https://cloud.qdrant.io", api_key="secret-123")


# ---------------------------------------------------------------------------
# _ensure_collection
# ---------------------------------------------------------------------------


class TestEnsureCollection:
    """Tests for collection creation / recreation."""

    def test_creates_when_missing(self) -> None:
        mock_client = MagicMock()
        mock_client.collection_exists.return_value = False

        _ensure_collection(mock_client, 1024)

        mock_client.collection_exists.assert_called_once_with(COLLECTION_NAME)
        mock_client.delete_collection.assert_not_called()
        mock_client.create_collection.assert_called_once()
        create_args = mock_client.create_collection.call_args
        assert create_args[1]["collection_name"] == COLLECTION_NAME
        assert DENSE_VECTOR_NAME in create_args[1]["vectors_config"]

    def test_recreates_when_exists(self) -> None:
        mock_client = MagicMock()
        mock_client.collection_exists.return_value = True

        _ensure_collection(mock_client, 768)

        mock_client.delete_collection.assert_called_once_with(COLLECTION_NAME)
        mock_client.create_collection.assert_called_once()

    def test_passes_dense_dimension(self) -> None:
        mock_client = MagicMock()
        mock_client.collection_exists.return_value = False

        _ensure_collection(mock_client, 1536)

        call = mock_client.create_collection.call_args
        vp = call[1]["vectors_config"][DENSE_VECTOR_NAME]
        assert vp.size == 1536

    def test_sparse_vector_config_included(self) -> None:
        mock_client = MagicMock()
        mock_client.collection_exists.return_value = False

        _ensure_collection(mock_client, 1024)

        call = mock_client.create_collection.call_args
        assert SPARSE_VECTOR_NAME in call[1]["sparse_vectors_config"]


# ---------------------------------------------------------------------------
# ingest pipeline
# ---------------------------------------------------------------------------


class TestIngestPipeline:
    """Integration-style tests for the full ingest() pipeline."""

    def test_full_pipeline_single_doc(self) -> None:
        doc = _fake_document()
        chunker = CodeChunker()
        embedder = _FakeEmbedder()
        mock_client = MagicMock()

        count = ingest([doc], chunker, embedder, mock_client)

        assert count > 0
        mock_client.upsert.assert_called_once()
        upsert_args = mock_client.upsert.call_args
        assert upsert_args[1]["collection_name"] == COLLECTION_NAME
        points = upsert_args[1]["points"]
        assert len(points) > 0
        for p in points:
            assert DENSE_VECTOR_NAME in p.vector
            assert SPARSE_VECTOR_NAME in p.vector

    def test_full_pipeline_multiple_docs(self) -> None:
        docs = [
            _fake_document("code:a.py", "def a():\n    pass\n"),
            _fake_document("code:b.py", "def b():\n    pass\n"),
        ]
        chunker = CodeChunker()
        embedder = _FakeEmbedder()
        mock_client = MagicMock()

        count = ingest(docs, chunker, embedder, mock_client)

        assert count > 0
        mock_client.upsert.assert_called_once()

    def test_empty_documents(self) -> None:
        embedder = _FakeEmbedder()
        mock_client = MagicMock()

        count = ingest([], CodeChunker(), embedder, mock_client)

        assert count == 0
        mock_client.upsert.assert_not_called()

    def test_docs_with_no_text_produce_no_chunks(self) -> None:
        doc = _fake_document(text="")
        chunker = CodeChunker()
        embedder = _FakeEmbedder()
        mock_client = MagicMock()

        count = ingest([doc], chunker, embedder, mock_client)

        assert count == 0
        mock_client.upsert.assert_not_called()

    def test_prose_chunker_pipeline(self) -> None:
        doc = _fake_document(
            doc_id="md:readme.md",
            text="# Hello\n\nThis is a paragraph. It has multiple sentences. Here is another one.",
            source_type="markdown",
            path="readme.md",
        )
        chunker = ProseChunker(chunk_size=128, chunk_overlap=16)
        embedder = _FakeEmbedder()
        mock_client = MagicMock()

        count = ingest([doc], chunker, embedder, mock_client)

        assert count > 0
        mock_client.upsert.assert_called_once()

    def test_sparse_vector_has_valid_format(self) -> None:
        doc = _fake_document()
        chunker = CodeChunker()
        embedder = _FakeEmbedder()
        mock_client = MagicMock()

        ingest([doc], chunker, embedder, mock_client)

        points = mock_client.upsert.call_args[1]["points"]
        for p in points:
            sv = p.vector[SPARSE_VECTOR_NAME]
            # qdrant-client normalises sparse vectors to SparseVector models.
            assert isinstance(sv, SparseVector)
            assert len(sv.indices) == len(sv.values)


# ---------------------------------------------------------------------------
# Subcommand handlers (with mocked deps)
# ---------------------------------------------------------------------------


class TestSubcommandHandlers:
    """Tests for ingest_code / ingest_markdown / ingest_runbook."""

    @pytest.fixture
    def mock_deps(self) -> dict[str, MagicMock]:
        """Mock all external dependencies for subcommand handlers."""
        with (
            patch("sentinel_rag.ingest._get_qdrant_client") as m_q,
            patch("sentinel_rag.ingest._ensure_collection") as m_ec,
            patch("sentinel_rag.ingest.get_embedder") as m_ge,
        ):
            m_q.return_value = MagicMock()
            m_ge.return_value = _FakeEmbedder()
            yield {"qdrant": m_q, "ensure": m_ec, "embedder": m_ge}

    def test_ingest_code_calls_pipeline(self, mock_deps: dict, tmp_path: MagicMock) -> None:
        # Create a temp file so the connector has something to load.
        src = tmp_path / "test.py"
        src.write_text("def hello():\n    return 42\n")

        count = ingest_code([str(tmp_path)])

        assert count > 0
        mock_deps["ensure"].assert_called_once()
        mock_deps["qdrant"].return_value.upsert.assert_called_once()

    def test_ingest_code_empty_dir(self, mock_deps: dict, tmp_path: MagicMock) -> None:
        # tmp_path is empty (no code files).
        count = ingest_code([str(tmp_path)])
        assert count == 0
        mock_deps["qdrant"].return_value.upsert.assert_not_called()

    def test_ingest_markdown(self, mock_deps: dict, tmp_path: MagicMock) -> None:
        md = tmp_path / "readme.md"
        md.write_text("# Hello\nWorld.")

        count = ingest_markdown([str(tmp_path)])

        assert count > 0
        mock_deps["qdrant"].return_value.upsert.assert_called_once()

    def test_ingest_markdown_requires_one_path(self) -> None:
        assert ingest_markdown([]) == 1
        assert ingest_markdown(["a", "b"]) == 1

    def test_ingest_runbook(self, mock_deps: dict, tmp_path: MagicMock) -> None:
        rb = tmp_path / "alert.md"
        rb.write_text("---\ntitle: Test\n---\n# Runbook\n\nRestart the pod.")

        count = ingest_runbook([str(tmp_path)])

        assert count > 0
        mock_deps["qdrant"].return_value.upsert.assert_called_once()

    def test_ingest_runbook_requires_one_path(self) -> None:
        assert ingest_runbook([]) == 1
        assert ingest_runbook(["a", "b"]) == 1


# ---------------------------------------------------------------------------
# Incremental postmortem ingestion (T3.12)
# ---------------------------------------------------------------------------


class TestIngestPostmortem:
    """ingest_postmortem adds a writeup WITHOUT wiping the collection."""

    _PM_TEXT = (
        "# Postmortem — pod crash\n\n"
        "## Summary\n\nPod demo-api OOMKilled 12 times.\n\n"
        "## Incident\n\nALERTS: kube_pod_oom demo-api\n\n"
        "## Remediation plan\n\nRaise memory limit 1Gi -> 2Gi.\n"
    )

    def test_upserts_postmortem_chunks(self) -> None:
        mock_client = MagicMock()
        mock_client.collection_exists.return_value = True
        with (
            patch("sentinel_rag.ingest.get_embedder", return_value=_FakeEmbedder()),
            patch("sentinel_rag.ingest._get_qdrant_client", return_value=mock_client),
        ):
            count = ingest_postmortem(
                title="Postmortem — pod crash",
                content=self._PM_TEXT,
                plan_id="plan-1",
            )

        assert count > 0
        mock_client.upsert.assert_called_once()
        points = mock_client.upsert.call_args[1]["points"]
        for p in points:
            assert p.payload["source_type"] == "postmortem"
            assert p.payload["plan_id"] == "plan-1"
            assert p.payload["title"] == "Postmortem — pod crash"
        # Existing collection is reused — no delete_collection call.
        mock_client.delete_collection.assert_not_called()
        mock_client.create_collection.assert_not_called()

    def test_creates_collection_if_missing(self) -> None:
        mock_client = MagicMock()
        mock_client.collection_exists.return_value = False
        with (
            patch("sentinel_rag.ingest.get_embedder", return_value=_FakeEmbedder()),
            patch("sentinel_rag.ingest._get_qdrant_client", return_value=mock_client),
        ):
            ingest_postmortem(title="t", content=self._PM_TEXT, plan_id="p2")

        mock_client.create_collection.assert_called_once()
        mock_client.upsert.assert_called_once()

    def test_doc_id_stable_per_plan(self) -> None:
        """Re-ingesting the same plan overwrites the same points (idempotent)."""
        mock_client = MagicMock()
        mock_client.collection_exists.return_value = True
        with (
            patch("sentinel_rag.ingest.get_embedder", return_value=_FakeEmbedder()),
            patch("sentinel_rag.ingest._get_qdrant_client", return_value=mock_client),
        ):
            ingest_postmortem(title="t", content=self._PM_TEXT, plan_id="plan-1")
            ids_1 = [p.id for p in mock_client.upsert.call_args[1]["points"]]
            ingest_postmortem(title="t", content=self._PM_TEXT, plan_id="plan-1")
            ids_2 = [p.id for p in mock_client.upsert.call_args[1]["points"]]

        assert ids_1 == ids_2

    def test_empty_content_returns_zero(self) -> None:
        mock_client = MagicMock()
        mock_client.collection_exists.return_value = True
        with (
            patch("sentinel_rag.ingest.get_embedder", return_value=_FakeEmbedder()),
            patch("sentinel_rag.ingest._get_qdrant_client", return_value=mock_client),
        ):
            count = ingest_postmortem(title="t", content="", plan_id="p3")

        assert count == 0
        mock_client.upsert.assert_not_called()


class TestEnsureCollectionIfMissing:
    """_ensure_collection_if_missing never wipes an existing collection."""

    def test_skips_when_collection_exists(self) -> None:
        mock_client = MagicMock()
        mock_client.collection_exists.return_value = True

        _ensure_collection_if_missing(mock_client, dense_dim=8)

        mock_client.create_collection.assert_not_called()
        mock_client.delete_collection.assert_not_called()

    def test_creates_when_missing(self) -> None:
        mock_client = MagicMock()
        mock_client.collection_exists.return_value = False

        _ensure_collection_if_missing(mock_client, dense_dim=8)

        mock_client.create_collection.assert_called_once()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


class TestCLI:
    """Tests for the argparse-based main() entry point."""

    def test_main_code_subcommand(self) -> None:
        """main(['code', '/some/path']) calls ingest_code(['/some/path'])."""
        with patch("sentinel_rag.ingest.ingest_code") as mock_handler:
            mock_handler.return_value = 0
            result = main(["code", "/some/path"])
            mock_handler.assert_called_once_with(["/some/path"])
            assert result == 0

    def test_main_markdown_subcommand(self) -> None:
        with patch("sentinel_rag.ingest.ingest_markdown") as mock_handler:
            mock_handler.return_value = 0
            result = main(["markdown", "/docs"])
            mock_handler.assert_called_once_with(["/docs"])
            assert result == 0

    def test_main_runbook_subcommand(self) -> None:
        with patch("sentinel_rag.ingest.ingest_runbook") as mock_handler:
            mock_handler.return_value = 0
            result = main(["runbook", "/docs/runbooks"])
            mock_handler.assert_called_once_with(["/docs/runbooks"])
            assert result == 0

    def test_main_missing_subcommand_exits(self) -> None:
        with pytest.raises(SystemExit):
            main([])

    def test_main_unknown_subcommand_exits(self) -> None:
        with pytest.raises(SystemExit):
            main(["unknown", "/path"])
