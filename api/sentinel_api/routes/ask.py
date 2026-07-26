"""FastAPI /ask endpoint — RAG-powered question answering (T1.13).

Accepts a natural-language query, retrieves relevant source chunks via
hybrid search + cross-encoder reranking, builds a prompt with the chunks as
context, calls the LLM gateway at ``llm.local``, and returns a grounded
answer with source citations.

POST /ask
---------
Request:  ``{"query": "where is the /ping handler?", "model": "gemma4"}``
Response: ``{"answer": "...", "sources": [{"path": "...", "lines": "...", "snippet": "..."}]}``

Environment variables
---------------------
====================  ===============================  ================================
Variable              Default                          Description
====================  ===============================  ================================
``LLM_BASE_URL``      ``http://llm.local/v1``          Base URL for the LLM gateway.
``LLM_MODEL``         ``gemma4``                       Default model for chat completions.
``LLM_TIMEOUT``       ``120``                          Timeout (seconds) for LLM calls.
``ASK_TOP_K_RETRIEVE`` ``50``                          Candidates from hybrid retrieval.
``ASK_TOP_K_RERANK``  ``5``                            Results after cross-encoder rerank.
====================  ===============================  ================================
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from sentinel_rag.citation import render_citation_json
from sentinel_rag.reranker import CrossEncoderReranker
from sentinel_rag.retrieve import RetrievedPoint, retrieve

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LLM_BASE_URL: str = os.environ.get(
    "LLM_BASE_URL",
    # In-cluster default: use k8s service DNS.  Override for local dev.
    "http://litellm.litellm.svc:4000/v1",
)
LLM_MODEL: str = os.environ.get("LLM_MODEL", "gemma4")
LLM_TIMEOUT: int = int(os.environ.get("LLM_TIMEOUT", "120"))
ASK_TOP_K_RETRIEVE: int = int(os.environ.get("ASK_TOP_K_RETRIEVE", "50"))
ASK_TOP_K_RERANK: int = int(os.environ.get("ASK_TOP_K_RERANK", "5"))

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class AskRequest(BaseModel):
    """Request body for ``POST /ask``."""

    query: str = Field(..., description="Natural-language question.", min_length=1)
    model: str = Field(
        default=LLM_MODEL,
        description="LLM model name (must be available at the gateway).",
    )
    top_k_rerank: int = Field(
        default=ASK_TOP_K_RERANK,
        ge=1,
        le=20,
        description="Number of source chunks to include after reranking.",
    )


class AskSource(BaseModel):
    """A single cited source chunk."""

    path: str
    lines: str
    snippet: str


class AskResponse(BaseModel):
    """Response body for ``POST /ask``."""

    answer: str
    sources: list[AskSource]
    model: str
    latency_ms: float


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["ask"])

# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a helpful coding assistant that answers questions about a codebase.
Answer ONLY based on the provided source code chunks below.
If the provided chunks don't contain enough information to answer the question, \
say "I couldn't find enough context to answer that question."
Always cite your sources using numbered markers like [1], [2] that refer \
to the source chunks listed after the context.
Be concise and factual. Do not hallucinate."""


def _build_context(sources: list[RetrievedPoint]) -> str:
    """Build a numbered context block from retrieved sources."""
    blocks: list[str] = []
    for i, s in enumerate(sources, 1):
        blocks.append(f"[{i}] {s.path}:{s.line_start}-{s.line_end}\n{s.text}")
    return "\n\n".join(blocks)


def _build_messages(query: str, sources: list[RetrievedPoint]) -> list[dict[str, str]]:
    """Build the message list for the LLM chat completion call."""
    context = _build_context(sources)
    user_message = (
        f"Source chunks:\n\n{context}\n\n"
        f"Question: {query}\n\n"
        f"Answer the question using the source chunks above. "
        f"Cite each source chunk with its number in brackets, like [1] or [2]."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]


# ---------------------------------------------------------------------------
# LLM caller
# ---------------------------------------------------------------------------


async def _call_llm(
    messages: list[dict[str, str]],
    model: str,
    client: httpx.AsyncClient,
) -> str:
    """Call the LLM gateway and return the assistant's response text."""
    url = f"{LLM_BASE_URL}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
    }
    try:
        response = await client.post(url, json=payload, timeout=LLM_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="LLM gateway timed out") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"LLM gateway error: {exc}") from exc

    choices: list[dict[str, Any]] = data.get("choices", [])
    if not choices:
        raise HTTPException(status_code=502, detail="LLM returned no choices")

    message: dict[str, Any] = choices[0].get("message", {})
    content: str = message.get("content", "")
    if not content:
        raise HTTPException(status_code=502, detail="LLM returned empty response")

    return content


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest) -> AskResponse:
    """Answer a natural-language question using RAG over the ingested codebase.

    Steps:
    1. Hybrid retrieval (dense + sparse, RRF fusion)
    2. Cross-encoder reranking
    3. Prompt construction with source chunks
    4. LLM call via the gateway at ``llm.local``
    5. Citation rendering
    """
    start = time.monotonic()

    # 1. Hybrid retrieval
    try:
        candidates: list[RetrievedPoint] = retrieve(
            request.query,
            top_k=ASK_TOP_K_RETRIEVE,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"Retrieval failed: {exc}") from exc

    # 2. Cross-encoder rerank
    reranker = CrossEncoderReranker()
    sources: list[RetrievedPoint] = reranker.rerank(
        request.query,
        candidates,
        top_k=request.top_k_rerank,
    )

    # 3. Build prompt
    messages = _build_messages(request.query, sources)

    # 4. Call LLM
    async with httpx.AsyncClient() as client:
        answer = await _call_llm(messages, request.model, client)

    latency_ms = (time.monotonic() - start) * 1000

    # 5. Render response with citations
    result = render_citation_json(answer, sources)

    return AskResponse(
        answer=result["answer"],
        sources=[AskSource(**s) for s in result["sources"]],
        model=request.model,
        latency_ms=round(latency_ms, 1),
    )
