"""Embedding service for Sentinel RAG (T1.5).

Produces dense vector embeddings for text, supporting local inference via
Ollama (BGE-M3) with cloud fallback to OpenAI's embedding models.

Provider selection is controlled by the ``EMBED_PROVIDER`` environment
variable; each provider reads its own configuration from the environment.

Quick start::

    from sentinel_rag.embed import get_embedder

    embedder = get_embedder()
    vec = embedder.embed("hello")          # single text → list[float]
    vecs = embedder.embed_batch(["a", "b"])  # batch → list[list[float]]

Run standalone to smoke-test::

    python -m sentinel_rag.embed "hello world"

Environment variables
---------------------

====================  =======  ==============================================
Variable              Default  Description
====================  =======  ==============================================
``EMBED_PROVIDER``    ollama   ``"ollama"``, ``"openai"``, or ``"local"``
                               (``sentence-transformers`` in-process).
``OLLAMA_BASE_URL``   http://  Base URL for the Ollama server.
                      localhost:
                      11434
``OLLAMA_MODEL``      bge-m3   Model name pulled from Ollama.
``OPENAI_API_KEY``    (none)   OpenAI API key (required for ``openai``).
``OPENAI_BASE_URL``   https:// Base URL for OpenAI-compatible API.
                      api.open
                      ai.com/v1
``OPENAI_MODEL``      text-    Model name for OpenAI embeddings.
                      embeddin
                      g-3-small
``LOCAL_EMBED_MODEL`` all-     ``sentence-transformers`` model name for the
                      MiniLM-  ``"local"`` provider.
                      L6-v2
====================  =======  ==============================================
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import ClassVar

import httpx

# --------------------------------------------------------------------------- #
# Abstract base
# --------------------------------------------------------------------------- #


class Embedder(ABC):
    """Abstract base for all embedding providers."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Expected output dimension for this embedder."""
        ...

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Produce an embedding vector for a single *text*.

        Raises:
            ValueError: If *text* is empty or whitespace-only.
            RuntimeError: If the upstream API returns an error.
        """
        ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Produce embedding vectors for a batch of *texts*.

        The default implementation calls :meth:`embed` sequentially.
        Subclasses may override with a true batch API call.
        """
        return [self.embed(t) for t in texts]


# --------------------------------------------------------------------------- #
# Ollama provider
# --------------------------------------------------------------------------- #


class OllamaEmbedder(Embedder):
    """Embedder that calls Ollama's ``/api/embed`` endpoint.

    Uses the BGE-M3 model (1024-dim) by default.  Model must be pulled
    beforehand: ``ollama pull bge-m3``.

    The vector dimension is detected lazily from the first embedding call
    and cached for subsequent lookups.
    """

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.model = model or os.getenv("OLLAMA_MODEL", "bge-m3")
        self.base_url = (
            base_url
            or os.getenv(
                "OLLAMA_BASE_URL",
                # In-cluster: host Docker bridge (Ollama runs on the host).
                "http://172.18.0.1:11434",
            )
        ).rstrip("/")
        self._dimension: int | None = None

    @property
    def dimension(self) -> int:
        if self._dimension is not None:
            return self._dimension
        # Detect dimension from a lightweight test embedding.
        # Falls back to 1024 (BGE-M3 default) if detection fails.
        try:
            vec = self.embed("dimension probe")
            self._dimension = len(vec)
        except Exception:
            self._dimension = 1024
        return self._dimension

    def embed(self, text: str) -> list[float]:
        self._validate_text(text)
        url = f"{self.base_url}/api/embed"
        payload = {"model": self.model, "input": text}
        try:
            response = httpx.post(url, json=payload, timeout=60)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Ollama embed request failed: {exc}") from exc

        embeddings = data.get("embeddings")
        if not embeddings or not isinstance(embeddings, list):
            raise RuntimeError(f"Ollama returned unexpected response: {data}")
        vec: list[float] = embeddings[0]
        return vec

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        for t in texts:
            self._validate_text(t)
        url = f"{self.base_url}/api/embed"
        payload = {"model": self.model, "input": texts}
        try:
            response = httpx.post(url, json=payload, timeout=120)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Ollama batch embed request failed: {exc}") from exc

        embeddings = data.get("embeddings")
        if not embeddings or not isinstance(embeddings, list):
            raise RuntimeError(f"Ollama returned unexpected response: {data}")
        return [list(e) for e in embeddings]

    @staticmethod
    def _validate_text(text: str) -> None:
        if not text or not text.strip():
            raise ValueError("text must be non-empty")


# --------------------------------------------------------------------------- #
# OpenAI provider
# --------------------------------------------------------------------------- #


class OpenAIEmbedder(Embedder):
    """Embedder that calls OpenAI's ``/v1/embeddings`` endpoint.

    Works with any OpenAI-compatible API (Azure, local vLLM, etc.) by
    setting ``OPENAI_BASE_URL``.
    """

    _DIMENSIONS: ClassVar[dict[str, int]] = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        dimensions: int | None = None,
    ) -> None:
        self.model = model or os.getenv("OPENAI_MODEL", "text-embedding-3-small")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.base_url = (
            base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        ).rstrip("/")
        self._dimensions_override = dimensions

        if not self.api_key:
            raise ValueError(
                "OPENAI_API_KEY must be set (or pass api_key=...) when using OpenAIEmbedder"
            )

    @property
    def dimension(self) -> int:
        if self._dimensions_override is not None:
            return self._dimensions_override
        return self._DIMENSIONS.get(self.model, 1536)

    def embed(self, text: str) -> list[float]:
        self._validate_text(text)
        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict = {"model": self.model, "input": text}
        if self._dimensions_override is not None:
            payload["dimensions"] = self._dimensions_override

        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=60)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"OpenAI embed request failed: {exc}") from exc

        embedding = data.get("data", [{}])[0].get("embedding")
        if embedding is None:
            raise RuntimeError(f"OpenAI returned unexpected response: {data}")
        return list(embedding)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        for t in texts:
            self._validate_text(t)
        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict = {"model": self.model, "input": texts}
        if self._dimensions_override is not None:
            payload["dimensions"] = self._dimensions_override

        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=120)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"OpenAI batch embed request failed: {exc}") from exc

        items = data.get("data", [])
        # OpenAI returns results in the same order as the input.
        items.sort(key=lambda d: d.get("index", 0))
        return [list(d["embedding"]) for d in items]

    @staticmethod
    def _validate_text(text: str) -> None:
        if not text or not text.strip():
            raise ValueError("text must be non-empty")


# --------------------------------------------------------------------------- #
# Local sentence-transformers provider (CI-friendly, no external services)
# --------------------------------------------------------------------------- #

_LOCAL_MODEL = os.getenv("LOCAL_EMBED_MODEL", "all-MiniLM-L6-v2")


class LocalEmbedder(Embedder):
    """Embedder that uses ``sentence-transformers`` for local inference.

    No external service (Ollama, OpenAI) needed — the model runs entirely
    in-process.  Designed for CI environments where you want a fast,
    deterministic embedding without network calls.

    Uses ``all-MiniLM-L6-v2`` (384-dim, ~80 MB) by default.  Override via
    the ``LOCAL_EMBED_MODEL`` env var.
    """

    _MODEL_DIMS: ClassVar[dict[str, int]] = {
        "all-MiniLM-L6-v2": 384,
        "all-mpnet-base-v2": 768,
        "BAAI/bge-small-en-v1.5": 384,
    }

    def __init__(
        self,
        model_name: str | None = None,
    ) -> None:
        self.model_name = model_name or _LOCAL_MODEL
        self._model: object | None = None

    @property
    def dimension(self) -> int:
        return self._MODEL_DIMS.get(self.model_name, 384)

    @property
    def model(self) -> object:
        """Lazily-loaded ``SentenceTransformer`` instance."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, text: str) -> list[float]:
        self._validate_text(text)
        try:
            vec = self.model.encode(text, normalize_embeddings=True)  # type: ignore[union-attr]
            return list(vec)  # type: ignore[arg-type]
        except Exception as exc:
            raise RuntimeError(f"Local embed failed: {exc}") from exc

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        for t in texts:
            self._validate_text(t)
        try:
            vecs = self.model.encode(texts, normalize_embeddings=True)  # type: ignore[union-attr]
            return [list(v) for v in vecs]  # type: ignore[arg-type]
        except Exception as exc:
            raise RuntimeError(f"Local batch embed failed: {exc}") from exc

    @staticmethod
    def _validate_text(text: str) -> None:
        if not text or not text.strip():
            raise ValueError("text must be non-empty")


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #


def get_embedder() -> Embedder:
    """Return an :class:`Embedder` based on the ``EMBED_PROVIDER`` env var.

    Supports ``"ollama"`` (default), ``"openai"``, and ``"local"``
    (sentence-transformers, CI-friendly).

    Raises:
        ValueError: If ``EMBED_PROVIDER`` is set to an unknown value.
    """
    provider = os.getenv("EMBED_PROVIDER", "ollama").lower()
    if provider == "ollama":
        return OllamaEmbedder()
    if provider == "openai":
        return OpenAIEmbedder()
    if provider == "local":
        return LocalEmbedder()
    raise ValueError(
        f"Unknown EMBED_PROVIDER: {provider!r}. Expected 'ollama', 'openai', or 'local'."
    )


# --------------------------------------------------------------------------- #
# Standalone smoke-test
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import sys

    text = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "hello world"
    embedder = get_embedder()
    print(f"Provider: {type(embedder).__name__}")
    print(f"Model: {embedder.model}")  # type: ignore[attr-defined]
    print(f"Expected dimension: {embedder.dimension}")
    vec = embedder.embed(text)
    print(f"Actual dimension: {len(vec)}")
    print(f"First 8 values: {vec[:8]}")
