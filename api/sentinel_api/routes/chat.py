"""WebSocket streaming chat endpoint (T2.5).

Accepts WS connections, runs the SRE agent graph with streaming events,
and pushes tokens / tool-calls / sources / errors to the client in
real time.

Protocol (client → server)
----------------------------
::

    {"type": "chat", "query": "How is my cluster doing?"}
    {"type": "stop"}   — cancels the current run

Protocol (server → client)
----------------------------
::

    {"type": "token",       "text": "The cluster has…"}
    {"type": "tool",        "name": "kubectl_get", "args": {"resource": "pods"}}
    {"type": "tool_result", "name": "kubectl_get", "result": "NAME …"}
    {"type": "sources",     "sources": [{"path": "…", "lines": "…", "snippet": "…"}]}
    {"type": "done"}
    {"type": "error",       "message": "something went wrong"}

Usage::

    wscat -c ws://localhost:8000/chat/ws
    > {"type":"chat","query":"list pods in observability"}
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

router = APIRouter(tags=["chat"])


# ---------------------------------------------------------------------------
# Lazy import — the agent graph is heavy and may not be available
# in all deployment contexts.
# ---------------------------------------------------------------------------
def _get_graph():
    """Return the compiled SRE agent graph (lazy import).

    This avoids import-time failures when the ``sentinel_agents``
    package is not on PYTHONPATH (e.g. in CI smoke tests).
    """
    from sentinel_agents.graph import AgentState, graph as g

    return AgentState, g


# ---------------------------------------------------------------------------
# Event builders
# ---------------------------------------------------------------------------
def _token_event(text: str) -> dict[str, Any]:
    return {"type": "token", "text": text}


def _tool_call_event(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool", "name": name, "args": args}


def _tool_result_event(name: str, result: str) -> dict[str, Any]:
    return {"type": "tool_result", "name": name, "result": result}


def _sources_event(sources: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "sources", "sources": sources}


def _done_event() -> dict[str, Any]:
    return {"type": "done"}


def _error_event(message: str) -> dict[str, Any]:
    return {"type": "error", "message": message}


# ---------------------------------------------------------------------------
# Message extractors
# ---------------------------------------------------------------------------
def _extract_tool_results(messages: list) -> list[dict[str, Any]]:
    """Scan new messages for ToolMessages and return their data."""
    results: list[dict[str, Any]] = []
    for m in messages:
        if isinstance(m, ToolMessage):
            result = m.content if hasattr(m, "content") else str(m)
            results.append({
                "name": getattr(m, "name", "unknown"),
                "result": result[:2000],  # truncate for WebSocket
            })
    return results


def _extract_rag_sources(messages: list) -> list[dict[str, Any]]:
    """Try to parse RAG source citations from ToolMessages.

    Two tool output formats are understood:

    1. ``rag_search`` — formatted text like::

        Found 10 document(s) for: "…"
        [1] path:lines (score: …, type: …)
            snippet…

    2. ``rag_evidence`` (T3.4) — structured JSON like::

        {"query": "…", "evidence": [{"path": …, "lines": …,
        "score": …, "source_type": …, "snippet": …}, …]}

    Returns a flat list of ``{path, lines, snippet}`` dicts.
    """
    sources: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, ToolMessage):
            continue
        name = getattr(m, "name", "")
        content = m.content if hasattr(m, "content") else str(m)

        # ── rag_evidence: structured JSON (T3.4) ──
        if name == "rag_evidence":
            try:
                payload = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                continue
            for ev in payload.get("evidence", []):
                if not isinstance(ev, dict):
                    continue
                path = ev.get("path", "")
                lines = ev.get("lines", "")
                if path and lines:
                    sources.append({
                        "path": path.strip(),
                        "lines": lines.strip(),
                        "snippet": str(ev.get("snippet", ""))[:300],
                    })
            continue

        if name != "rag_search":
            continue
        # ── rag_search: formatted text ──
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("[") and "]" in line and ":" in line:
                # Try to extract path:lines
                try:
                    bracket_end = line.index("]")
                    head = line[bracket_end + 1:].strip()
                    # head looks like "path:lines (score: …, type: …)"
                    path_lines, _, _ = head.partition(" (")
                    path, _, lines = path_lines.partition(":")
                    if path and lines:
                        snippet = (
                            content.split("\n")[content.split("\n").index(line.rstrip()) + 1]
                            if line.rstrip() in content.split("\n")
                            and content.split("\n").index(line.rstrip()) + 1 < len(content.split("\n"))
                            else ""
                        ).strip()
                        sources.append({
                            "path": path.strip(),
                            "lines": lines.strip(),
                            "snippet": snippet[:300] if snippet else "",
                        })
                except (ValueError, IndexError):
                    continue
    return sources


# ---------------------------------------------------------------------------
# Streaming helper — runs the graph and pushes events over the WS
# ---------------------------------------------------------------------------
async def _emit_agent_chunk(
    node_output: dict[str, Any],
    ws: WebSocket,
    accumulated_text: str,
    seen_tool_call_ids: set[str],
) -> str:
    """Emit tokens + tool-call events for one agent node output chunk.

    Used by both the SRE agent and the Security Agent (T3.2) — they emit
    the same ``token`` / ``tool`` event shapes so the frontend needs no
    special-casing.

    Returns the updated ``accumulated_text`` so the caller can thread it
    through the streaming loop.
    """
    new_messages: list = node_output.get("messages", [])

    for msg in new_messages:
        if isinstance(msg, AIMessage):
            content = msg.content if hasattr(msg, "content") else ""
            if isinstance(content, str) and content:
                # For non-streaming LLMs this is the full response.
                # Emit only the newly-seen suffix.
                new_text = content[len(accumulated_text):]
                if new_text:
                    accumulated_text = content
                    await ws.send_json(_token_event(new_text))

            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    tc_id = tc.get("id", "")
                    if tc_id not in seen_tool_call_ids:
                        seen_tool_call_ids.add(tc_id)
                        await ws.send_json(
                            _tool_call_event(
                                name=tc.get("name", "unknown"),
                                args=tc.get("args", {}),
                            )
                        )

    return accumulated_text


# ---------------------------------------------------------------------------
# Streaming helper — runs the graph and pushes events over the WS
# ---------------------------------------------------------------------------
async def _stream_agent(ws: WebSocket, query: str) -> None:
    """Run the LangGraph agent with streaming updates over the WebSocket."""
    try:
        AgentState, graph = _get_graph()
    except ImportError as exc:
        await ws.send_json(
            _error_event(
                f"Agent graph not available: {exc}. "
                "Make sure the 'agents' package is on PYTHONPATH "
                "and dependencies are installed."
            )
        )
        return

    initial_state: AgentState = {
        "messages": [HumanMessage(content=query)],
        "tool_calls": [],
        "scratchpad": {},
        "routing": "",
        "classification_json": "",
    }

    accumulated_text: str = ""
    seen_tool_call_ids: set[str] = set()

    try:
        async for chunk in graph.astream(initial_state, stream_mode="updates"):
            # ── triage_agent node produced output (T3.1) ──
            if "triage_agent" in chunk:
                triage_output = chunk["triage_agent"]
                routing = triage_output.get("routing", "general")
                classification_json = triage_output.get("classification_json", "{}")
                scratchpad = triage_output.get("scratchpad", {})

                # Parse classification for the frontend
                try:
                    classification = json.loads(classification_json)
                except (json.JSONDecodeError, TypeError):
                    classification = {"category": routing, "reasoning": "unknown"}

                await ws.send_json({
                    "type": "classification",
                    "category": routing,
                    "reasoning": classification.get("reasoning", ""),
                    "refined_query": classification.get("refined_query", query),
                })

            # ── sre_agent node produced output ──
            if "sre_agent" in chunk:
                accumulated_text = await _emit_agent_chunk(
                    chunk["sre_agent"], ws, accumulated_text, seen_tool_call_ids
                )

            # ── security_agent node produced output (T3.2) ──
            # Same event shapes as sre_agent — the frontend just sees
            # tool calls + tokens; we don't need a separate event type.
            if "security_agent" in chunk:
                accumulated_text = await _emit_agent_chunk(
                    chunk["security_agent"], ws, accumulated_text, seen_tool_call_ids
                )

            # ── cost_agent node produced output (T3.3) ──
            # Same event shapes as sre_agent / security_agent.
            if "cost_agent" in chunk:
                accumulated_text = await _emit_agent_chunk(
                    chunk["cost_agent"], ws, accumulated_text, seen_tool_call_ids
                )

            # ── rag_agent node produced output (T3.4) ──
            # Same event shapes as the other specialists.
            if "rag_agent" in chunk:
                accumulated_text = await _emit_agent_chunk(
                    chunk["rag_agent"], ws, accumulated_text, seen_tool_call_ids
                )

            # ── incident loop nodes produced output (T3.5) ──
            if "dispatch" in chunk:
                dispatch_out = chunk["dispatch"]
                await ws.send_json({
                    "type": "dispatch",
                    "specialists": ["sre_agent", "security_agent", "rag_agent"],
                    "incident": dispatch_out.get("incident", "")[:500],
                })

            if "synthesis" in chunk:
                synth_out = chunk["synthesis"]
                await ws.send_json({
                    "type": "synthesis",
                    "text": synth_out.get("synthesis", ""),
                })

            if "planner" in chunk:
                plan_out = chunk["planner"]
                await ws.send_json({
                    "type": "plan",
                    "plan": plan_out.get("plan", {}),
                })

            if "approval" in chunk:
                approval_out = chunk["approval"]
                await ws.send_json({
                    "type": "approval",
                    "status": approval_out.get("approval_status", "awaiting_approval"),
                    "plan": approval_out.get("scratchpad", {}).get("pending_plan", {}),
                })

            # ── tools / sec_tools / cost_tools / rag_tools node output ──
            tools_output = None
            if "tools" in chunk:
                tools_output = chunk["tools"]
            elif "sec_tools" in chunk:
                tools_output = chunk["sec_tools"]
            elif "cost_tools" in chunk:
                tools_output = chunk["cost_tools"]
            elif "rag_tools" in chunk:
                tools_output = chunk["rag_tools"]

            if tools_output is not None:
                new_messages: list = tools_output.get("messages", [])

                # Emit tool results
                for result in _extract_tool_results(new_messages):
                    await ws.send_json(
                        _tool_result_event(
                            name=result["name"],
                            result=result["result"],
                        )
                    )

                # Emit sources if rag_search was involved
                rag_sources = _extract_rag_sources(new_messages)
                if rag_sources:
                    await ws.send_json(_sources_event(rag_sources))

        # ── Done ──
        await ws.send_json(_done_event())

    except Exception as exc:
        await ws.send_json(_error_event(f"{type(exc).__name__}: {exc}"))


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------
@router.websocket("/chat/ws")
async def chat_websocket(ws: WebSocket) -> None:
    """Streaming chat WebSocket.

    Accepts ``{"type": "chat", "query": "…"}`` messages and streams
    agent tokens, tool calls, tool results, and sources back to the
    client.
    """
    await ws.accept()

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json(_error_event("Invalid JSON"))
                continue

            msg_type = data.get("type", "")

            if msg_type == "chat":
                query = data.get("query", "")
                if not query or not query.strip():
                    await ws.send_json(_error_event("Missing or empty 'query' field"))
                    continue
                await _stream_agent(ws, query.strip())

            elif msg_type == "stop":
                await ws.send_json(_done_event())
                # We don't actually cancel the asyncio task in this
                # simple implementation — the client just stops listening.

            else:
                await ws.send_json(
                    _error_event(f"Unknown message type: '{msg_type}'. Expected 'chat' or 'stop'.")
                )

    except WebSocketDisconnect:
        pass  # client disconnected — clean exit
