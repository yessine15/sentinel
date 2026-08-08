"""Sentinel Agents package — LangGraph multi-agent orchestrator.

Phase 2 (T2.1+): LangGraph StateGraph with tool-calling SRE agent.
Phase 3 (T3.1+): Triage Agent → Specialist routing (SRE, Knowledge).
Phase 3 (T3.2+): Security Agent specialist with security tooling.
Phase 3 (T3.3+): Cost Agent specialist with right-sizing suggestions.
Phase 3 (T3.4+): RAG Agent specialist with ranked evidence + citations.
Phase 3 (T3.5+): Incident loop — parallel specialists → synthesis → plan → approval.
Phase 3 (T3.6+): Human-in-the-loop approval — plans persisted in Postgres,
                 approve/reject API unblocks the graph.
"""

__version__ = "0.8.0"
