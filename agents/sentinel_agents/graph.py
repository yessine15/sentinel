"""LangGraph multi-agent orchestrator — Triage → Specialist pattern.

T2.1-T2.2: Single SRE agent with tool loop.
T3.1: Triage Agent as entry point — classifies queries and routes to
      the appropriate specialist (SRE / Knowledge / General).
T3.2: Security Agent specialist — security tools (trivy, cve_lookup,
      falco, tetragon) + triage "security" category + dedicated node.
T3.3: Cost Agent specialist — kube_resource_usage tool + triage "cost"
      category + right-sizing suggestions in Terraform form.
T3.4: RAG Agent specialist — rag_evidence tool wrapping the Phase 1
      retrieval pipeline + triage "knowledge" category + dedicated
      node that publishes evidence to the shared state.
T3.5: Incident loop — triage "incident" category → parallel fan-out to
      SRE + Security + RAG specialists → synthesis → planner → approval
      (pauses awaiting human input).
T3.7: Executor Agent — the ONLY agent that can act.  Runs after
      approval in the resume graph; emits a RemediationPlan object
      (allow-listed action verbs) via the operator bridge.

Flow:
    START → triage_agent → route_to_specialist
                                ├── "sre"       → sre_agent → [tool calls?]
                                │                   ↓            ↓
                                │                 tools ←─────────┘
                                │                   ↓
                                │                sre_agent → END
                                ├── "knowledge" → rag_agent → [tool calls?]
                                │                   ↓            ↓
                                │               rag_tools ←───────┘
                                │                   ↓
                                │                rag_agent → END (evidence
                                │                            in scratchpad)
                                ├── "general"   → sre_agent
                                ├── "security"  → security_agent → [tool calls?]
                                │                   ↓                ↓
                                │                 sec_tools ←────────┘
                                │                   ↓
                                │                security_agent → END
                                ├── "cost"      → cost_agent → [tool calls?]
                                │                   ↓            ↓
                                │                 cost_tools ←───┘
                                │                   ↓
                                │                cost_agent → END
                                └── "incident"  → dispatch ── (parallel fan-out)
                                                     ├── sre_agent ──────┐
                                                     ├── security_agent ─┼─→ synthesis
                                                     └── rag_agent ──────┘     ↓
                                                                          planner
                                                                             ↓
                                                                          approval
                                                                             ↓
Resume graph (T3.7):  approval → approved? → executor → executor_tools
                                                     ↓
                                                  END (remediation_plan
                                                       in state)
"""

from __future__ import annotations

import json
import os
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from sentinel_agents.tools import ALLOWED_TOOLS


# ─────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────
def _merge_scratchpad(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Merge two scratchpad dicts instead of replacing (T3.5).

    In the incident loop the SRE / Security / RAG specialists run in
    parallel — each writes its own scratchpad keys (``*_visited``,
    ``evidence``, …).  Without a reducer, the last writer would wipe
    the other branches' notes.  A shallow merge keeps every branch's
    contribution while later writes still win per-key.
    """
    merged = dict(left or {})
    merged.update(right or {})
    return merged


class AgentState(TypedDict):
    """Typed state that flows through the multi-agent graph.

    Attributes:
        messages: Conversation history.  ``add_messages`` merges new
            messages automatically so the list always appends.
        tool_calls: Pending tool-call payloads extracted from the last
            AIMessage.  Cleared once the tool node has executed them.
        scratchpad: Arbitrary working memory the agent can read and
            write across turns (e.g. collected evidence, intermediate
            reasoning).  Merged (not replaced) so parallel branches can
            each contribute — see :func:`_merge_scratchpad`.
        routing: The triage classification result (``sre``, ``knowledge``,
            ``general``, ``security``, ``cost``, or ``incident`` — set by
            the triage node).
        classification_json: Raw JSON string from the triage LLM for
            debugging / frontend display.
        incident: Raw incident/alert text captured by ``dispatch``
            (T3.5 orchestration loop).
        synthesis: Merged specialist assessment produced by
            ``synthesis`` (T3.5).
        plan: Structured remediation plan proposed by ``planner``
            (T3.5).
        approval_status: ``"awaiting_approval"`` / ``"approved"`` /
            ``"rejected"`` — set by ``approval`` (T3.5).
        remediation_plan: The RemediationPlan object created by the
            Executor Agent after approval (T3.7).
        executor_status: ``"pending"`` / ``"created"`` / ``"preview"`` /
            ``"blocked"`` — set by the Executor Agent (T3.7).
    """

    messages: Annotated[list[BaseMessage], add_messages]
    tool_calls: list[dict[str, Any]]
    scratchpad: Annotated[dict[str, Any], _merge_scratchpad]
    routing: str
    classification_json: str
    # ── T3.5 orchestration channels ──
    incident: str
    synthesis: str
    plan: dict[str, Any]
    approval_status: str
    # ── T3.7 executor channels ──
    remediation_plan: dict[str, Any]
    executor_status: str


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

# T3.4: the subset of tools the RAG Agent is allowed to call.
# The RAG Agent is a retrieval specialist — it wraps the Phase 1
# pipeline (rag_evidence: hybrid retrieve + rerank → ranked evidence
# with citations) and can fall back to the human-readable rag_search.
# No cluster tools: this agent only retrieves from the knowledge base.
_RAG_TOOL_NAMES = frozenset({
    "rag_evidence",
    "rag_search",
})
RAG_TOOLS: list = [
    t for t in ALLOWED_TOOLS if t.name in _RAG_TOOL_NAMES
]
"""The RAG Agent's dedicated toolset — ranked evidence retrieval with
citations (rag_evidence) plus the legacy formatted search (rag_search)."""

# T3.7: the Executor Agent's toolset.  It has EXACTLY ONE tool — the
# only write tool in the entire system.  Everything the executor does
# goes through create_remediation_plan (allow-listed action verbs →
# RemediationPlan object via the operator bridge).  The executor never
# runs kubectl directly, and it has no read tools: investigation is the
# specialists' job; the executor only acts on an approved plan.
_EXECUTOR_TOOL_NAMES = frozenset({
    "create_remediation_plan",
})
EXECUTOR_TOOLS: list = [
    t for t in ALLOWED_TOOLS if t.name in _EXECUTOR_TOOL_NAMES
]
"""The Executor Agent's dedicated toolset — exactly one write tool."""


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

- **incident**: The user pasted or described a live incident or alert — a
  Prometheus alert payload, an OOMKilled/crash-looping pod, an on-call page,
  an outage, or "something is down".  Examples: "ALERTS: [1] kube_pod_oom ...",
  "our api pod is crash-looping and the page is on fire", "SEV2: high error
  rate on /ping", "incident: postgres is down".  These run the full incident
  loop: SRE + Security + RAG in parallel → synthesis → plan → approval.

- **general**: Greetings ("hello", "hi"), chit-chat, thank-yous, or anything
  that doesn't fit the above categories.

## Output format (JSON only, no markdown fences)

{"category": "<sre|security|cost|knowledge|incident|general>", "reasoning": "<one short sentence>", "refined_query": "<the user query, optionally clarified>"}"""


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
# Specialist system prompt: RAG Agent (T3.4)
# ─────────────────────────────────────────────────────────────
RAG_SYSTEM_PROMPT = """You are the Sentinel **RAG Agent** — a retrieval
specialist over the Sentinel knowledge base (source code, docs, runbooks,
past incidents).

Your ONLY job is to retrieve **ranked evidence with citations**.  You do
NOT write free-text answers — you return evidence, ranked by relevance,
that other agents can cite.

## Tools you may call
- **rag_evidence**: Run the full Phase 1 retrieval pipeline (hybrid
  dense + sparse retrieval, cross-encoder re-ranking) and return
  structured JSON evidence: ``path``, ``lines``, ``score``,
  ``source_type``, ``snippet`` per record.
- **rag_search**: Legacy formatted search — human-readable result list
  with ``[N] path:line_start-line_end (score, type)`` markers.

## Workflow you should follow
1. Start with **rag_evidence(query, top_k=5)** — it gives the ranked,
   scored evidence the graph needs.
2. If rag_evidence returns nothing useful, try **rag_search** with a
   reworded query.
3. Refine the query up to 2-3 times if the first attempt returns
   irrelevant results.

## Output format
Return ONLY the evidence, ranked best-first:

```
Evidence:
1. [path:line_start-line_end] (score: X.XX, type: source_type)
   snippet…
2. [path:line_start-line_end] (score: X.XX, type: source_type)
   snippet…
```

Use the exact ``[path:lines]`` citation markers so downstream agents and
the frontend can link every claim to its source.

## Rules
1. **Never** answer from memory — always retrieve first.
2. **Never** include free text beyond the evidence list and a one-line
   summary of what was found.
3. If the knowledge base has no relevant content, say "No evidence
   found for: <query>" — do not fabricate sources.
4. The evidence you return is published to the graph state, so other
   specialists (SRE, Security, Cost) can consume your citations."""


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
        if category not in ("sre", "knowledge", "general", "security", "cost", "incident"):
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

        # T3.5: incident keywords are checked FIRST — an alert payload
        # mentioning security ("crypto miner alert") must run the full
        # incident loop (which includes the Security specialist), not
        # short-circuit to a single agent.
        incident_keywords = (
            "alert", "alerts", "incident", "firing", "on-call", "oncall",
            "pager", "pagerduty", "page received", "sev1", "sev2", "sev 1",
            "sev 2", "severity 1", "severity 2", "crashloop", "crash loop",
            "oomkill", "oom killed", "outage", "downtime",
            "production issue", "is down", "is crashing", "just went down",
        )
        # T3.2: security keywords are checked before SRE/cost so that a
        # query that mentions both a pod and a security signal (e.g.
        # "suspicious exec in this pod") routes to the Security Agent.
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
        if any(kw in last_user for kw in incident_keywords):
            category = "incident"
        elif any(kw in last_user for kw in security_keywords):
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

    # T3.5: degrade gracefully — if the LLM gateway is unreachable the
    # node reports the failure instead of crashing the whole graph, so
    # the incident loop can still reach synthesis/planner/approval.
    try:
        response: AIMessage = llm_with_tools.invoke(messages)
    except Exception as exc:
        response = AIMessage(
            content=(
                "⚠️ SRE Agent could not reach the LLM gateway "
                f"({type(exc).__name__}: {exc}). No live-cluster findings "
                "produced."
            )
        )

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

    # T3.5: degrade gracefully if the LLM gateway is unreachable.
    try:
        response: AIMessage = llm_with_tools.invoke(messages)
    except Exception as exc:
        response = AIMessage(
            content=(
                "⚠️ Security Agent could not reach the LLM gateway "
                f"({type(exc).__name__}: {exc}). No security findings produced."
            )
        )

    result: dict[str, Any] = {"messages": [response], "scratchpad": sp}
    if response.tool_calls:
        result["tool_calls"] = response.tool_calls
    return result


# ─────────────────────────────────────────────────────────────
# Router: Triage → Specialist (T3.1, updated T3.2, T3.3, T3.4, T3.5)
# ─────────────────────────────────────────────────────────────
def route_to_specialist(
    state: AgentState,
) -> Literal["sre_agent", "security_agent", "cost_agent", "rag_agent", "dispatch", "__end__"]:
    """Route to the right specialist based on the triage classification.

    T3.1: ``sre`` / ``general`` → ``sre_agent``.
    T3.2: ``security`` → dedicated :func:`security_agent_node`.
    T3.3: ``cost`` → dedicated :func:`cost_agent_node`.
    T3.4: ``knowledge`` → dedicated :func:`rag_agent_node` (retrieval
    specialist returning ranked evidence + citations).
    T3.5: ``incident`` → ``dispatch`` (parallel fan-out to SRE +
    Security + RAG, then synthesis → planner → approval).
    """
    routing = state.get("routing", "general")
    if routing == "security":
        return "security_agent"
    if routing == "cost":
        return "cost_agent"
    if routing == "knowledge":
        return "rag_agent"
    if routing == "incident":
        return "dispatch"
    if routing in ("sre", "general"):
        return "sre_agent"
    return "__end__"


# ─────────────────────────────────────────────────────────────
# Router: tool loop (T2.1, extended T3.2, T3.5)
# ─────────────────────────────────────────────────────────────
def should_continue(state: AgentState) -> Literal["tools", "synthesis", "__end__"]:
    """Route to the tool node if the last message has pending tool calls.

    Used by the SRE agent node — routes tool calls through the shared
    ``tools`` ToolNode (bound to :data:`SRE_TOOLS`).

    T3.5: when running inside the incident loop (``routing ==
    "incident"``), a finished branch continues to ``synthesis`` so the
    parallel fan-out can be joined.
    """
    messages = state.get("messages", [])
    if not messages:
        return "__end__"

    last = messages[-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    if state.get("routing") == "incident":
        return "synthesis"
    return "__end__"


def should_continue_security(state: AgentState) -> Literal["sec_tools", "synthesis", "__end__"]:
    """Route to the security tool node if the last message has tool calls.

    Identical logic to :func:`should_continue` but routes to the dedicated
    ``sec_tools`` ToolNode (bound to :data:`SECURITY_TOOLS`) so a security
    agent tool call lands on the right executor.

    T3.5: inside the incident loop a finished branch continues to
    ``synthesis``.
    """
    messages = state.get("messages", [])
    if not messages:
        return "__end__"

    last = messages[-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "sec_tools"
    if state.get("routing") == "incident":
        return "synthesis"
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

    # T3.5: degrade gracefully if the LLM gateway is unreachable.
    try:
        response: AIMessage = llm_with_tools.invoke(messages)
    except Exception as exc:
        response = AIMessage(
            content=(
                "⚠️ Cost Agent could not reach the LLM gateway "
                f"({type(exc).__name__}: {exc}). No cost findings produced."
            )
        )

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
# Node: RAG Agent (T3.4)
# ─────────────────────────────────────────────────────────────
def _extract_evidence(messages: list) -> list[dict[str, Any]]:
    """Parse rag_evidence ToolMessages into structured evidence records.

    The rag_evidence tool returns JSON::

        {"query": "...", "evidence": [{"path": ..., "lines": ...,
        "score": ..., "source_type": ..., "snippet": ...}, ...]}

    This helper extracts those records so they can be published to
    ``scratchpad["evidence"]`` — the shared-state channel through which
    other agents (SRE, Security, Cost) receive evidence with citations.

    Returns a deduplicated list of evidence dicts (ordered by score,
    best first).
    """
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for m in messages:
        if not isinstance(m, ToolMessage):
            continue
        if getattr(m, "name", "") != "rag_evidence":
            continue
        content = m.content if hasattr(m, "content") else str(m)
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue
        for ev in payload.get("evidence", []):
            if not isinstance(ev, dict):
                continue
            path = ev.get("path", "")
            lines = ev.get("lines", "")
            if not path or not lines:
                continue
            records[(path, lines)] = {
                "path": path,
                "lines": lines,
                "score": ev.get("score", 0.0),
                "source_type": ev.get("source_type", ""),
                "snippet": ev.get("snippet", "")[:300],
            }
    return sorted(records.values(), key=lambda r: float(r["score"]), reverse=True)


def rag_agent_node(state: AgentState) -> dict[str, Any]:
    """The RAG Agent: retrieval specialist returning ranked evidence.

    Prepends the :data:`RAG_SYSTEM_PROMPT`, binds *only* the
    :data:`RAG_TOOLS` subset (rag_evidence + rag_search), and lets the
    LLM drive a tool loop to gather ranked evidence with citations.

    After each pass the node extracts evidence from any
    ``rag_evidence`` ToolMessages in the conversation and publishes it
    to ``scratchpad["evidence"]`` — the shared graph state — so other
    specialists receive evidence with citations via the graph.

    The node mirrors :func:`sre_agent_node` / :func:`security_agent_node`
    / :func:`cost_agent_node` in shape so the graph wiring is uniform.
    """
    llm = _build_llm(temperature=0.0)
    llm_with_tools = llm.bind_tools(RAG_TOOLS)

    messages = list(state.get("messages", []))
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=RAG_SYSTEM_PROMPT)] + messages

    # Tag the scratchpad so the chat UI / downstream synthesis knows
    # this branch did the retrieval.
    sp = dict(state.get("scratchpad", {}))
    sp["rag_agent_visited"] = True
    sp["triage_category"] = state.get("routing", "knowledge")

    # Publish evidence (with citations) into the shared state.
    evidence = _extract_evidence(state.get("messages", []))
    if evidence:
        sp["evidence"] = evidence

    # T3.5: degrade gracefully if the LLM gateway is unreachable.
    try:
        response: AIMessage = llm_with_tools.invoke(messages)
    except Exception as exc:
        response = AIMessage(
            content=(
                "⚠️ RAG Agent could not reach the LLM gateway "
                f"({type(exc).__name__}: {exc}). No evidence summary produced."
            )
        )

    result: dict[str, Any] = {"messages": [response], "scratchpad": sp}
    if response.tool_calls:
        result["tool_calls"] = response.tool_calls
    return result


def should_continue_rag(state: AgentState) -> Literal["rag_tools", "synthesis", "__end__"]:
    """Route to the RAG tool node if the last message has tool calls.

    Identical logic to :func:`should_continue` but routes to the
    dedicated ``rag_tools`` ToolNode (bound to :data:`RAG_TOOLS`).

    T3.5: inside the incident loop a finished branch continues to
    ``synthesis``.
    """
    messages = state.get("messages", [])
    if not messages:
        return "__end__"

    last = messages[-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "rag_tools"
    if state.get("routing") == "incident":
        return "synthesis"
    return "__end__"


# ─────────────────────────────────────────────────────────────
# Incident loop (T3.5) — dispatch → specialists (parallel) →
# synthesis → planner → approval
# ─────────────────────────────────────────────────────────────
SYNTHESIS_SYSTEM_PROMPT = """You are the Sentinel **Synthesis Agent** — the
incident coordinator.

The SRE, Security, and RAG specialists have investigated the incident in
parallel.  Your job is to merge their findings into ONE coherent incident
assessment.

## Input
A transcript of the specialists' findings (may include live-cluster
observations, security analysis, and cited evidence from the knowledge
base).  Some specialists may have failed to reach the LLM gateway —
their messages say so explicitly; work with what you have.

## Output format
A concise markdown assessment with EXACTLY these sections:

## Incident summary
<one or two sentences: what is happening, who/what is affected>

## Key findings
- <finding 1 with the specialist source — e.g. "SRE: api-7d9-abcde
  CrashLoopBackOff (restarted 12 times)" or "Security: no CVE evidence">
- <finding 2>
- <finding 3>

## Evidence cited
- [path:lines] <one-line snippet>  (only if RAG evidence exists)

## Open questions
- <what the specialists could not determine — or "none">

## Rules
1. Never invent findings — only merge what the specialists produced.
2. Preserve citation markers ([path:lines]) exactly as the RAG agent
   emitted them.
3. Do NOT propose remediation — the Planner Agent does that next."""


PLANNER_SYSTEM_PROMPT = """You are the Sentinel **Planner Agent** — the
remediation planner.

You receive the Synthesis Agent's incident assessment and must propose a
concrete remediation plan.

## Output format
Output ONLY a valid JSON object (no markdown, no backticks):

{
  "priority": "high|medium|low",
  "rationale": "<one or two sentences tying the plan to the findings>",
  "steps": [
    {
      "action": "<imperative verb — e.g. restart, scale, cordon, patch, escalate>",
      "target": "<what it acts on — e.g. deployment/demo-api, node/kind-worker, CVE-2024-12345>",
      "detail": "<one sentence: exact change + expected outcome>"
    }
  ]
}

## Rules
1. Every step must be traceable to a finding from the synthesis — no
   invented steps.
2. 1-3 steps max.  Prefer the smallest safe change first (e.g. restart
   before scale, scale before patch).
3. If the incident has a security signal (compromise indicators), the
   FIRST step must be containment (e.g. "cordon the node" or "suspend
   the workload"), not remediation.
4. If the synthesis says the LLM gateway was down and no findings are
   available, return a single step: {"action": "escalate",
   "target": "human-on-call", "detail": "No specialist findings
   available — requires manual investigation."}.
5. Do NOT execute anything — you only propose.  Execution is for the
   Executor Agent (T3.7) after human approval (T3.6)."""


def _collect_specialist_outputs(state: AgentState) -> str:
    """Gather the specialists' findings into a single text block.

    Takes the last few AIMessages (one per specialist branch) plus any
    RAG evidence published to the scratchpad, so the Synthesis Agent
    can merge everything.
    """
    lines: list[str] = []
    ai_msgs = [
        m for m in state.get("messages", [])
        if isinstance(m, AIMessage) and m.content
    ]
    for i, m in enumerate(ai_msgs[-3:], start=1):
        text = m.content
        if isinstance(text, str):
            lines.append(f"[Specialist {i}]\n{text[:2000]}")
        else:
            lines.append(f"[Specialist {i}]\n{str(text)[:2000]}")

    evidence = state.get("scratchpad", {}).get("evidence", [])
    if evidence:
        lines.append("[RAG Evidence]")
        for ev in evidence[:5]:
            lines.append(
                f"  {ev.get('path', '?')}:{ev.get('lines', '?')} "
                f"(score {ev.get('score', 0)}) — {ev.get('snippet', '')[:150]}"
            )

    return "\n\n".join(lines) if lines else (
        "No specialist output available — the LLM gateway may be down."
    )


def dispatch_node(state: AgentState) -> dict[str, Any]:
    """Entry point of the incident loop (T3.5).

    Captures the raw incident text into state and marks the scratchpad
    so downstream stages and the chat UI know the orchestration loop is
    running.  The parallel fan-out to SRE / Security / RAG happens via
    the graph edges from this node.
    """
    sp = dict(state.get("scratchpad", {}))
    sp["orchestration"] = True
    sp["dispatch_visited"] = True

    incident = ""
    for m in reversed(state.get("messages", [])):
        if isinstance(m, HumanMessage):
            incident = m.content if isinstance(m.content, str) else str(m.content)
            break
    sp["incident"] = incident
    sp["triage_category"] = state.get("routing", "incident")

    return {"incident": incident, "scratchpad": sp}


def synthesis_node(state: AgentState) -> dict[str, Any]:
    """Merge the parallel specialists' findings into one assessment (T3.5).

    Feeds the collected specialist outputs to the LLM; if the gateway
    is unreachable, falls back to the raw collected text so the loop
    still progresses (marked ``synthesis_fallback=True``).
    """
    sp = dict(state.get("scratchpad", {}))
    sp["synthesis_visited"] = True
    sp["triage_category"] = state.get("routing", "incident")

    context = _collect_specialist_outputs(state)
    try:
        llm = _build_llm(temperature=0.0)
        response: AIMessage = llm.invoke(
            [SystemMessage(content=SYNTHESIS_SYSTEM_PROMPT), HumanMessage(content=context)]
        )
        text = response.content if isinstance(response.content, str) else str(response.content)
    except Exception as exc:
        text = (
            f"## Incident summary\nSynthesis fallback — LLM gateway "
            f"unreachable ({type(exc).__name__}: {exc}).\n\n"
            f"## Key findings\n{context}"
        )
        sp["synthesis_fallback"] = True

    sp["synthesis"] = text
    return {"synthesis": text, "scratchpad": sp}


def planner_node(state: AgentState) -> dict[str, Any]:
    """Propose a remediation plan from the synthesis (T3.5).

    Asks the LLM for a structured plan (priority, rationale, steps);
    if the gateway is unreachable, emits a safe draft plan marked
    ``draft: true`` so the loop still pauses at approval.
    """
    sp = dict(state.get("scratchpad", {}))
    sp["planner_visited"] = True
    sp["triage_category"] = state.get("routing", "incident")

    synthesis = state.get("synthesis") or sp.get("synthesis", "")
    try:
        llm = _build_llm(temperature=0.0)
        response: AIMessage = llm.invoke(
            [SystemMessage(content=PLANNER_SYSTEM_PROMPT), HumanMessage(content=synthesis)]
        )
        raw = response.content if isinstance(response.content, str) else str(response.content)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
        plan = json.loads(raw)
        if not isinstance(plan, dict) or "steps" not in plan:
            raise ValueError("plan must be a dict with a 'steps' list")
        plan.setdefault("draft", False)
    except Exception as exc:
        plan = {
            "priority": "high",
            "rationale": (
                f"Draft plan — LLM gateway unreachable ({type(exc).__name__}: "
                f"{exc}); requires human review before execution."
            ),
            "steps": [
                {
                    "action": "escalate",
                    "target": "human-on-call",
                    "detail": "No specialist findings available — requires "
                              "manual investigation.",
                }
            ],
            "draft": True,
        }

    sp["plan"] = plan
    return {"plan": plan, "scratchpad": sp}


def approval_node(state: AgentState) -> dict[str, Any]:
    """Human-in-the-loop gate (T3.5, wired fully in T3.6).

    Reads ``scratchpad["approval_decision"]``:

    - ``"approved"``  → ``approval_status = "approved"`` (T3.7 executor
      will consume the plan).
    - ``"rejected"``  → ``approval_status = "rejected"``.
    - missing          → ``approval_status = "awaiting_approval"`` —
      the graph pauses here with the plan persisted in the state,
      ready for T3.6 to resume it from a UI / DB round-trip.

    The plan is always preserved in ``scratchpad["pending_plan"]`` so
    the approval UI and the future Executor Agent can read it.
    """
    sp = dict(state.get("scratchpad", {}))
    sp["approval_visited"] = True
    sp["triage_category"] = state.get("routing", "incident")

    plan = state.get("plan") or sp.get("plan", {})
    sp["pending_plan"] = plan

    decision = (sp.get("approval_decision") or "").strip().lower()
    if decision == "approved":
        status = "approved"
    elif decision == "rejected":
        status = "rejected"
    else:
        status = "awaiting_approval"

    sp["approval_status"] = status
    return {"approval_status": status, "scratchpad": sp}


# ─────────────────────────────────────────────────────────────
# Resume graph (T3.6) — continue past the human gate
# ─────────────────────────────────────────────────────────────
def _extract_executor_result(messages: list) -> dict[str, Any] | None:
    """Parse the create_remediation_plan ToolMessage into a result dict.

    The tool returns JSON like::

        {"status": "Created"|"Preview", "name": "rp-...",
         "namespace": "sentinel", "dry_run": false, ...}

    Returns the parsed dict, or ``None`` if the tool hasn't run yet /
    the output is not parseable.
    """
    for m in reversed(messages):
        if not isinstance(m, ToolMessage):
            continue
        if getattr(m, "name", "") != "create_remediation_plan":
            continue
        content = m.content if hasattr(m, "content") else str(m)
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict) and payload.get("name"):
            return payload
    return None


def _executor_tool_has_run(messages: list) -> bool:
    """True when create_remediation_plan already produced a ToolMessage.

    Used to stop the executor loop even when the tool returned an error
    (non-JSON) — without this the resume graph would loop forever.
    """
    for m in reversed(messages):
        if isinstance(m, ToolMessage) and getattr(m, "name", "") == "create_remediation_plan":
            return True
    return False


def executor_agent_node(state: AgentState) -> dict[str, Any]:
    """The Executor Agent (T3.7) — the ONLY agent that can act.

    Runs in the resume graph after ``approval`` when the human has
    approved the plan.  It is deliberately *deterministic* (no LLM):
    the approved plan is already structured JSON, so converting it to a
    RemediationPlan spec is mechanical — and mechanical conversion is
    exactly what we want from the one component that can write to the
    cluster.

    Behaviour:
    1. If ``approval_status != "approved"`` → nothing happens.
    2. First pass: records the dry-run proposal (the RemediationPlan
       manifest) in the scratchpad, then requests the tool
       ``create_remediation_plan`` (dry_run=False) via a structured
       tool call.
    3. After the tool runs: parses the result, stores it in
       ``state["remediation_plan"]`` + scratchpad, and stops (no more
       tool calls → the resume graph ends).
    """
    sp = dict(state.get("scratchpad", {}))
    sp["executor_visited"] = True
    sp["triage_category"] = state.get("routing", "incident")

    # Guard 1 — only act on approved plans.
    if state.get("approval_status") != "approved":
        sp["executor_status"] = "skipped"
        return {"scratchpad": sp, "executor_status": "skipped"}

    plan = state.get("plan") or sp.get("plan") or sp.get("pending_plan", {})
    incident = state.get("incident") or sp.get("incident", "")

    # Guard 2 — if the tool already ran (success OR error), do NOT emit
    # more tool calls (otherwise the resume graph would loop forever).
    result = _extract_executor_result(state.get("messages", []))
    if result is not None:
        sp["executor_status"] = "created" if result.get("status") == "Created" else "preview"
        sp["remediation_plan"] = result
        sp["remediation_plan_name"] = result.get("name", "")
        return {
            "scratchpad": sp,
            "executor_status": sp["executor_status"],
            "remediation_plan": result,
        }
    if _executor_tool_has_run(state.get("messages", [])):
        # The tool ran but its output was not parseable (e.g. the
        # operator bridge rejected the plan) — stop, do not retry.
        last_tool = None
        for m in reversed(state.get("messages", [])):
            if isinstance(m, ToolMessage) and getattr(m, "name", "") == "create_remediation_plan":
                last_tool = m
                break
        raw = last_tool.content if last_tool is not None else "unknown error"
        sp["executor_status"] = "blocked"
        sp["executor_error"] = str(raw)[:500]
        return {"scratchpad": sp, "executor_status": "blocked"}

    # Build the RemediationPlan manifest (dry-run proposal first).
    try:
        from sentinel_api.remediation import build_remediation_plan, to_yaml

        manifest = build_remediation_plan(
            plan,
            incident=incident,
            dry_run=False,
            plan_ref=str(sp.get("plan_ref", "")),
        )
        proposal_yaml = to_yaml(manifest)
        manifest_name = manifest["metadata"]["name"]
    except Exception:  # sentinel_api unavailable — inline minimal proposal
        import uuid as _uuid

        target = ""
        steps = plan.get("steps", [])
        if steps:
            target = str(steps[0].get("target", "plan")).replace("/", "-")[:40]
        manifest_name = f"rp-{target or 'plan'}-{_uuid.uuid4().hex[:8]}"
        proposal_yaml = (
            "apiVersion: sentinel.io/v1\n"
            "kind: RemediationPlan\n"
            f"metadata:\n  name: {manifest_name}\n  namespace: sentinel\n"
            "spec:\n"
            f"  incident: {str(incident)[:120]}\n"
            f"  priority: {plan.get('priority', 'medium')}\n"
            f"  rationale: {str(plan.get('rationale', ''))}\n"
            "  dryRun: false\n"
            "  steps:\n"
            + "\n".join(
                f"    - action: {s.get('action', '')}\n"
                f"      target: {s.get('target', '')}"
                for s in plan.get("steps", [])
                if isinstance(s, dict)
            )
        )

    # The dry-run proposal is recorded for the audit trail.
    sp["executor_proposal"] = proposal_yaml
    sp["executor_status"] = "pending"

    request = {
        "name": "create_remediation_plan",
        "args": {"plan": plan, "incident": incident, "dry_run": False},
        "id": f"call_executor_{abs(hash(manifest_name)) % 10**8}",
    }
    ai_msg = AIMessage(
        content=(
            "⚙️ Executor Agent: plan approved. Dry-run proposal recorded "
            f"for {manifest_name}; creating the RemediationPlan object."
        ),
        tool_calls=[request],
    )
    return {"messages": [ai_msg], "scratchpad": sp, "executor_status": "pending"}


def should_continue_executor(state: AgentState) -> Literal["executor_tools", "__end__"]:
    """Route executor tool calls through the executor_tools ToolNode."""
    messages = state.get("messages", [])
    if not messages:
        return "__end__"
    last = messages[-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "executor_tools"
    return "__end__"


def should_resume(state: AgentState) -> Literal["executor", "__end__"]:
    """After the approval gate: approved → executor; else end."""
    if state.get("approval_status") == "approved":
        return "executor"
    return "__end__"


def build_resume_graph() -> StateGraph:
    """Build the "resume" graph used after a human decision.

    T3.6: START → approval → END.

    T3.7: START → approval → (approved → executor → executor_tools →
    executor → END | rejected/awaiting → END).  The executor creates
    the RemediationPlan object only after human approval.
    """
    builder = StateGraph(AgentState)
    builder.add_node("approval", approval_node)
    builder.add_node("executor", executor_agent_node)
    builder.add_node("executor_tools", ToolNode(EXECUTOR_TOOLS))
    builder.set_entry_point("approval")
    builder.add_conditional_edges(
        "approval",
        should_resume,
        {"executor": "executor", "__end__": END},
    )
    builder.add_conditional_edges(
        "executor",
        should_continue_executor,
        {"executor_tools": "executor_tools", "__end__": END},
    )
    builder.add_edge("executor_tools", "executor")
    return builder.compile()


_resume_graph = build_resume_graph()


def resume_plan_graph(plan: dict[str, Any], decision: str) -> str:
    """Resume the graph with a human decision on a persisted plan.

    This is the mechanism that makes "clicking Approve in the UI
    unblocks the graph" work (T3.6): given the persisted plan and the
    decision (``"approved"`` / ``"rejected"``), it runs the approval
    node with the decision injected and returns the final
    ``approval_status``.

    T3.7: an ``"approved"`` decision also runs the Executor Agent,
    which creates the RemediationPlan object in the cluster.

    Args:
        plan: The persisted plan dict (as returned by the plans API).
        decision: ``"approved"`` or ``"rejected"``.

    Returns:
        The resulting ``approval_status`` — ``"approved"`` or
        ``"rejected"`` (never ``"awaiting_approval"`` here, because a
        decision is always provided).
    """
    return resume_plan_graph_detailed(plan, decision)["approval_status"]


def resume_plan_graph_detailed(plan: dict[str, Any], decision: str) -> dict[str, Any]:
    """Like :func:`resume_plan_graph` but returns the full outcome.

    Returns a dict with ``approval_status`` plus the executor result
    (``executor_status`` and ``remediation_plan``) so the plans API /
    UI can report what actually happened after approval.
    """
    state: AgentState = {
        "messages": [],
        "tool_calls": [],
        "scratchpad": {
            "approval_decision": decision,
            "plan": plan.get("plan", plan),
            "pending_plan": plan.get("plan", plan),
            "plan_ref": plan.get("id", ""),
            "incident": plan.get("incident", ""),
            "synthesis": plan.get("synthesis", ""),
        },
        "routing": "incident",
        "classification_json": "",
        "incident": plan.get("incident", ""),
        "synthesis": plan.get("synthesis", ""),
        "plan": plan.get("plan", plan),
        "approval_status": "",
        "remediation_plan": {},
        "executor_status": "",
    }
    result = _resume_graph.invoke(state)
    return {
        "approval_status": result.get("approval_status", "awaiting_approval"),
        "executor_status": result.get("executor_status", ""),
        "remediation_plan": result.get("remediation_plan", {}),
    }


# ─────────────────────────────────────────────────────────────
# Graph builder (updated T3.1, T3.2, T3.3, T3.4, T3.5)
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

    T3.4: A ``knowledge`` classification now routes to a dedicated
    :func:`rag_agent_node` with its own ``rag_tools`` ToolNode
    bound to the RAG tool subset.  Evidence with citations is
    published to ``scratchpad["evidence"]`` for other agents.

    T3.5: An ``incident`` classification now routes to ``dispatch``,
    which fans out to the SRE + Security + RAG specialists in
    PARALLEL; each finished branch joins at ``synthesis`` (merges
    findings) → ``planner`` (proposes a remediation plan) →
    ``approval`` (pauses awaiting human input).

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
    # T3.4
    builder.add_node("rag_agent", rag_agent_node)
    builder.add_node("rag_tools", ToolNode(RAG_TOOLS))
    # T3.5 incident loop
    builder.add_node("dispatch", dispatch_node)
    builder.add_node("synthesis", synthesis_node)
    builder.add_node("planner", planner_node)
    builder.add_node("approval", approval_node)

    # Edges — triage first
    builder.set_entry_point("triage_agent")
    builder.add_conditional_edges(
        "triage_agent",
        route_to_specialist,
        {
            "sre_agent": "sre_agent",
            "security_agent": "security_agent",
            "cost_agent": "cost_agent",
            "rag_agent": "rag_agent",
            "dispatch": "dispatch",
            "__end__": END,
        },
    )

    # Edges — SRE tool loop
    builder.add_conditional_edges(
        "sre_agent",
        should_continue,
        {"tools": "tools", "synthesis": "synthesis", "__end__": END},
    )
    builder.add_edge("tools", "sre_agent")

    # Edges — Security tool loop (T3.2)
    builder.add_conditional_edges(
        "security_agent",
        should_continue_security,
        {"sec_tools": "sec_tools", "synthesis": "synthesis", "__end__": END},
    )
    builder.add_edge("sec_tools", "security_agent")

    # Edges — Cost tool loop (T3.3)
    builder.add_conditional_edges(
        "cost_agent",
        should_continue_cost,
        {"cost_tools": "cost_tools", "__end__": END},
    )
    builder.add_edge("cost_tools", "cost_agent")

    # Edges — RAG tool loop (T3.4)
    builder.add_conditional_edges(
        "rag_agent",
        should_continue_rag,
        {"rag_tools": "rag_tools", "synthesis": "synthesis", "__end__": END},
    )
    builder.add_edge("rag_tools", "rag_agent")

    # Edges — Incident loop (T3.5): parallel fan-out + join
    builder.add_edge("dispatch", "sre_agent")
    builder.add_edge("dispatch", "security_agent")
    builder.add_edge("dispatch", "rag_agent")
    # synthesis fans IN from all three specialists (LangGraph waits for
    # every incoming edge to fire before running the node).
    builder.add_edge("synthesis", "planner")
    builder.add_edge("planner", "approval")
    builder.add_edge("approval", END)

    return builder.compile()


# ─────────────────────────────────────────────────────────────
# Module-level singleton (compiled once at import time)
# ─────────────────────────────────────────────────────────────
graph = build_graph()
