"""LangGraph multi-agent orchestrator — Triage → Specialist pattern.

T2.1-T2.2: Single SRE agent with tool loop.
T3.1: Triage Agent as entry point — classifies queries and routes to
      the appropriate specialist (SRE for now; Security + RAG in T3.2+).

Flow:
    START → triage_agent → route_to_specialist
                                ├── "sre" → sre_agent → [tool calls?]
                                │                ↓              ↓
                                │              tools ←──────────┘
                                │                ↓
                                │             sre_agent → END
                                ├── "knowledge" → sre_agent (with RAG hint)
                                └── "general"   → sre_agent
"""

from __future__ import annotations

import json
import os
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from sentinel_agents.tools import ALLOWED_TOOLS


# ─────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────
class AgentState(TypedDict):
    """Typed state that flows through the multi-agent graph.

    Attributes:
        messages: Conversation history.  ``add_messages`` merges new
            messages automatically so the list always appends.
        tool_calls: Pending tool-call payloads extracted from the last
            AIMessage.  Cleared once the tool node has executed them.
        scratchpad: Arbitrary working memory the agent can read and
            write across turns (e.g. collected evidence, intermediate
            reasoning).
        routing: The triage classification result (``sre``, ``knowledge``,
            ``general``, or ``security`` — set by the triage node).
        classification_json: Raw JSON string from the triage LLM for
            debugging / frontend display.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    tool_calls: list[dict[str, Any]]
    scratchpad: dict[str, Any]
    routing: str
    classification_json: str


# ─────────────────────────────────────────────────────────────
# Tool list — populated from the allow-list registry (T2.2)
# ─────────────────────────────────────────────────────────────
SRE_TOOLS = ALLOWED_TOOLS


# ─────────────────────────────────────────────────────────────
# LLM factories
# ─────────────────────────────────────────────────────────────
def _build_llm(temperature: float = 0.0) -> ChatOpenAI:
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
        temperature=temperature,
        timeout=120,
    )


# ─────────────────────────────────────────────────────────────
# Triage system prompt (T3.1)
# ─────────────────────────────────────────────────────────────
TRIAGE_SYSTEM_PROMPT = """You are the Sentinel **Triage Agent** — the first responder.

Your ONLY job is to **classify** the user's message. Do NOT answer the question.
Do NOT use any tools. Output ONLY a valid JSON object (no markdown, no backticks).

## Categories

Pick EXACTLY ONE:

- **sre**: The user asks about live cluster state or wants an action performed.
  Examples: "are there any failing pods?", "show me deployments in sentinel",
  "what's the CPU usage?", "why did that pod restart?", "check the logs for...".
  These need kubectl, Prometheus metrics, or Loki log queries.

- **knowledge**: The user asks about the Sentinel project itself — how it works,
  its architecture, code, runbooks, or documentation. Examples: "how does the
  agent work?", "what is the RAG pipeline?", "explain the tool allow-list".
  These are answered from the knowledge base (vector DB with source code + docs).

- **general**: Greetings ("hello", "hi"), chit-chat, thank-yous, or anything
  that doesn't fit the above categories.

## Output format (JSON only, no markdown fences)

{"category": "<sre|knowledge|general>", "reasoning": "<one short sentence>", "refined_query": "<the user query, optionally clarified>"}"""


# ─────────────────────────────────────────────────────────────
# Specialist system prompts (T3.1)
# ─────────────────────────────────────────────────────────────
SRE_SYSTEM_PROMPT = """You are the Sentinel **SRE Agent** — a Kubernetes operations specialist.

Your job is to inspect the live cluster and answer operational questions.
You have access to these tools:
- **kubectl_get**: List Kubernetes resources (pods, deployments, services, etc.)
- **kubectl_describe**: Get detailed info about a specific resource
- **promql_query**: Query Prometheus for metrics (CPU, memory, rates, etc.)
- **logql_query**: Query Loki for recent logs
- **rag_search**: Search the Sentinel knowledge base for runbooks and documentation

## Rules
1. Always use tools to gather facts — never guess about cluster state.
2. When you use tools, explain what you're looking for and what you found.
3. If a tool fails, try an alternative approach or explain the limitation.
4. Present findings clearly with resource names, namespaces, and relevant metrics.
5. If you use rag_search results, cite the source file paths."""

KNOWLEDGE_SYSTEM_PROMPT = """You are the Sentinel **Knowledge Agent** — a project expert.

Your job is to answer questions about the Sentinel project using its knowledge base.
You have access to:
- **rag_search**: Search the vector database containing Sentinel source code,
  documentation, runbooks, architecture decisions, and past incident reports.

## Rules
1. ALWAYS search the knowledge base before answering — use rag_search.
2. Cite specific file paths and line numbers from search results.
3. If the knowledge base doesn't have the answer, say so honestly.
4. You may also use kubectl_get or other tools if the question involves live state."""


# ─────────────────────────────────────────────────────────────
# Node: Triage Agent (T3.1)
# ─────────────────────────────────────────────────────────────
def triage_agent_node(state: AgentState) -> dict[str, Any]:
    """Classify the user's query and set the routing key.

    Sends a system prompt + the user's last message to the LLM and
    expects a JSON classification.  Falls back to ``"general"`` if
    the LLM output cannot be parsed.
    """
    llm = _build_llm(temperature=0.0)

    messages = state.get("messages", [])
    if not messages:
        return {"routing": "general", "classification_json": '{"category":"general","reasoning":"empty input"}'}

    # Build triage prompt: system + last user message
    triage_messages = [SystemMessage(content=TRIAGE_SYSTEM_PROMPT)]
    # Only include the last user message (not the full history) for classification
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            triage_messages.append(m)
            break

    try:
        response: AIMessage = llm.invoke(triage_messages)
        raw = response.content if hasattr(response, "content") else str(response)
        # Clean up: strip markdown fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        classification = json.loads(raw)
        category = classification.get("category", "general").lower().strip()
        # Validate category
        if category not in ("sre", "knowledge", "general", "security"):
            category = "general"

        return {
            "routing": category,
            "classification_json": raw,
            "scratchpad": {
                **(state.get("scratchpad", {})),
                "triage_category": category,
                "triage_reasoning": classification.get("reasoning", ""),
                "triage_refined_query": classification.get("refined_query", ""),
            },
        }
    except (json.JSONDecodeError, KeyError, AttributeError, OSError, Exception) as exc:
        # Fallback: keyword-based classification (JSON parse failed OR LLM unreachable)
        last_user = ""
        for m in reversed(messages):
            if isinstance(m, HumanMessage):
                last_user = (m.content or "").lower()
                break

        if any(kw in last_user for kw in ("pod", "deploy", "node", "cluster", "metric",
                                            "cpu", "memory", "log", "crash", "restart",
                                            "kubectl", "prometheus", "loki", "namespace")):
            category = "sre"
        else:
            category = "general"

        return {
            "routing": category,
            "classification_json": json.dumps({"category": category, "reasoning": f"fallback (parse error: {exc})"}),
            "scratchpad": {
                **(state.get("scratchpad", {})),
                "triage_category": category,
                "triage_reasoning": f"Keyword fallback — classify failed: {exc}",
            },
        }


# ─────────────────────────────────────────────────────────────
# Node: SRE Agent (T2.1, updated T3.1)
# ─────────────────────────────────────────────────────────────
def sre_agent_node(state: AgentState) -> dict[str, Any]:
    """The SRE agent: calls the LLM with bound tools.

    Prepends a system prompt based on the triage routing category so
    the specialist knows how to handle the query.  If the LLM requests
    tool calls they are stored in ``tool_calls`` and routed to the
    tool-executor node.
    """
    routing = state.get("routing", "sre")

    # Pick the right system prompt
    if routing == "knowledge":
        system_prompt = KNOWLEDGE_SYSTEM_PROMPT
    else:
        system_prompt = SRE_SYSTEM_PROMPT

    llm = _build_llm(temperature=0.0)
    llm_with_tools = llm.bind_tools(SRE_TOOLS)

    # Prepend system prompt to messages if not already present
    messages = list(state.get("messages", []))
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=system_prompt)] + messages

    response: AIMessage = llm_with_tools.invoke(messages)

    result: dict[str, Any] = {"messages": [response]}

    if response.tool_calls:
        result["tool_calls"] = response.tool_calls

    return result


# ─────────────────────────────────────────────────────────────
# Router: Triage → Specialist (T3.1)
# ─────────────────────────────────────────────────────────────
def route_to_specialist(state: AgentState) -> Literal["sre_agent", "__end__"]:
    """Route to the SRE specialist based on the triage classification.

    All categories currently route to ``sre_agent`` (which adapts its
    system prompt based on ``routing``).  Future tasks (T3.2+) will
    add dedicated specialist agents (security_agent, rag_agent).
    """
    routing = state.get("routing", "general")
    if routing in ("sre", "knowledge", "general"):
        return "sre_agent"
    # security → will be added in T3.2
    return "sre_agent"


# ─────────────────────────────────────────────────────────────
# Router: SRE tool loop (T2.1)
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
# Graph builder (updated T3.1)
# ─────────────────────────────────────────────────────────────
def build_graph() -> StateGraph:
    """Build and compile the multi-agent StateGraph.

    T3.1: Triage agent is the entry point.  It classifies the query
    and routes to the SRE specialist, which loops with tools until
    it has a final answer.

    Returns a compiled graph that is ready to ``invoke()``.
    """
    builder = StateGraph(AgentState)

    # Nodes
    builder.add_node("triage_agent", triage_agent_node)
    builder.add_node("sre_agent", sre_agent_node)
    builder.add_node("tools", ToolNode(SRE_TOOLS))

    # Edges — triage first
    builder.set_entry_point("triage_agent")
    builder.add_conditional_edges(
        "triage_agent",
        route_to_specialist,
        {"sre_agent": "sre_agent", "__end__": END},
    )

    # Edges — SRE tool loop
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
