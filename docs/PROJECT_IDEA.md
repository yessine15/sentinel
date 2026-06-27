# Sentinel — AI-Native DevSecOps & SRE Platform

> A self-hosted platform where a team of specialized AI agents monitors your
> infrastructure, triages incidents, answers questions about your own systems
> (via RAG), hardens security, and even heals problems for you — all running
> on Kubernetes with full observability and GitOps delivery.

---

## 1. Why this project

Hiring managers for **DevOps / Platform / Cloud-Native / AI Engineer** roles in
2025–2026 are bored of seeing "another TODO app" and "another CI/CD pipeline".
What actually stands out is a candidate who can **combine** disciplines into one
coherent system. This project forces you to touch, deeply, every layer that
modern teams care about:

| Discipline            | Where it shows up in Sentinel                       |
|-----------------------|----------------------------------------------------|
| DevOps / GitOps       | ArgoCD + Terraform + GitHub Actions delivery        |
| Kubernetes (advanced)  | Custom operator, CRDs, autoscaling, policies        |
| Security / DevSecOps  | Trivy, Falco, Tetragon, OPA/Kyverno, Cosign       |
| Multi-AI agents       | LangGraph orchestrator with specialized agents      |
| RAG                   | Vector DB over runbooks/incidents/code/docs         |
| Observability         | Prometheus, Loki, Tempo, Grafana, OpenTelemetry    |
| eBPF (hot in market)  | Cilium networking + Tetragon runtime security       |
| LLMOps                | Local + cloud models, evals, prompt management      |
| SRE / Platform eng.   | SLOs, error budgets, self-healing, toil reduction   |

It is also **genuinely useful in daily life**: if you run a homelab, side
projects, or any Kubernetes cluster, Sentinel becomes your personal SRE team
that watches things while you sleep.

---

## 2. The problem it solves (real, not imaginary)

Small teams and solo developers cannot afford a 24/7 SRE + Security team. When
something breaks at 3am:
- Logs are scattered across services — nobody knows what to look at first.
- Runbooks exist but nobody reads them under pressure.
- Security alerts pile up with no triage.
- Repeat incidents happen because knowledge never gets captured.

Sentinel does what a **tier-1 on-call engineer** does:
1. Detects the anomaly.
2. Pulls the relevant runbook/history via RAG.
3. Diagnoses root cause with reasoning across multiple agents.
4. Proposes (and, with approval, executes) a remediation.
5. Writes a postmortem and feeds the lesson back into the knowledge base.

This is a closed **learning loop** — every incident makes the system smarter.
That loop is the "wow" moment in an interview.

---

## 3. High-level architecture

```
                            ┌──────────────────────────────┐
   You / Slack / Web ───────┤   API Gateway + Chat UI      │
                            │  (FastAPI + WebSocket + Next) │
                            └──────────────┬───────────────┘
                                           │
                            ┌──────────────▼───────────────┐
                            │   Agent Orchestrator          │
                            │   (LangGraph state machine)  │
                            └─┬───────┬───────┬───────┬─────┘
                              │       │       │       │
                     ┌────────▼┐ ┌────▼───┐ ┌─▼─────┐ ┌▼────────┐
                     │  SRE    │ │Security│ │ Cost  │ │ Code    │
                     │ Agent   │ │ Agent  │ │ Agent │ │ Review  │
                     │         │ │        │ │       │ │ Agent   │
                     └────┬────┘ └───┬────┘ └──┬────┘ └────┬────┘
                          │          │         │           │
            ┌─────────────┼──────────┴─────────┴───────────┼────────────┐
            │             │                                    │           │
   ┌────────▼─────────┐  │   ┌───────────────────┐   ┌───────▼────────┐   │
   │ RAG Knowledge     │  │   │ Tool / Action Bus  │   │ LLM Gateway    │   │
   │ Base (Qdrant +    │◄─┘   │ (kubectl, helm,    │◄──┤ (Ollama local +│   │
   │ pgvector + rerank)│      │  terraform, gh)    │   │  OpenAI/Anthropic)│
   └────────┬──────────┘      └────────┬──────────┘   └────────────────┘   │
            │                          │                                     │
   ┌────────▼──────────┐    ┌──────────▼──────────────┐                   │
   │ Ingestion pipeline │    │ Custom K8s Operator      │◄──────────────────┘
   │ (runbooks,         │    │ (reconciles desired vs  │
   │  incidents, code,  │    │  actual state, executes │
   │  docs, tickets)    │    │  healing actions safely)│
   └────────────────────┘    └──────────┬──────────────┘
                                        │
   ┌────────────────────────────────────▼───────────────────────────────┐
   │                  Kubernetes Platform (the substrate)                │
   │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ │
   │  │Prometheus│ │   Loki   │ │  Tempo   │ │ Grafana  │ │  OTel   │ │
   │  │ metrics  │ │  logs    │ │  traces  │ │dashboards│ │collector│ │
   │  └────▲─────┘ └────▲─────┘ └────▲─────┘ └─────▲────┘ └────▲────┘ │
   │       └────────────┴────────────┴───────────┴───────────┴────┘    │
   │   Cilium (eBPF networking) · Tetragon (runtime sec) · Falco         │
   │   Trivy (image scan) · Kyverno (admission) · Cosign (signing)      │
   └────────────────────────────────────────────────────────────────────┘
                                        │
                          ┌─────────────▼─────────────┐
                          │  GitOps (ArgoCD) +         │
                          │  Terraform (infra) +        │
                          │  GitHub Actions (CI/CD)     │
                          └────────────────────────────┘
```

---

## 4. Technology stack & justification

### 4.1 Platform / Orchestration
| Tech | Role | Why it matters to employers |
|------|------|-----------------------------|
| **Kubernetes** | Everything runs here | Core skill; advanced usage (CRDs, operators) = senior signal |
| **Custom Operator (Go + Kubebuilder)** | Executes healing actions safely via CRDs | Writing an operator is a "wow" — shows deep K8s |
| **ArgoCD** | GitOps delivery | Industry standard for GitOps |
| **Terraform / OpenTofu** | Provision cloud + K8s | IaC is table stakes |

### 4.2 Networking & Security (DevSecOps)
| Tech | Role |
|------|------|
| **Cilium** | eBPF-based networking + network policies |
| **Tetragon** | eBPF runtime security (detects suspicious syscalls live) |
| **Falco** | Runtime rule-based alerting |
| **Trivy** | Image + IaC + misconfiguration scanning in CI |
| **Kyverno / OPA Gatekeeper** | Admission-control policies (block bad manifests) |
| **Cosign / Sigstore** | Sign images, verify supply chain (SLSA) |
| **External Secrets + Vault** | No secrets in git |

### 4.3 Observability ("the monitoring window")
| Tech | Role |
|------|------|
| **Prometheus + Alertmanager** | Metrics + alerting |
| **Loki** | Log aggregation |
| **Tempo** | Distributed tracing |
| **Grafana** | Dashboards + the real-time "monitoring window" UI |
| **OpenTelemetry** | Vendor-neutral instrumentation of your own services |
| **Pyrra** | SLOs & error budgets (very "SRE") |

### 4.4 AI / Agents / RAG
| Tech | Role |
|------|------|
| **LangGraph** | Multi-agent orchestration as a state machine (robust, not chaotic chains) |
| **Ollama** | Local models (privacy: your logs never leave the box) |
| **OpenAI / Anthropic / Gemini** | Cloud models for hard reasoning (LLM Gateway chooses) |
| **LiteLLM** | Unified LLM gateway (fallback, cost, rate limits) |
| **Qdrant** + **pgvector** | Vector stores (hybrid: one for code/docs, one for incidents) |
| **LlamaIndex** | Ingestion + reranking pipelines |
| **BGE / Cohere reranker** | Retrieval quality (shows you care about RAG done right) |

### 4.5 App / Interface
| Tech | Role |
|------|------|
| **FastAPI** | Backend API + WebSocket streaming |
| **Next.js** | Chat UI + dashboards |
| **Postgres** | Source of truth for incidents, postmortems, agent memory |
| **Redis** | Task queue (Celery/RQ) for async agent work |
| **n8n** | Optional no-code glue for notifications/automation |

---

## 5. The multi-agent system (deep dive)

This is the heart. **Do not** build a single mega-prompt. Build a **state
machine** where specialized agents collaborate, with a human-in-the-loop gate.

### Agents
1. **Triage Agent** — receives a raw alert, enriches it (labels, severity,
   affected service), routes to the right specialist.
2. **SRE Agent** — queries metrics/logs/traces, correlates, hypothesizes root
   cause. Tools: `kubectl`, `promql`, `logql`, SQL over metrics.
3. **Security Agent** — classifies if alert is security-relevant, cross-checks
   Tetragon/Falco events, checks CVEs. Tools: Trivy API, CVE DB.
4. **RAG Agent** — retrieves runbooks, past incidents, code, and docs; returns
   ranked evidence with citations.
5. **Cost Agent** — flags runaway spend, idle resources, right-sizing.
6. **Code Review Agent** — on PRs, reviews infra code (Helm, Terraform, Docker)
   for best practices and security.
7. **Executor Agent** — the ONLY agent that can perform actions, and only after
   a human approves a plan (or auto-approves low-risk in a "sandbox" mode).
8. **Postmortem Agent** — writes the incident writeup, extracts lessons, and
   **writes them back into the vector DB** so the loop closes.

### The loop (why it's smart)
```
alert → triage → [SRE + Security + RAG in parallel] → synthesis →
   plan → human approval → executor heals → postmortem → embed in KB →
   next time the same alert fires, recall is instant.
```

### Safety
- **Allow-list of actions** the executor can run (scale deployment, restart pod,
  rollback, cordon node, block IP). Nothing else.
- **Dry-run first**: executor proposes the exact `kubectl`/`terraform` command.
- **Audit log**: every agent thought + action stored in Postgres, viewable in UI.
- **RBAC**: a dedicated Kubernetes ServiceAccount with least privilege.

---

## 6. The RAG knowledge base (deep dive)

Naive RAG = upload a PDF and grep-ish search. **Production RAG** (what employers
want) is structured, evaluated, and self-improving.

### Sources ingested
- Runbooks (Markdown in git)
- Past incidents & postmortems (Postgres)
- Codebase (chunked by function/file, with AST awareness)
- Helm charts, Terraform modules, Dockerfiles
- Kubernetes resource manifests (live cluster state snapshots)
- Chat history (agent memory)
- Security policies (Kyverno rules, CIS benchmarks)
- Vendor docs (cached) for the exact tools you use

### Pipeline
1. **Ingest** → chunking strategies per source type (code vs prose).
2. **Embed** → BGE-M3 (local) or OpenAI embeddings.
3. **Store** → Qdrant (hybrid: dense + sparse BM25).
4. **Retrieve** → hybrid search + **cross-encoder reranker**.
5. **Cite** → every answer links to source file + line. No hallucinated fixes.
6. **Evaluate** → a small eval set (golden Q&A) with recall@k and faithfulness
   metrics; CI fails if retrieval quality drops.

### The killer feature
The KB is **self-updating**: every postmortem an agent writes is auto-embedded.
So Sentinel literally learns from its own resolved incidents. Show this in the
demo: trigger the same incident twice — second time it resolves 10× faster.

---

## 7. The custom Kubernetes Operator

Write this in **Go with Kubebuilder**. Define a CRD, e.g. `RemediationPlan`:

```yaml
apiVersion: sentinel.io/v1
kind: RemediationPlan
metadata:
  name: heal-api-oom-2026-06-27
spec:
  diagnosis: "pod OOMKilled, memory limit too low for new traffic"
  proposedAction:
    kubectl: "kubectl set resources deploy/api -c=api --limits=memory=1Gi"
  riskLevel: low
  autoApprove: true
status:
  state: Applied        # Proposed → Approved → Applied → Verified → Closed
  appliedBy: executor-agent
  verified: true
```

The **operator reconciles** these objects: when approved, it runs the action,
verifies the fix (metrics recovered?), and updates status. This is the safe,
auditable bridge between "AI wants to do X" and "X happens in the cluster".

This single component proves you understand K8s controllers, reconciliation
loops, RBAC, and CRD design — major seniority signal.

---

## 8. CI/CD & GitOps flow

```
git push → GitHub Actions:
   ├─ lint + tests (code)
   ├─ Trivy scan images + IaC
   ├─ Cosign sign image
   ├─ build + push to registry
   └─ update Helm values in gitops repo
                │
                ▼
        ArgoCD detects drift → syncs to cluster
                │
                ▼
        Kyverno admission policies gate the deploy
                │
                ▼
        Canary rollout (Argo Rollouts) + SLO check
                │
   if SLO violated → auto-rollback
```

Everything is **declarative** and **reproducible** — `git` is the source of truth,
not `kubectl edit`.

---

## 9. Concrete, demoable features (the "wow" list)

Pick 3–4 to demo in a 5-minute video — these are what get you hired:

1. **"Ask Sentinel"** chat: *"Why did the payments service latency spike last
   night?"* → it correlates Prometheus + Loki + traces, cites the relevant
   runbook, and shows the offending query. *(RAG + multi-source + agents)*
2. **Self-healing**: trigger an OOM or a crashed pod; Sentinel detects, writes a
   `RemediationPlan`, (with approval) applies it, verifies, writes postmortem.
   *(Operator + agents + observability)*
3. **Security autopilot**: Tetragon detects a reverse shell in a pod; Security
   Agent confirms, Cordons node, opens a ticket with full evidence chain.
   *(eBPF + security agent + execution)*
4. **Cost cop**: Cost Agent flags an over-provisioned node, suggests right-size,
   generates the exact PR to change Terraform. *(Cost agent + IaC)*
5. **Code review on PRs**: PR opened → Code Review Agent reviews the Helm
   chart, comments inline, blocks merge if it violates policy. *(CI + agents)*
6. **The learning loop demo**: fire the same incident twice, show the second
   resolution is near-instant because the KB already has the answer.

---

## 10. Roadmap (build incrementally, ~3–5 months part-time)

### Phase 0 — Foundations (week 1–2)
- Provision cluster (kind/k3s locally, then a real cloud K8s).
- ArgoCD + Terraform skeleton + GitHub Actions CI.
- Observability stack: Prometheus, Loki, Grafana, OTel.
- **Deliverable:** a git push deploys an app with full metrics/logs.

### Phase 1 — RAG core (week 3–5)
- Qdrant + pgvector, ingestion pipelines for docs + code.
- FastAPI `/ask` endpoint, simple retrieval + rerank.
- Eval set + CI check on retrieval quality.
- **Deliverable:** "ask your codebase" works with citations.

### Phase 2 — Single agent SRE (week 6–8)
- LiteLLM gateway + Ollama local model.
- One agent that can run safe `kubectl` tools, answer on live state.
- Chat UI (Next.js + WebSocket streaming).
- **Deliverable:** chat that can inspect the live cluster.

### Phase 3 — Multi-agent + operator (week 9–12)
- LangGraph orchestrator, add Security + Cost + Code-review agents.
- Build the Kubebuilder operator + CRDs.
- Human-in-the-loop approval flow.
- **Deliverable:** self-healing demo works end to end.

### Phase 4 — Security hardening (week 13–15)
- Cilium + Tetragon + Falco + Kyverno + Trivy + Cosign.
- Wire Security Agent to runtime events.
- **Deliverable:** security autopilot demo.

### Phase 5 — Polish, evals, portfolio (week 16–18)
- Postmortem agent + auto KB update (close the loop).
- SLOs with Pyrra, dashboards, on-call simulation.
- README, architecture diagrams, 5-min demo video, blog posts.
- **Deliverable:** portfolio-ready.

---

## 11. What hiring managers will actually see (portfolio strategy)

The project alone isn't enough — **presentation** is half the signal. Plan:

- **Repo structure**: mono-repo with clear folders (`/operator`, `/agents`,
  `/rag`, `/infra`, `/gitops`, `/frontend`). Clean README with architecture.
- **Architecture diagram** in the README (the ASCII above, plus a nicer one via
  Excalidraw/Mermaid).
- **5-minute demo video** walking through the 3 best features. This converts.
- **3 blog posts**: "Building a custom K8s operator for AI-driven remediation",
  "Production RAG with self-updating knowledge base", "eBPF runtime security
  feeding an LLM agent". Post on dev.to / LinkedIn.
- **Live demo** (optional): deploy a read-only instance on a small cloud node
  with a public Grafana + a sandboxed "playground" chat.
- **Evals dashboard**: show retrieval quality + agent success-rate metrics. This
  screams "I build serious AI, not prompt hacks."

### Skills matrix this project proves
- Kubernetes (advanced) · Operator pattern · GitOps · IaC
- DevSecOps end-to-end · Supply chain security · eBPF
- Observability · SRE practices · SLOs
- LLM app architecture · Multi-agent systems · Production RAG · LLMOps/evals
- Go · Python · TypeScript · system design

That list = the exact JD bullet points for Senior Platform / AI Infra /
DevSecOps / SRE roles in 2026.

---

## 12. Stretch goals (to go from "great" to "unforgettable")

- **Multi-cluster**: manage several clusters from one Sentinel control plane.
- **MCP (Model Context Protocol)** server exposing Sentinel as tools to any
  agent/IDE — very current in 2025–2026.
- **Fine-tuned small model** (LoRA) on your incident history for cheap local
- **Policy-as-code generation**: Security Agent writes Kyverno/Rego rules for
  gaps it discovers.
- **Chaos engineering hook**: Sentinel + Litmus/Chaos Mesh runs game-days and
  learns from the failures.
- **Voice mode** for on-call: talk to Sentinel over phone at 3am.

---

## 13. Getting-started checklist

- [ ] Pick local cluster: `kind` or `k3d` for dev, a small cloud node for "prod".
- [ ] Create mono-repo, set up GitHub Actions skeleton.
- [ ] Stand up observability stack (Phase 0) — get *one* app fully instrumented.
- [ ] Stand up Qdrant + LlamaIndex ingestion of your own repos/docs.
- [ ] Get Ollama + LiteLLM running locally.
- [ ] Build the first `/ask` endpoint with citations.
- [ ] Read Kubebuilder docs; scaffold the operator + first CRD.
- [ ] Draft the agent architecture in `docs/agents.md` before coding LangGraph.

> Start narrow: a single agent that can answer "what's wrong with my cluster
> right now" using RAG over your runbooks. Expand outward. Do not try to build
> all 8 agents first — the orchestration will collapse under its own weight.

---

## 14. Adapt to your level

- **Junior / early-career**: stop at Phase 2–3 (RAG + one agent + GitOps +
  observability). That alone beats 95% of portfolios.
- **Mid-level**: add the operator + multi-agent + security stack (Phases 3–4).
- **Senior target**: complete the loop, add evals, multi-cluster, MCP, and the
  fine-tuned model (stretches).

Each phase is a valuable, deployable artifact on its own — you can start
interviewing after Phase 2 and keep building in parallel.
