"""LangGraph scaffolding — single SRE agent node with a stub tool.

T2.1: Runnable graph with typed State, one agent node, and a stub tool
that demonstrates the tool-calling loop end-to-end.

Flow:
    START → sre_agent → [tool calls?] → tools → sre_agent (loop)
                             ↓
                            END
"""

from __future__ import annotations

import os
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode


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
# Stub tool (T2.1 placeholder — real tools land in T2.2)
# ─────────────────────────────────────────────────────────────
@tool
def get_cluster_summary() -> str:
    """Return a high-level summary of the Kubernetes cluster health.

    Use this when the user asks about cluster status, node count,
    or overall health.
    """
    return (
        "Cluster summary (stub — T2.2 will wire real kubectl):\n"
        "- 3 nodes: 1 control-plane + 2 workers (all Ready)\n"
        "- 42 pods running across all namespaces\n"
        "- CPU 23 % | Memory 47 % | Disk 61 %\n"
        "- No alerts firing\n"
    )


STUB_TOOLS = [get_cluster_summary]


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
    base_url = os.environ.get("LLM_GATEWAY_URL", "http://localhost:4000/v1")
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
    llm_with_tools = llm.bind_tools(STUB_TOOLS)

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
    builder.add_node("tools", ToolNode(STUB_TOOLS))

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
