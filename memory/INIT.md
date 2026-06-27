# INIT — Sentinel Project Memory for LLM

> This file captures the current state, decisions, and context of the Sentinel
> project. When an LLM returns to this workspace, reading this file should
> bring it up to speed instantly.

---

## Project Identity

- **Name:** Sentinel
- **Tagline:** AI-Native DevSecOps & SRE Platform
- **Goal:** A self-hosted platform where a team of specialized AI agents
  monitors infrastructure, triages incidents, answers questions via RAG,
  hardens security, and self-heals — running on Kubernetes with full
  observability and GitOps delivery.
- **Target audience:** DevOps / Platform / SRE / AI Engineer roles (portfolio
  project for 2025–2026 hiring).
- **Timeline:** ~3–5 months part-time, 5 phases.

---

## Current Status

- [x] **PROJECT_IDEA.md** — the vision document.
- [x] **PROJECT_IDEA_EXPLAINED.md** — beginner-friendly companion explaining
  every term and technology.
- [x] **INIT.md** — this file, the LLM memory file.
- [ ] Phase 0: Foundations (cluster + GitOps + observability).
- [ ] Phase 1: RAG core.
- [ ] Phase 2: Single agent SRE.
- [ ] Phase 3: Multi-agent + operator.
- [ ] Phase 4: Security hardening.
- [ ] Phase 5: Polish, evals, portfolio.

> **We are at:** Pre-Phase 0. Nothing has been scaffolded yet. All we have is
> the idea and explanations. Ready to begin building when the user says so.

---

## Tech Stack Decisions (locked)

These have been chosen in the vision doc and should be adhered to unless the
user explicitly overrides:

| Layer | Choice | Notes |
|---|---|---|
| Local cluster | `kind` | K8s IN Docker; fast, multi-node, widely used. |
| Backend API | FastAPI | Python, WebSocket support, async. |
| Chat UI | Next.js + React | Server-side rendering, streaming. |
| Agent framework | LangGraph | State-machine/graph-based orchestration. |
| LLM gateway | LiteLLM | Unified API for local + cloud LLMs. |
| Local LLM | Ollama (BGE-M3, Llama, etc.) | Privacy; no logs leave the box. |
| Vector DB (primary) | Qdrant | Hybrid dense + sparse (BM25). |
| Vector DB (secondary) | pgvector | Postgres extension, for incidents. |
| RAG framework | LlamaIndex | Chunking, embedding, retrieval, reranking. |
| Reranker | BGE / Cohere cross-encoder | Re-ranks top candidates by relevance. |
| Operator | Go + Kubebuilder | CRD: RemediationPlan. |
| GitOps | ArgoCD | Syncing Git → cluster. |
| IaC | Terraform / OpenTofu | Cloud provisioning, cluster resources. |
| CI/CD | GitHub Actions | Lint, scan, build, sign, push. |
| Metrics | Prometheus + Alertmanager | Numeric time-series. |
| Logs | Loki | Log aggregation. |
| Traces | Tempo | Distributed tracing. |
| Dashboards | Grafana | All observability in one UI. |
| Instrumentation | OpenTelemetry | Vendor-neutral metrics/logs/traces. |
| SLOs | Pyrra | On top of Prometheus. |
| Networking | Cilium (eBPF) | Network policies + observability. |
| Runtime security | Tetragon (eBPF) + Falco | Syscall-level + rule-based. |
| Image scanning | Trivy | CI + cluster scanning. |
| Admission control | Kyverno | Policy-as-code, gates deployments. |
| Supply chain | Cosign / Sigstore | Image signing and verification. |
| Secrets | External Secrets + Vault | No secrets in git. |
| Data/state | Postgres + Redis | Postgres = source of truth; Redis = task queue. |
| Notifications | n8n (optional) | No-code automation glue. |

---

## Architecture — Key Design Decisions

1. **Multi-agent state machine, not a single mega-prompt.** Each agent has one
   specialty (triage, SRE, security, cost, code review, RAG, executor,
   postmortem). LangGraph orchestrates them as a graph with human-in-the-loop
   gates.

2. **Executor Agent is the ONLY one that can act.** It has an allow-list of
   actions (scale, restart, rollback, cordon, block IP). Always dry-runs
   first. Always requires human approval (or sandbox auto-approve).

3. **Operator as safe bridge.** The `RemediationPlan` CRD is the auditable
   contract between "AI wants to do X" and "X happens in the cluster".
   Reconciliation loop: Proposed → Approved → Applied → Verified → Closed.

4. **Production RAG, not naive RAG.**
   - Source-aware chunking (AST for code, prose for docs).
   - Hybrid retrieval (dense + sparse) + cross-encoder reranker.
   - Citations on every answer (file path + line range).
   - Eval set + CI gate on recall@k and faithfulness.
   - **Self-updating:** every postmortem is auto-embedded.

5. **The learning loop** (the core differentiator):
   ```
   alert → triage → [SRE+Security+RAG parallel] → synthesis →
     plan → approve → executor heals → postmortem → embed in KB →
     next time: instant recall
   ```

6. **Safety is paramount.** Allow-lists, dry-runs, RBAC with dedicated
   ServiceAccount, full audit log (every agent thought and action).

7. **GitOps everything.** Git is the source of truth, not `kubectl edit`.
   ArgoCD auto-syncs. Canary rollouts via Argo Rollouts with SLO-based
   auto-rollback.

---

## Portfolio Strategy (for context)

- Monorepo with clear folders: `/operator`, `/agents`, `/rag`, `/infra`,
  `/gitops`, `/frontend`.
- 5-minute demo video (top 3 features).
- 3 blog posts (operator, RAG, eBPF).
- Evals dashboard showing retrieval + agent success metrics.
- Optional live read-only demo on a small cloud node.

---

## Glossary (quick LLM reference)

When you encounter these terms in conversation, here is the Sentinel-specific
meaning:

| Term | Meaning in this project |
|---|---|
| `RemediationPlan` | CRD (custom resource) that represents one healing action. |
| The operator | Go + Kubebuilder controller that reconciles RemediationPlans. |
| Agent graph | LangGraph state machine with 8 specialized nodes. |
| KB / Knowledge Base | Qdrant + pgvector, populated by LlamaIndex pipelines. |
| RAG Agent | One of the 8 agents — handles retrieval + citation. |
| Executor Agent | The only agent that can take real K8s actions. |
| The loop | alert → diagnose → heal → postmortem → embed → faster next time. |
| Eval set | Golden Q&A pairs used to measure retrieval quality in CI. |

---

## Common Conventions

- **Language for each component:**
  - Operator: **Go**
  - Agents + RAG + API: **Python** (FastAPI, LangGraph, LlamaIndex)
  - Frontend: **TypeScript** (Next.js, React)
  - Infrastructure: **HCL** (Terraform/OpenTofu)
  - Cluster config: **YAML** (Helm charts, K8s manifests)

- **Monorepo structure** (planned):
  ```
  /operator        — Go + Kubebuilder operator code
  /agents          — Python agent definitions (LangGraph)
  /rag             — Python RAG pipeline (LlamaIndex, Qdrant)
  /api             — Python FastAPI backend
  /frontend        — Next.js chat UI + dashboards
  /infra           — Terraform/OpenTofu for cloud provisioning
  /gitops          — Helm charts, K8s manifests (ArgoCD source)
  /docs            — Architecture docs, runbooks, agent designs
  /scripts         — Utility scripts
  ```

- **Keep it buildable incrementally.** Each phase is a standalone artifact.
  Phase 0 needs no AI at all. Phase 1 adds retrieval. Phase 2 adds the first
  agent. Phase 3 adds multi-agent + operator. Phase 4 adds advanced security.
  Phase 5 polishes everything.

---

## How to use this file

- **For the LLM:** Read this first when entering the workspace. It replaces
  the need to re-read all source files and conversation history.
- **For the user:** Update this file when major decisions change, when a phase
  completes, or when the tech stack shifts. This keeps the LLM's context
  accurate.
- **Suggested updates:** After every phase completion, bump the "Current
  Status" section. When new tooling is added, update the tech stack table.
