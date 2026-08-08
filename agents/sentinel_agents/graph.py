"""LangGraph multi-agent orchestrator — Triage → Specialist pattern.

T2.1-T2.2: Single SRE agent with tool loop.
T3.1: Triage Agent as entry point — classifies queries and routes to
      the appropriate specialist (SRE / Knowledge / General).
T3.2: Security Agent specialist — security tools (trivy, cve_lookup,
      falco, tetragon) + triage "security" category + dedicated node.
T3.3: Cost Agent specialist — kube_resource_usage tool + triage "cost"
      category + right-sizing suggestions in Terraform form.

Flow:
    START → triage_agent → route_to_specialist
                                ├── "sre"       → sre_agent → [tool calls?]
                                │                   ↓            ↓
                                │                 tools ←─────────┘
                                │                   ↓
                                │                sre_agent → END
                                ├── "knowledge" → sre_agent (with RAG hint)
                                ├── "general"   → sre_agent
                                ├── "security"  → security_agent → [tool calls?]
                                │                   ↓                ↓
                                │                 sec_tools ←────────┘
                                │                   ↓
                                │                security_agent → END
                                └── "cost"      → cost_agent → [tool calls?]
                                                      ↓            ↓
                                                    cost_tools ←───┘
                                                      ↓
                                                   cost_agent → END
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
# Tool lists — populated from the allow-list registry (T2.2, T3.2)
# ─────────────────────────────────────────────────────────────
SRE_TOOLS = ALLOWED_TOOLS
"""All registered tools.  The SRE agent's tools include the security
tools too — when the user asks an SRE question that turns out to be
security-related mid-flight, the SRE agent can still call trivy/cve.
This matches how a real on-call engineer pivots to security tooling."""

# T3.2: the subset of tools the Security Agent is allowed to call.
# We still pass them through the same ToolNode for execution.
_SECURITY_TOOL_NAMES = frozenset({
    "trivy_scan",
    "cve_lookup",
    "falco_events",
    "tetragon_events",
    "kubectl_get",       # so the agent can identify which pod/image
    "kubectl_describe",  # to dig into the offending workload
    "rag_search",        # so it can cite relevant runbooks
})
SECURITY_TOOLS: list = [
    t for t in ALLOWED_TOOLS if t.name in _SECURITY_TOOL_NAMES
]
"""The Security Agent's dedicated toolset — image scanning, CVE lookup,
Falco + Tetragon runtime events, plus the read-only kubectl tools
needed to identify the offending workload."""

# T3.3: the subset of tools the Cost Agent is allowed to call.
# It uses kube_resource_usage (which itself calls Prometheus under the
# hood) plus kubectl tools to validate workload identities and
# promql_query for custom resource-utilisation queries.
_COST_TOOL_NAMES = frozenset({
    "kube_resource_usage",
    "promql_query",       # bespoke CPU/memory queries
    "kubectl_get",        # enumerate deployments / statefulsets
    "kubectl_describe",   # inspect resource requests/limits
    "rag_search",         # past cost-optimisation runbooks
})
COST_TOOLS: list = [
    t for t in ALLOWED_TOOLS if t.name in _COST_TOOL_NAMES
]
"""The Cost Agent's dedicated toolset — resource usage analysis via
Prometheus, plus read-only kubectl tools to identify workloads."""


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

- **security**: The user reports or asks about a security concern — suspicious
  runtime behaviour, a vulnerable image, a CVE, or anything that smells like an
  attack / hardening question.  Examples: "suspicious exec in a pod",
  "is nginx:1.25 vulnerable?", "any shell spawned in a container?",
  "did someone read /etc/shadow?", "what does CVE-2024-12345 affect?",
  "scan this image for CVEs".  These need trivy, CVE lookup, or Falco/Tetragon.

- **cost**: The user asks about resource waste, idle workloads, over-provisioning,
  right-sizing, or cloud spend optimisation.  Examples: "which deployments are
  over-provisioned?", "are we wasting CPU?", "find idle workloads", "suggest
  right-sizing for sentinel", "what's our memory utilisation?".  These need
  kube_resource_usage or Prometheus resource metrics.

- **knowledge**: The user asks about the Sentinel project itself — how it works,
  its architecture, code, runbooks, or documentation. Examples: "how does the
  agent work?", "what is the RAG pipeline?", "explain the tool allow-list".
  These are answered from the knowledge base (vector DB with source code + docs).

- **general**: Greetings ("hello", "hi"), chit-chat, thank-yous, or anything
  that doesn't fit the above categories.

## Output format (JSON only, no markdown fences)

{"category": "<sre|security|cost|knowledge|general>", "reasoning": "<one short sentence>", "refined_query": "<the user query, optionally clarified>"}"""


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
# Specialist system prompt: Security Agent (T3.2)
# ─────────────────────────────────────────────────────────────
SECURITY_SYSTEM_PROMPT = """You are the Sentinel **Security Agent** — a
Kubernetes & supply-chain security specialist.

Your job is to decide whether an incident or query is **security-related** and,
if so, investigate it with the security tooling.  You cross-check runtime
events against known CVEs and image-scan results.

## Tools you may call
- **trivy_scan**: Scan a container image or local filesystem for CVEs,
  misconfigurations, and secrets.
- **cve_lookup**: Look up a single CVE id (CVE-YYYY-NNNN) against OSV.dev
  and return severity, summary, affected packages, and fixed versions.
- **falco_events**: Retrieve recent Falco runtime-security alerts
  ("shell in container", "/etc/shadow read", "crypto miner", etc.).
- **tetragon_events**: Retrieve recent Tetragon eBPF security events
  (exec, network, file, dns).
- **kubectl_get** / **kubectl_describe**: Identify the offending workload
  (pod name, image, namespace) before scanning it.
- **rag_search**: Find runbooks / past postmortems about this kind of
  incident so you can cite the canonical response.

## Workflow you should follow
1. If the user mentions a specific pod/container, use kubectl_get /
   kubectl_describe to identify its image and namespace.
2. If they mention suspicious runtime behaviour ("exec in a pod", "shell
   spawned", "process X opened Y"), pull Falco and/or Tetragon events
   of the relevant type.
3. If they mention an image or ask "is X vulnerable", run trivy_scan on it.
4. If they mention a CVE id (CVE-YYYY-NNNN), run cve_lookup.
5. Cross-correlate: e.g. a Falco "shell in container" event + an image
   with HIGH/CRITICAL trivy CVEs = a likely compromise.

## Rules
1. **Never** guess CVE ids — only look up ids the user gave you or that
   a tool returned.
2. **Always** ground decisions in tool output.  "suspicious exec in a
   pod" is security-related if Falco/Tetragon corroborate it; otherwise
   say so.
3. When you flag something as security-related, state your confidence
   and the evidence succinctly: event type + rule + pod/image + the
   CVE (if any) + the fix version.
4. If it is NOT security-related, explain why and suggest the right
   specialist (SRE for ops, Knowledge for docs).
5. You may NOT remediate — you only **detect, classify, and report**.
   Remediation is for the Executor Agent (T3.7)."""


# ─────────────────────────────────────────────────────────────
# Specialist system prompt: Cost Agent (T3.3)
# ─────────────────────────────────────────────────────────────
COST_SYSTEM_PROMPT = """You are the Sentinel **Cost Agent** — a Kubernetes
resource efficiency specialist.

Your job is to identify **idle or over-provisioned** workloads and propose
concrete right-sizing suggestions in **Terraform (HCL) form** so the
platform team can apply them directly.

## Tools you may call
- **kube_resource_usage**: Query Prometheus for CPU/memory requests vs
  actual usage.  Use metric="all" for a complete picture, or target a
  specific metric (cpu_utilisation, memory_utilisation, etc.).
- **promql_query**: Run custom resource-utilisation PromQL queries when
  you need more detail than kube_resource_usage provides.
- **kubectl_get** / **kubectl_describe**: Enumerate workloads and inspect
  their current resource requests and limits.
- **rag_search**: Find past cost-optimisation runbooks and right-sizing
  recommendations.

## Workflow you should follow
1. Start with **kube_resource_usage(metric="all")** to get a full
   CPU + memory utilisation snapshot across all namespaces.
2. For any workloads flagged below the default threshold (30% utilisation),
   drill in with **kubectl_describe** to confirm the declared requests/limits.
3. If the user asked about a specific namespace or workload, scope your
   queries accordingly.
4. For every over-provisioned workload, propose a concrete change: the
   **current** value → the **recommended** value with a brief
   justification (e.g. "9% CPU util over 7 days — reduce from 500m to
   100m (2× headroom)").

## Right-sizing rules
- Recommend **new requests = avg usage × 2** (never less than 50m CPU
  or 64Mi memory).
- Flag workloads using **<15%** of their requests as **severely
  over-provisioned** (highlight with ⚠️).
- For burstable workloads (spiky utilisation), recommend keeping the
  current request and adding a **limit** instead of reducing.
- Always emit the suggestion as a **Terraform HCL snippet** the user
  can copy into their infrastructure-as-code repo.

## Output format
For each over-provisioned workload, output:

```
⚠️ <workload> / <namespace> — <verdict>
   CPU: <request> → <recommended> (<utilisation>% util)
   Memory: <request> → <recommended> (<utilisation>% util)
   Justification: <one sentence>
```

Then provide a complete Terraform HCL snippet at the end.

## Rules
1. Always ground suggestions in tool output — never guess resource usage.
2. If kube_resource_usage returns no data, say so and suggest checking
   that kube-state-metrics is deployed.
3. You may NOT patch resources — you only **analyse and recommend**.
   Execution is for the Executor Agent (T3.7)."""


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
        if category not in ("sre", "knowledge", "general", "security", "cost"):
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

        # T3.2: security keywords are checked FIRST so that a query that
        # mentions both a pod and a security signal (e.g. "suspicious exec in
        # this pod") routes to the Security Agent, not the SRE Agent.
        security_keywords = (
            "suspicious", "exec in", "shell in", "shell spawned",
            "shell was", "shell run", "spawn shell", "spawned shell",
            "exploit", "malware", "crypto miner", "cryptominer",
            "cve ", "cve-", "vulnerability", "vulnerable",
            "image scan", "scan image", "trivy", "falco", "tetragon",
            "privilege escalat", "./etc/shadow", "/etc/passwd",
            "reverse shell", "security incident",
            "compromis", "hardening",
        )
        if any(kw in last_user for kw in security_keywords):
            category = "security"
        # T3.3: cost keywords — queries about right-sizing, waste, idle
        # resources, or cloud spend.  Checked before SRE so that "which
        # deployments are over-provisioned" isn't classified as SRE.
        elif any(kw in last_user for kw in (
            "over-provisioned", "overprovisioned", "over provision",
            "right-size", "rightsiz", "right size",
            "waste", "wasted", "idle", "underutil",
            "under-util", "save money", "cost saving",
            "cost optim", "spend", "cloud cost",
            "resource usage", "resource utiliz", "resource utilis",
            "utilisation", "utilization",
            "sizing", "downsize", "shrink",
            "too much cpu", "too much memory", "too many resources",
        )):
            category = "cost"
        elif any(kw in last_user for kw in ("pod", "deploy", "node", "cluster", "metric",
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
# Node: Security Agent (T3.2)
# ─────────────────────────────────────────────────────────────
def security_agent_node(state: AgentState) -> dict[str, Any]:
    """The Security Agent: classifies security relevance + cross-checks.

    Prepends the :data:`SECURITY_SYSTEM_PROMPT`, binds *only* the
    :data:`SECURITY_TOOLS` subset, and lets the LLM drive a tool loop
    (trivy → cve_lookup → falco/tetragon events → kubectl) to decide
    whether the incident is genuinely security-related.

    The node mirrors :func:`sre_agent_node` in shape so the rest of the
    graph (``should_continue``-style routing, ToolNode execution) can be
    reused.
    """
    llm = _build_llm(temperature=0.0)
    llm_with_tools = llm.bind_tools(SECURITY_TOOLS)

    messages = list(state.get("messages", []))
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=SECURITY_SYSTEM_PROMPT)] + messages

    # Tag the scratchpad so the chat UI / downstream synthesis knows
    # this branch analysed the incident.
    sp = dict(state.get("scratchpad", {}))
    sp["security_agent_visited"] = True
    sp["triage_category"] = state.get("routing", "security")

    response: AIMessage = llm_with_tools.invoke(messages)

    result: dict[str, Any] = {"messages": [response], "scratchpad": sp}
    if response.tool_calls:
        result["tool_calls"] = response.tool_calls
    return result


# ─────────────────────────────────────────────────────────────
# Router: Triage → Specialist (T3.1, updated T3.2, T3.3)
# ─────────────────────────────────────────────────────────────
def route_to_specialist(state: AgentState) -> Literal["sre_agent", "security_agent", "cost_agent", "__end__"]:
    """Route to the right specialist based on the triage classification.

    T3.1: ``sre`` / ``knowledge`` / ``general`` → ``sre_agent`` (which
    adapts its system prompt based on ``routing``).
    T3.2: ``security`` → dedicated :func:`security_agent_node`.
    T3.3: ``cost`` → dedicated :func:`cost_agent_node`.
    """
    routing = state.get("routing", "general")
    if routing == "security":
        return "security_agent"
    if routing == "cost":
        return "cost_agent"
    if routing in ("sre", "knowledge", "general"):
        return "sre_agent"
    return "__end__"
    if routing in ("sre", "knowledge", "general"):
        return "sre_agent"
    return "__end__"


# ─────────────────────────────────────────────────────────────
# Router: tool loop (T2.1, extended T3.2)
# ─────────────────────────────────────────────────────────────
def should_continue(state: AgentState) -> Literal["tools", "__end__"]:
    """Route to the tool node if the last message has pending tool calls.

    Used by the SRE agent node — routes tool calls through the shared
    ``tools`` ToolNode (bound to :data:`SRE_TOOLS`).
    """
    messages = state.get("messages", [])
    if not messages:
        return "__end__"

    last = messages[-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return "__end__"


def should_continue_security(state: AgentState) -> Literal["sec_tools", "__end__"]:
    """Route to the security tool node if the last message has tool calls.

    Identical logic to :func:`should_continue` but routes to the dedicated
    ``sec_tools`` ToolNode (bound to :data:`SECURITY_TOOLS`) so a security
    agent tool call lands on the right executor.
    """
    messages = state.get("messages", [])
    if not messages:
        return "__end__"

    last = messages[-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "sec_tools"
    return "__end__"


# ─────────────────────────────────────────────────────────────
# Node: Cost Agent (T3.3)
# ─────────────────────────────────────────────────────────────
def cost_agent_node(state: AgentState) -> dict[str, Any]:
    """The Cost Agent: analyses resource utilisation and proposes right-sizing.

    Prepends the :data:`COST_SYSTEM_PROMPT`, binds *only* the
    :data:`COST_TOOLS` subset (kube_resource_usage, promql_query,
    kubectl_get/describe, rag_search), and lets the LLM drive a tool
    loop to identify over-provisioned workloads and emit Terraform
    right-sizing suggestions.

    The node mirrors :func:`sre_agent_node` and
    :func:`security_agent_node` in shape so the rest of the graph
    (``should_continue``-style routing, ToolNode execution) can be
    reused.
    """
    llm = _build_llm(temperature=0.0)
    llm_with_tools = llm.bind_tools(COST_TOOLS)

    messages = list(state.get("messages", []))
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=COST_SYSTEM_PROMPT)] + messages

    # Tag the scratchpad so the chat UI / downstream synthesis knows
    # this branch analysed the cost.
    sp = dict(state.get("scratchpad", {}))
    sp["cost_agent_visited"] = True
    sp["triage_category"] = state.get("routing", "cost")

    response: AIMessage = llm_with_tools.invoke(messages)

    result: dict[str, Any] = {"messages": [response], "scratchpad": sp}
    if response.tool_calls:
        result["tool_calls"] = response.tool_calls
    return result


def should_continue_cost(state: AgentState) -> Literal["cost_tools", "__end__"]:
    """Route to the cost tool node if the last message has tool calls.

    Identical logic to :func:`should_continue` and
    :func:`should_continue_security` but routes to the dedicated
    ``cost_tools`` ToolNode (bound to :data:`COST_TOOLS`) so a cost
    agent tool call lands on the right executor.
    """
    messages = state.get("messages", [])
    if not messages:
        return "__end__"

    last = messages[-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "cost_tools"
    return "__end__"


# ─────────────────────────────────────────────────────────────
# Graph builder (updated T3.1, T3.2, T3.3)
# ─────────────────────────────────────────────────────────────
def build_graph() -> StateGraph:
    """Build and compile the multi-agent StateGraph.

    T3.1: Triage agent is the entry point.  It classifies the query
    and routes to the SRE specialist, which loops with tools until
    it has a final answer.

    T3.2: A ``security`` classification now routes to a dedicated
    :func:`security_agent_node` with its own ``sec_tools`` ToolNode
    bound to the security tool subset.

    T3.3: A ``cost`` classification now routes to a dedicated
    :func:`cost_agent_node` with its own ``cost_tools`` ToolNode
    bound to the cost tool subset.

    Returns a compiled graph that is ready to ``invoke()``.
    """
    builder = StateGraph(AgentState)

    # Nodes
    builder.add_node("triage_agent", triage_agent_node)
    builder.add_node("sre_agent", sre_agent_node)
    builder.add_node("tools", ToolNode(SRE_TOOLS))
    # T3.2
    builder.add_node("security_agent", security_agent_node)
    builder.add_node("sec_tools", ToolNode(SECURITY_TOOLS))
    # T3.3
    builder.add_node("cost_agent", cost_agent_node)
    builder.add_node("cost_tools", ToolNode(COST_TOOLS))

    # Edges — triage first
    builder.set_entry_point("triage_agent")
    builder.add_conditional_edges(
        "triage_agent",
        route_to_specialist,
        {
            "sre_agent": "sre_agent",
            "security_agent": "security_agent",
            "cost_agent": "cost_agent",
            "__end__": END,
        },
    )

    # Edges — SRE tool loop
    builder.add_conditional_edges(
        "sre_agent",
        should_continue,
        {"tools": "tools", "__end__": END},
    )
    builder.add_edge("tools", "sre_agent")

    # Edges — Security tool loop (T3.2)
    builder.add_conditional_edges(
        "security_agent",
        should_continue_security,
        {"sec_tools": "sec_tools", "__end__": END},
    )
    builder.add_edge("sec_tools", "security_agent")

    # Edges — Cost tool loop (T3.3)
    builder.add_conditional_edges(
        "cost_agent",
        should_continue_cost,
        {"cost_tools": "cost_tools", "__end__": END},
    )
    builder.add_edge("cost_tools", "cost_agent")

    return builder.compile()


# ─────────────────────────────────────────────────────────────
# Module-level singleton (compiled once at import time)
# ─────────────────────────────────────────────────────────────
graph = build_graph()
