"""LangGraph SRE agent — StateGraph with allow-listed tools.

T2.1: Runnable graph with typed State, one agent node, tool loop.
T2.2: Replaced stub tool with real allow-listed tool registry
      (kubectl_get, kubectl_describe, promql_query, logql_query).

Flow:
    START → sre_agent → [tool calls?] → tools → sre_agent (loop)
                             ↓
                            END
"""

from __future__ import annotations

import os
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from sentinel_agents.tools import ALLOWED_TOOLS


# ─────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────
class AgentState(TypedDict):
    """Typed state that flows through the SRE agent graph.

    Attributes:
        messages: Conversation history.  ``add_messages`` merges new
            messages automatically so the list always appends.
        tool_calls: Pending tool-call payloads extracted from the last
            AIMessage.  Cleared once the tool node has executed them.
        scratchpad: Arbitrary working memory the agent can read and
            write across turns (e.g. collected evidence, intermediate
            reasoning).
    """

    messages: Annotated[list[BaseMessage], add_messages]
    tool_calls: list[dict[str, Any]]
    scratchpad: dict[str, Any]


# ─────────────────────────────────────────────────────────────
# Tool list — populated from the allow-list registry (T2.2)
# ─────────────────────────────────────────────────────────────
SRE_TOOLS = ALLOWED_TOOLS


# ─────────────────────────────────────────────────────────────
# LLM factory
# ─────────────────────────────────────────────────────────────
def _build_llm() -> ChatOpenAI:
    """Create a ChatOpenAI pointing at the local LLM gateway (proxy.py).

    Environment variables
    ---------------------
    LLM_GATEWAY_URL : str
        Base URL of the OpenAI-compatible gateway (default
        ``http://localhost:4000/v1``).
    LLM_MODEL : str
        Model name to request from the gateway (default ``gemma4``).
    """
    base_url = os.environ.get(
        "LLM_GATEWAY_URL",
        # In-cluster: k8s service DNS.  Override for local dev.
        "http://litellm.litellm.svc:4000/v1",
    )
    model = os.environ.get("LLM_MODEL", "gemma4")
    return ChatOpenAI(
        base_url=base_url,
        api_key="not-needed",  # proxy does not require auth
        model=model,
        temperature=0.0,
        timeout=120,
    )


# ─────────────────────────────────────────────────────────────
# Nodes
# ─────────────────────────────────────────────────────────────
def sre_agent_node(state: AgentState) -> dict[str, Any]:
    """The SRE agent: calls the LLM with bound tools.

    If the LLM requests tool calls they are stored in ``tool_calls``
    and routed to the tool-executor node by the conditional edge.
    """
    llm = _build_llm()
    llm_with_tools = llm.bind_tools(SRE_TOOLS)

    response: AIMessage = llm_with_tools.invoke(state["messages"])

    result: dict[str, Any] = {"messages": [response]}

    if response.tool_calls:
        result["tool_calls"] = response.tool_calls

    return result


# ─────────────────────────────────────────────────────────────
# Router
# ─────────────────────────────────────────────────────────────
def should_continue(state: AgentState) -> Literal["tools", "__end__"]:
    """Route to the tool node if the last message has pending tool calls."""
    messages = state.get("messages", [])
    if not messages:
        return "__end__"

    last = messages[-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return "__end__"


# ─────────────────────────────────────────────────────────────
# Graph builder
# ─────────────────────────────────────────────────────────────
def build_graph() -> StateGraph:
    """Build and compile the SRE agent StateGraph.

    Returns a compiled graph that is ready to ``invoke()``.
    """
    builder = StateGraph(AgentState)

    # Nodes
    builder.add_node("sre_agent", sre_agent_node)
    builder.add_node("tools", ToolNode(SRE_TOOLS))

    # Edges
    builder.set_entry_point("sre_agent")
    builder.add_conditional_edges(
        "sre_agent",
        should_continue,
        {"tools": "tools", "__end__": END},
    )
    builder.add_edge("tools", "sre_agent")

    return builder.compile()


# ─────────────────────────────────────────────────────────────
# Module-level singleton (compiled once at import time)
# ─────────────────────────────────────────────────────────────
graph = build_graph()
