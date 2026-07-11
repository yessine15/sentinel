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
``EMBED_PROVIDER``    ollama   ``"ollama"`` or ``"openai"``.
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
    """

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.model = model or os.getenv("OLLAMA_MODEL", "bge-m3")
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip(
            "/"
        )

    @property
    def dimension(self) -> int:
        # BGE-M3 always outputs 1024.
        return 1024

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
# Factory
# --------------------------------------------------------------------------- #


def get_embedder() -> Embedder:
    """Return an :class:`Embedder` based on the ``EMBED_PROVIDER`` env var.

    Raises:
        ValueError: If ``EMBED_PROVIDER`` is set to an unknown value.
    """
    provider = os.getenv("EMBED_PROVIDER", "ollama").lower()
    if provider == "ollama":
        return OllamaEmbedder()
    if provider == "openai":
        return OpenAIEmbedder()
    raise ValueError(f"Unknown EMBED_PROVIDER: {provider!r}. Expected 'ollama' or 'openai'.")


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
