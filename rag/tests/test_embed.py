"""Tests for the Sentinel RAG embedding service (T1.5)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sentinel_rag.embed import (
    Embedder,
    OllamaEmbedder,
    OpenAIEmbedder,
    get_embedder,
)

# --------------------------------------------------------------------------- #
# Embedder ABC
# --------------------------------------------------------------------------- #


def test_embedder_is_abstract() -> None:
    """Embedder cannot be instantiated directly."""
    with pytest.raises(TypeError):
        Embedder()  # type: ignore[abstract]


def test_embedder_has_dimension_property() -> None:
    """dimension is an abstract property."""
    assert hasattr(Embedder, "dimension")
    assert getattr(Embedder.dimension, "__isabstractmethod__", False) is True


def test_embedder_has_embed_method() -> None:
    """embed is an abstract method."""
    assert hasattr(Embedder, "embed")
    assert getattr(Embedder.embed, "__isabstractmethod__", False) is True


# --------------------------------------------------------------------------- #
# OllamaEmbedder
# --------------------------------------------------------------------------- #


class TestOllamaEmbedder:
    """Tests for OllamaEmbedder — HTTP calls are fully mocked."""

    @pytest.fixture
    def mock_httpx(self) -> MagicMock:
        """Patch httpx.post so we never hit the network."""
        with patch("sentinel_rag.embed.httpx.post") as mock_post:
            yield mock_post

    def test_dimension(self) -> None:
        embedder = OllamaEmbedder()
        assert embedder.dimension == 1024

    def test_embed_single(self, mock_httpx: MagicMock) -> None:
        """embed() returns a 1024-dim vector."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"embeddings": [[0.1] * 1024]}
        mock_response.raise_for_status.return_value = None
        mock_httpx.return_value = mock_response

        embedder = OllamaEmbedder()
        vec = embedder.embed("hello world")
        assert len(vec) == 1024
        assert vec == [0.1] * 1024

        # Verify the HTTP call
        mock_httpx.assert_called_once()
        call_args = mock_httpx.call_args
        assert call_args[0][0] == "http://localhost:11434/api/embed"
        assert call_args[1]["json"] == {"model": "bge-m3", "input": "hello world"}

    def test_embed_batch(self, mock_httpx: MagicMock) -> None:
        """embed_batch() sends all texts in one request."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"embeddings": [[0.1] * 1024, [0.2] * 1024]}
        mock_response.raise_for_status.return_value = None
        mock_httpx.return_value = mock_response

        embedder = OllamaEmbedder()
        vecs = embedder.embed_batch(["hello", "world"])
        assert len(vecs) == 2
        assert len(vecs[0]) == 1024
        assert vecs[0] == [0.1] * 1024
        assert vecs[1] == [0.2] * 1024

        mock_httpx.assert_called_once()
        call_args = mock_httpx.call_args
        assert call_args[1]["json"] == {"model": "bge-m3", "input": ["hello", "world"]}

    def test_embed_empty_text_raises(self) -> None:
        embedder = OllamaEmbedder()
        with pytest.raises(ValueError, match="non-empty"):
            embedder.embed("")
        with pytest.raises(ValueError, match="non-empty"):
            embedder.embed("   ")

    def test_embed_batch_empty_text_raises(self) -> None:
        embedder = OllamaEmbedder()
        with pytest.raises(ValueError, match="non-empty"):
            embedder.embed_batch(["ok", ""])

    def test_embed_batch_default_impl(self, mock_httpx: MagicMock) -> None:
        """embed_batch is overridden for true batch; verify it's not the default loop."""
        embedder = OllamaEmbedder()
        # If we hadn't overridden embed_batch, it would call embed() N times.
        # With our override, it calls httpx.post once.
        mock_response = MagicMock()
        mock_response.json.return_value = {"embeddings": [[0.0] * 1024] * 3}
        mock_response.raise_for_status.return_value = None
        mock_httpx.return_value = mock_response

        vecs = embedder.embed_batch(["a", "b", "c"])
        assert len(vecs) == 3
        assert mock_httpx.call_count == 1

    def test_custom_base_url(self, mock_httpx: MagicMock) -> None:
        """Custom OLLAMA_BASE_URL is respected."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"embeddings": [[0.5] * 1024]}
        mock_response.raise_for_status.return_value = None
        mock_httpx.return_value = mock_response

        embedder = OllamaEmbedder(base_url="http://ollama:11434")
        embedder.embed("hi")
        assert mock_httpx.call_args[0][0] == "http://ollama:11434/api/embed"

    def test_http_error_raises_runtime_error(self, mock_httpx: MagicMock) -> None:
        """HTTP errors become RuntimeError."""
        import httpx

        mock_httpx.side_effect = httpx.ConnectError("connection refused")
        embedder = OllamaEmbedder()
        with pytest.raises(RuntimeError, match="Ollama embed request failed"):
            embedder.embed("hello")

    def test_unexpected_response_raises(self, mock_httpx: MagicMock) -> None:
        """Malformed API response raises RuntimeError."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"not_embeddings": 42}
        mock_response.raise_for_status.return_value = None
        mock_httpx.return_value = mock_response

        embedder = OllamaEmbedder()
        with pytest.raises(RuntimeError, match="unexpected response"):
            embedder.embed("hello")


# --------------------------------------------------------------------------- #
# OpenAIEmbedder
# --------------------------------------------------------------------------- #


class TestOpenAIEmbedder:
    """Tests for OpenAIEmbedder — HTTP calls are fully mocked."""

    @pytest.fixture
    def mock_httpx(self) -> MagicMock:
        """Patch httpx.post so we never hit the network."""
        with patch("sentinel_rag.embed.httpx.post") as mock_post:
            yield mock_post

    def test_missing_api_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OpenAIEmbedder requires an API key."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            OpenAIEmbedder(api_key="")  # empty string explicitly passed

    def test_dimension_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """text-embedding-3-small is 1536 dim."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        embedder = OpenAIEmbedder()
        assert embedder.dimension == 1536

    def test_dimension_large(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        embedder = OpenAIEmbedder(model="text-embedding-3-large")
        assert embedder.dimension == 3072

    def test_dimension_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        embedder = OpenAIEmbedder(dimensions=512)
        assert embedder.dimension == 512

    def test_embed_single(self, monkeypatch: pytest.MonkeyPatch, mock_httpx: MagicMock) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embedding": [0.3] * 1536, "index": 0}]}
        mock_response.raise_for_status.return_value = None
        mock_httpx.return_value = mock_response

        embedder = OpenAIEmbedder()
        vec = embedder.embed("hello")
        assert len(vec) == 1536
        assert vec == [0.3] * 1536

    def test_embed_batch(self, monkeypatch: pytest.MonkeyPatch, mock_httpx: MagicMock) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"embedding": [0.1] * 1536, "index": 0},
                {"embedding": [0.2] * 1536, "index": 1},
            ]
        }
        mock_response.raise_for_status.return_value = None
        mock_httpx.return_value = mock_response

        embedder = OpenAIEmbedder()
        vecs = embedder.embed_batch(["a", "b"])
        assert len(vecs) == 2
        assert vecs[0] == [0.1] * 1536
        assert vecs[1] == [0.2] * 1536

    def test_embed_empty_text_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        embedder = OpenAIEmbedder()
        with pytest.raises(ValueError, match="non-empty"):
            embedder.embed("")

    def test_embed_batch_empty_text_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        embedder = OpenAIEmbedder()
        with pytest.raises(ValueError, match="non-empty"):
            embedder.embed_batch(["ok", "   "])

    def test_custom_base_url(self, monkeypatch: pytest.MonkeyPatch, mock_httpx: MagicMock) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embedding": [0.0] * 1536, "index": 0}]}
        mock_response.raise_for_status.return_value = None
        mock_httpx.return_value = mock_response

        embedder = OpenAIEmbedder(base_url="https://custom.openai.com/v1")
        embedder.embed("hi")
        assert mock_httpx.call_args[0][0] == "https://custom.openai.com/v1/embeddings"

    def test_http_error_raises_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch, mock_httpx: MagicMock
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        import httpx

        mock_httpx.side_effect = httpx.HTTPStatusError(
            "unauthorized", request=MagicMock(), response=MagicMock(status_code=401)
        )
        embedder = OpenAIEmbedder()
        with pytest.raises(RuntimeError, match="OpenAI embed request failed"):
            embedder.embed("hello")

    def test_unexpected_response_raises(
        self, monkeypatch: pytest.MonkeyPatch, mock_httpx: MagicMock
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{}]}  # no "embedding" key
        mock_response.raise_for_status.return_value = None
        mock_httpx.return_value = mock_response

        embedder = OpenAIEmbedder()
        with pytest.raises(RuntimeError, match="unexpected response"):
            embedder.embed("hello")


# --------------------------------------------------------------------------- #
# Factory: get_embedder
# --------------------------------------------------------------------------- #


def test_get_embedder_default_is_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMBED_PROVIDER", raising=False)
    embedder = get_embedder()
    assert isinstance(embedder, OllamaEmbedder)


def test_get_embedder_explicit_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_PROVIDER", "ollama")
    embedder = get_embedder()
    assert isinstance(embedder, OllamaEmbedder)


def test_get_embedder_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    embedder = get_embedder()
    assert isinstance(embedder, OpenAIEmbedder)


def test_get_embedder_unknown_provider_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_PROVIDER", "cohere")
    with pytest.raises(ValueError, match="Unknown EMBED_PROVIDER"):
        get_embedder()


def test_get_embedder_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_PROVIDER", "OLLAMA")
    embedder = get_embedder()
    assert isinstance(embedder, OllamaEmbedder)


# --------------------------------------------------------------------------- #
# Default embed_batch on base class
# --------------------------------------------------------------------------- #


def test_default_embed_batch_is_sequential() -> None:
    """The base Embedder.embed_batch calls embed() once per text."""

    class _Fake(Embedder):
        @property
        def dimension(self) -> int:
            return 4

        def embed(self, text: str) -> list[float]:
            return [len(text)] * 4

    fake = _Fake()
    vecs = fake.embed_batch(["a", "bb", "ccc"])
    assert vecs == [[1] * 4, [2] * 4, [3] * 4]
