# TASKS.md — Sentinel Build Plan

> Decomposition of the Sentinel project into ordered, checkable mini-tasks.
> Work top-to-bottom. Each phase builds on the previous one. Do **not** skip
> ahead — later tasks depend on earlier outputs.
>
> Legend:
> - `[ ]` not started
> - `[~]` in progress
> - `[x]` done
>
> Each task lists: **Goal → Steps → Done when**.

---

## How to use this file

- Keep one task `[~]` in progress at a time.
- When the "Done when" criteria are met, mark `[x]` and move on.
- Update `INIT.md` "Current Status" when a whole phase completes.
- If a task turns out to be wrong, edit it — but keep the file the single
  source of truth for what's left to build.

---

# Phase 0 — Foundations (week 1–2)

**Phase goal:** a `git push` deploys an app to a local cluster with full
metrics, logs, and traces visible in Grafana. No AI yet — pure DevOps.

## M0.1 Repo & tooling bootstrap
- [x] **T0.1 Initialize git monorepo**
  - Goal: one repo, clear top-level folders.
  - Steps: `git init`; create `/operator /agents /rag /api /frontend /infra
    /gitops /docs /scripts`; add `.gitignore` (Python, Go, Node, Terraform,
    kind); add `README.md` stub with architecture.
  - Done when: `git log` has initial commit; folders exist; VS Code opens it
    cleanly.

- [ ] **T0.2 Install local prerequisites**
  - Goal: every CLI we need is available.
  - Steps: install Docker, `kind`, `kubectl`, `helm`, `terraform` (or
    `tofu`), `go`, `python3.12`, `node20`, `ollama`, `trivy`, `cosign`;
    verify each with `--version`.
  - Done when: a single `scripts/install-check.sh` prints OK for every tool.

- [ ] **T0.3 Python & Node env hygiene**
  - Goal: reproducible dev environments.
  - Steps: add `pyproject.toml` (use `uv` or `poetry`); add `.python-version`;
    add `frontend/package.json` + `.nvmrc`; add pre-commit hooks (ruff,
    prettier, gofmt).
  - Done when: `uv sync` and `npm install` run clean from a fresh clone.

## M0.2 Local cluster
- [ ] **T0.4 Create kind cluster config**
  - Goal: a multi-node local cluster with extra port mappings for Ingress.
  - Steps: write `infra/kind-cluster.yaml` (1 control-plane + 2 workers,
    `extraPortMappings` for 80/443/30080-30083); script `scripts/kind-up.sh`.
  - Done when: `./scripts/kind-up.sh` and `kubectl get nodes` shows 3 Ready
    nodes.

- [ ] **T0.5 Install Ingress + cert basics**
  - Goal: reachable services from the host browser.
  - Steps: `helm install ingress-nginx ingress-nginx --set ...`; add entries
    to `/etc/hosts` for `sentinel.local`, `grafana.local`.
  - Done when: `curl http://sentinel.local` hits the default backend.

## M0.3 GitOps skeleton
- [ ] **T0.6 ArgoCD install + bootstrap app**
  - Goal: ArgoCD watches the `/gitops` folder and syncs it.
  - Steps: `helm install argocd argo/argo-cd`; port-forward or Ingress to UI;
    create an `Application` in `gitops/argocd/apps/` that points at
    `gitops/` itself (App-of-Apps).
  - Done when: deleting a manifest in `/gitops` makes ArgoCD re-sync it
    automatically.

- [ ] **T0.7 Folder layout under /gitops**
  - Goal: clean, convention-based GitOps tree.
  - Steps: create `gitops/{base,components,projects}/<app>/{Chart,values}.yaml`;
    define Kustomize bases for namespace, configs, common labels.
  - Done when: every app lives under `gitops/projects/<app>/` with its own
    Helm values.

## M0.4 CI (GitHub Actions)
- [ ] **T0.8 CI workflow skeleton**
  - Goal: every push triggers lint + test.
  - Steps: `.github/workflows/ci.yml` with jobs for Python (ruff + pytest),
    Go (gofmt + vet + test), Frontend (lint + build), and a security job
    (Trivy filesystem scan + tfsec for Terraform).
  - Done when: a bad PR fails CI; a clean PR passes.

- [ ] **T0.9 Container build pipeline**
  - Goal: build & push image on tag.
  - Steps: add `Dockerfile` for the demo app; workflow job builds and pushes
    to GHCR under `ghcr.io/<user>/sentinel-demo:<tag>`.
  - Done when: a git tag `vX` produces a published image.

## M0.5 Observability stack
- [ ] **T0.10 Deploy kube-prometheus-stack**
  - Goal: Prometheus + Alertmanager + Grafana in cluster.
  - Steps: Helm chart under `gitops/components/observability/`; configure
    persistent storage; default datasources; expose Grafana via Ingress.
  - Done when: `http://grafana.local` shows dashboards + Prometheus as a
    datasource.

- [ ] **T0.11 Deploy Loki + Promtail**
  - Goal: logs aggregated from all pods.
  - Steps: add Loki Helm release + Promtail DaemonSet; configure Grafana
    Loki datasource.
  - Done when: Grafana "Explore > Loki" shows logs from `kube-system` pods.

- [ ] **T0.12 Deploy Tempo + OTel collector**
  - Goal: distributed traces flowing.
  - Steps: add Tempo Helm release; add OTel collector Deployment with
    `otlp` receiver exporting to Tempo; add Grafana Tempo datasource.
  - Done when: a trace ID from the demo app appears in Grafana Explore.

- [ ] **T0.13 Build & deploy a demo instrumented app**
  - Goal: a sample service emitting metrics, logs, traces end-to-end.
  - Steps: write a tiny FastAPI app in `/api` (export `/metrics`, structured
    JSON logs, OTel spans); a Helm chart under `gitops/projects/demo-api/`;
    wire to Ingress at `sentinel.local`.
  - Done when: hit `http://sentinel.local/ping`; see request rate in
    Prometheus, log line in Loki, span in Tempo.

## M0.6 Phase 0 wrap-up
- [ ] **T0.14 Document the run**
  - Goal: anyone can reproduce Phase 0.
  - Steps: `docs/phase0.md` with prerequisites, `kind-up.sh`, ArgoCD UI
    password retrieval, Grafana datasources, screenshots.
  - Done when: teammate can get to the same Grafana dashboards in <30 min.

✅ **Phase 0 complete when:** a `git push` updates `gitops/`, ArgoCD deploys
the demo app, and you can see metrics + logs + traces for `/ping` in one
Grafana dashboard.

---

# Phase 1 — RAG Core (week 3–5)

**Phase goal:** an `/ask` endpoint that answers questions about your own
codebase/docs with citations. No agents yet — just retrieval.

## M1.1 Vector store
- [ ] **T1.1 Deploy Qdrant**
  - Goal: vector DB available in-cluster.
  - Steps: Helm chart for Qdrant under `gitops/components/qdrant/`;
    persistent PVC; Ingress at `qdrant.local`.
  - Done when: `curl http://qdrant.local/collections` returns `[]`.

- [ ] **T1.2 Deploy Postgres + pgvector**
  - Goal: relational store with vector extension for incidents.
  - Steps: Postgres Helm chart; enable `pgvector` extension via init SQL;
    Ingress for psql if needed.
  - Done when: `\dx` shows `vector`.

## M1.2 Ingestion pipeline
- [ ] **T1.3 Define source connectors**
  - Goal: pluggable loaders for each source type.
  - Steps: in `/rag/sentinel_rag/sources/`, create `runbook.py`, `code.py`,
    `markdown.py`, `postgres_incident.py` — each returns `Document` objects
    with metadata (path, line range, source_type, doc_id).
  - Done when: each loader can be run standalone and prints chunks.

- [ ] **T1.4 Chunkers (AST-aware for code)**
  - Goal: smart chunking per source type.
  - Steps: prose chunker (sentence + sliding window); code chunker using
    `tree-sitter` to split at function/class boundaries; record exact line
    ranges in metadata.
  - Done when: a code chunk never cuts mid-function; line ranges stored.

- [ ] **T1.5 Embedding service**
  - Goal: produce embeddings locally (BGE-M3 via Ollama) with cloud fallback.
  - Steps: `/rag/sentinel_rag/embed.py`; support Ollama `bge-m3` and OpenAI
    embeddings; configurable via env.
  - Done when: `embed("hello")` returns a 1024-dim vector.

- [ ] **T1.6 Ingest CLI**
  - Goal: one command to ingest a source into Qdrant.
  - Steps: `rag/sentinel_rag/ingest.py` with subcommands per source; writes
    to Qdrant collection `sentinel_kb` with sparse + dense vectors.
  - Done when: `python -m sentinel_rag.ingest code ./api` fills the
    collection; Qdrant shows point count > 0.

## M1.3 Retrieval
- [ ] **T1.7 Hybrid retriever**
  - Goal: combine dense + sparse (BM25) results.
  - Steps: `/rag/sentinel_rag/retrieve.py`; query Qdrant using hybrid
    prefetch; return top 50 candidates with scores.
  - Done when: a query returns sensible ranked candidates, not noise.

- [ ] **T1.8 Cross-encoder reranker**
  - Goal: re-rank the top 50 down to the best 5.
  - Steps: integrate BGE reranker (local via `sentence-transformers` or
    Cohere API); produce final ranked list.
  - Done when: reranker visibly re-orders results vs raw hybrid scores on
    a hand-picked test query.

- [ ] **T1.9 Citation renderer**
  - Goal: every returned chunk is attributable.
  - Steps: helper producing `[file:lines]` markers from stored metadata;
    function to render answer + sources block.
  - Done when: output JSON includes `{answer, sources:[{path, lines,
    snippet}]}`.

## M1.4 Eval set + CI gate
- [ ] **T1.10 Golden Q&A set**
  - Goal: a small, curated evaluation dataset.
  - Steps: `rag/eval/golden.jsonl` with ~15 hand-written Q&A pairs over your
    own codebase/docs; each has expected source references.
  - Done when: file exists and is committed.

- [ ] **T1.11 Eval script + metrics**
  - Goal: measure recall@k and faithfulness.
  - Steps: `rag/eval/run.py` runs retrieval over golden set; computes
    recall@5 and (later, once answers exist) faithfulness via a judge prompt
    through LiteLLM.
  - Done when: `python -m sentinel_rag.eval.run` prints a metrics table and
    exits non-zero if recall@5 < threshold (e.g. 0.7).

## M1.5 API surface
- [ ] **T1.12 LiteLLM gateway**
  - Goal: single gateway for all model calls.
  - Steps: deploy LiteLLM (Helm or container) with a config file listing
    Ollama + (optional) OpenAI; expose at `llm.local`.
  - Done when: `curl http://llm.local/v1/chat/completions` returns a chat
    completion from Ollama.

- [ ] **T1.13 FastAPI `/ask` endpoint**
  - Goal: HTTP entry point that retrieves + answers + cites.
  - Steps: `/api/sentinel_api/routes/ask.py` → retrieves → builds a prompt
    with the chunks → calls LiteLLM → returns `{answer, sources}`.
  - Done when: `curl -d '{"query":"where is the /ping handler?"}' ...` returns
    a grounded answer with a source citation.

- [ ] **T1.14 CI runs the eval gate**
  - Goal: retrieval regressions fail CI.
  - Steps: add a workflow job that ingests the test corpus and runs
    `eval.run`, failing if recall drops.
  - Done when: a deliberately-broken chunker fails CI.

✅ **Phase 1 complete when:** `POST /ask` answers questions about your own
code with citations, and CI guards retrieval quality.

---

# Phase 2 — Single Agent SRE (week 6–8)

**Phase goal:** a chat agent that can answer "what's wrong with my cluster
right now" by running safe `kubectl` tools on live state, streaming its
answer to a Next.js UI.

## M2.1 Agents package skeleton
- [ ] **T2.1 LangGraph scaffolding**
  - Goal: a runnable graph with a single SRE agent node.
  - Steps: `/agents/sentinel_agents/graph.py` defines a `StateGraph` with one
    node `sre_agent`; a typed `State` (messages, tool calls, scratchpad);
    compile and run end-to-end with a stub tool.
  - Done when: `python -m sentinel_agents.run` produces a chat turn.

- [ ] **T2.2 Tool registry + allow-list**
  - Goal: tools the agent may call, with strict allow-list enforcement.
  - Steps: `/agents/sentinel_agents/tools/` with `kubectl_get.py`,
    `kubectl_describe.py`, `promql_query.py`, `logql_query.py`; each tool
    validates args against an allow-list (`kubectl get` only, no
    `kubectl delete`).
  - Done when: agent cannot trigger a non-allow-listed command.

- [ ] **T2.3 Wire tools to live cluster**
  - Goal: tools talk to the real cluster & Prometheus/Loki.
  - Steps: tools use in-cluster kubeconfig + service account (read-only
    ClusterRole); PromQL via Prometheus HTTP API; LogQL via Loki HTTP API.
  - Done when: agent answers "list pods in default ns" using a live query.

## M2.2 Agent ↔ RAG integration
- [ ] **T2.4 Let the SRE agent retrieve from the KB**
  - Goal: agent uses RAG as a tool when relevant.
  - Steps: add a `rag_search(query)` tool that calls the retrieval pipeline;
    inject results + citations into the agent's state.
  - Done when: agent cites a runbook when answering a known-problem question.

## M2.3 Streaming chat API
- [ ] **T2.5 WebSocket streaming endpoint**
  - Goal: server pushes agent tokens/events live.
  - Steps: `/api/sentinel_api/routes/chat.py` WebSocket; agent emits
    `{type:"token", text}` / `{type:"tool", name, args}` / `{type:"sources"}`
    events; client renders progressively.
  - Done when: a browser tool (`wscat`) sees streaming token events.

## M2.4 Frontend chat UI
- [ ] **T2.6 Scaffold Next.js app**
  - Goal: a clean chat UI shell.
  - Steps: `npx create-next-app frontend`; tailwind; a chat layout with
    message list + input; a WebSocket client hook.
  - Done when: typing a message and pressing Enter forwards it to the WS.

- [ ] **T2.7 Render streaming answers + citations**
  - Goal: nice UX resembling ChatGPT with source chips.
  - Steps: render token stream; render source chips as clickable `[file:lines]`
    with hover popover showing snippet; render tool-call events inline.
  - Done when: an answer streams in and shows clickable citations.

- [ ] **T2.8 Deploy frontend via GitOps**
  - Goal: UI runs in-cluster.
  - Steps: Dockerfile for Next.js; Helm chart under
    `gitops/projects/frontend/`; Ingress at `sentinel.local`.
  - Done when: opening `http://sentinel.local` shows the chat UI.

✅ **Phase 2 complete when:** from the web UI you can chat with an agent that
inspects the live cluster and answers using both live state and the RAG KB,
with citations.

---

# Phase 3 — Multi-Agent + Operator (week 9–12)

**Phase goal:** the full loop — `alert → triage → parallel specialists →
plan → human approval → executor heals → postmortem → embed`. The operator is
the safe execution bridge.

## M3.1 More agents
- [ ] **T3.1 Triage Agent**
  - Goal: turns a raw alert into a structured incident.
  - Steps: `/agents/sentinel_agents/agents/triage.py`; enriches labels,
    severity, affected service; routes to the next node in the graph.
  - Done when: a fake Alertmanager webhook payload is normalized to a typed
    incident.

- [ ] **T3.2 Security Agent**
  - Goal: classifies security relevance + cross-checks runtime events/CVEs.
  - Steps: new tools `trivy_scan.py`, `cve_lookup.py`,
    `falco_events.py`, `tetragon_events.py`; agent decides if the incident
    is security-related.
  - Done when: a "suspicious exec in a pod" payload is flagged security.

- [ ] **T3.3 Cost Agent**
  - Goal: flags wasted spend + suggests right-sizing.
  - Steps: tool `kube_resource_usage.py` + (optional) cloud cost API; agent
    emits a right-sizing suggestion in Terraform form.
  - Done when: agent identifies an idle/over-provisioned resource and
    proposes a concrete change.

- [ ] **T3.4 RAG Agent (as a proper agent)**
  - Goal: dedicated retrieval agent returning ranked evidence + citations.
  - Steps: wraps the Phase 1 retrieval pipeline into an agent; returns only
    cited evidence, never free text.
  - Done when: other agents receive evidence with citations via the graph.

## M3.2 Orchestrator
- [ ] **T3.5 Build the LangGraph loop**
  - Goal: alert → triage → [SRE + Security + RAG in parallel] → synthesis →
    plan → approval → executor → postmortem → embed.
  - Steps: define nodes and edges; use parallel fan-out for the three
    specialists; a `synthesis` node merges their outputs; a `planner` node
    proposes a remediation plan; an `approval` node blocks for human input.
  - Done when: feeding a test alert drives state through the full graph,
    pausing at approval.

- [ ] **T3.6 Human-in-the-loop approval**
  - Goal: a gate where a user approves/rejects a plan.
  - Steps: store pending plan in Postgres; expose `/plans/{id}/approve` and
    `/reject`; UI shows a plan card with Approve/Reject buttons.
  - Done when: clicking Approve in the UI unblocks the graph.

- [ ] **T3.7 Executor Agent**
  - Goal: the *only* agent that can act; create RemediationPlan objects.
  - Steps: agent emits a `RemediationPlan` spec via the operator API; never
    runs `kubectl` directly; proposes a dry-run first.
  - Done when: approving a plan creates a `RemediationPlan` object in the
    cluster.

## M3.3 The Kubernetes operator
- [ ] **T3.8 Scaffold Kubebuilder operator**
  - Goal: a compilable Go operator skeleton.
  - Steps: `kubebuilder init --domain sentinel.io`; `kubebuilder create api
    --group sentinel --version v1 --kind RemediationPlan`; generate types
    in `/operator/api/v1/`.
  - Done when: `make manifests` produces the CRD YAML; `make install` installs
    it into the kind cluster.

- [ ] **T3.9 Implement reconcile loop**
  - Goal: operator acts on approved plans and verifies them.
  - Steps: reconcile switches on `status.state`:
    `Proposed` → wait;
    `Approved` → run action (via allow-listed executor) → `Applied`;
    `Applied` → watch metrics → `Verified`;
    `Verified` → after cooldown → `Closed`.
  - Done when: applying an approved RemediationPlan scales a Deployment and
    its status flips to `Verified` once metrics recover.

- [ ] **T3.10 RBAC + ServiceAccount (least privilege)**
  - Goal: operator pod can only touch what it needs.
  - Steps: a `ClusterRole` granting read on pods/deployments + write on
    RemediationPlan status + patch on deployments/nodes (allow-listed);
    bind to operator's ServiceAccount via `ClusterRoleBinding`.
  - Done when: dropping a permission breaks a specific action cleanly.

- [ ] **T3.11 Deploy operator via GitOps**
  - Goal:_OPERATOR runs in cluster, managed by ArgoCD.
  - Steps: build operator image; Helm chart under
    `gitops/projects/operator/`; ArgoCD syncs it.
  - Done when: ArgoCD shows the operator Deployment healthy.

## M3.4 Loop closure
- [ ] **T3.12 Postmortem Agent**
  - Goal: writes the writeup and feeds it back into the KB.
  - Steps: agent takes the incident state + actions + verification, drafts
    a postmortem markdown; stores in Postgres; spawns an ingestion job that
    embeds it into Qdrant.
  - Done when: after resolving an incident, a `/ask` query about that incident
    returns the freshly-written postmortem.

✅ **Phase 3 complete when:** end-to-end self-healing demo works — trigger an
OOM, Sentinel writes a RemediationPlan, you approve it, the operator applies
and verifies the fix, and a postmortem lands in the KB.

---

# Phase 4 — Security Hardening (week 13–15)

**Phase goal:** runtime security feeds the Security Agent; admission policies
gate deploys; images are signed and scanned. Security autopilot demo works.

## M4.1 Runtime security
- [ ] **T4.1 Install Cilium (replace default CNI)**
  - Goal: eBPF-based networking + network policies.
  - Steps: tear down existing CNI; install Cilium via Helm under
    `gitops/components/cilium/`; verify connectivity with `cilium status`.
  - Done when: pods still communicate; `cilium status` OK; Hubble UI shows
    a traffic flow map.

- [ ] **T4.2 Install Tetragon**
  - Goal: eBPF runtime security events.
  - Steps: Helm chart under `gitops/components/tetragon/`; enable a starter
    policy that logs suspicious exec; expose Tetragon events to the agent.
  - Done when: triggering `bash` inside an nginx pod produces a Tetragon
    event visible to the Security Agent.

- [ ] **T4.3 Install Falco (rule-based alerts)**
  - Goal: complementary runtime rule alerting.
  - Steps: Falco Helm chart; ship alerts to Loki + Alertmanager.
  - Done when: a Falco rule fires and you can see it in Grafana.

## M4.2 Admission & supply chain
- [ ] **T4.4 Install Kyverno + baseline policies**
  - Goal: block bad manifests at admission time.
  - Steps: Kyverno Helm chart; policies under `gitops/components/kyverno/`:
    disallow `:latest` images, require resource limits, disallow privileged
    containers, require image signatures.
  - Done when: deploying a pod with `image: nginx:latest` is rejected.

- [ ] **T4.5 Sign images with Cosign**
  - Goal: images are signed in CI and verified at admission.
  - Steps: add a CI job that runs `cosign sign` on the built image using a
    keyless identity; add a Kyverno `verifyImages` policy.
  - Done when: an unsigned image cannot be deployed.

- [ ] **T4.6 Trivy scanning in CI and cluster**
  - Goal: catch vulnerabilities + IaC misconfigurations.
  - Steps: existing CI Trivy job + add a scheduled Trivy operator in
    cluster that reports VulnerabilityReports.
  - Done when: a known-vulnerable test image is flagged in CI and in the
    cluster.

## M4.3 Wire security into agents
- [ ] **T4.7 Security Agent eats runtime events**
  - Goal: real security alerts drive the agent graph.
  - Steps: Tetragon/Falco events land in Postgres via a collector; the
    Triage Agent pulls recent security events and routes to the Security
    Agent.
  - Done when: a reverse shell attempt in a pod produces a Security Agent
    diagnosis + a plan (cordon node).

- [ ] **T4.8 External Secrets + Vault**
  - Goal: no secrets in git.
  - Steps: deploy a dev Vault; configure External Secrets Operator to sync
    Postgres credentials + LLM API keys from Vault into K8s Secrets.
  - Done when: removing a Secret from git does not break the deploy.

✅ **Phase 4 complete when:** opening a reverse shell in a demo pod triggers
Sentinel to detect, diagnose, and (with approval) cordon the node — and your
deploys are gated by signatures + policies.

---

# Phase 5 — Polish, Evals, Portfolio (week 16–18)

**Phase goal:** close the loop cleanly, measure quality, and turn it into a
hirable portfolio piece.

## M5.1 SLOs & on-call simulation
- [ ] **T5.1 Deploy Pyrra SLOs**
  - Goal: SLOs + error budgets visible.
  - Steps: Pyrra Helm chart under `gitops/components/pyrra/`; define SLOs
    for the demo API (availability + latency); Grafana dashboard.
  - Done when: an error-budget burn chart appears in Grafana.

- [ ] **T5.2 On-call simulation scripts**
  - Goal: reproducible incident injections.
  - Steps: `scripts/chaos/{oom.sh,crashpod.sh,reverseshell.sh}` that each
    trigger a known demo scenario.
  - Done when: each script deterministically drives Sentinel through the
    loop.

## M5.2 Evaluations dashboard
- [ ] **T5.3 Agent success-rate metrics**
  - Goal: measure agent effectiveness, not just retrieval.
  - Steps: log every agent run with start/end + outcome to Postgres; expose
    `/metrics` counting `agent_runs_total{agent, outcome}`.
  - Done when: Grafana shows success rate per agent.

- [ ] **T5.4 Evals dashboard page**
  - Goal: one place showing retrieval + agent quality.
  - Steps: Next.js page `/evals` pulling recall@k, faithfulness, and agent
    success rates.
  - Done when: visiting `/evals` shows current quality numbers.

## M5.3 Portfolio assets
- [ ] **T5.5 README polish**
  - Goal: a stranger "gets it" in 2 minutes.
  - Steps: hero section; Mermaid architecture diagram; feature screenshots;
    quick-start; link to the demo video.
  - Done when: README renders cleanly on GitHub.

- [ ] **T5.6 5-minute demo video**
  - Goal: the conversion asset.
  - Steps: script + record the 3 best demos (Ask Sentinel, self-healing,
    security autopilot); upload unlisted to YouTube; link in README.
  - Done when: video link is in the README and plays the full loop.

- [ ] **T5.7 Three blog posts**
  - Goal: written signal of depth.
  - Steps: draft + publish on dev.to/LinkedIn:
    1. "Building a custom K8s operator for AI-driven remediation"
    2. "Production RAG with a self-updating knowledge base"
    3. "eBPF runtime security feeding an LLM agent"
  - Done when: all 3 published and linked from the README.

- [ ] **T5.8 Optional live demo**
  - Goal: a clickable read-only playground.
  - Steps: deploy a small cloud K8s with Terraform; read-only Grafana + a
    sandboxed chat; harden with rate limits.
  - Done when: a public URL is in the README and works for a visitor.

✅ **Phase 5 complete when:** repo + README + video + blog posts + evals
dashboard together tell the story. Project is portfolio-ready.

---

# Stretch goals (after Phase 5, optional)

- [ ] **S1 Multi-cluster** — manage several clusters from one Sentinel
  control plane.
- [ ] **S2 MCP server** — expose Sentinel tools to any MCP-compatible
  IDE/agent.
- [ ] **S3 Fine-tuned small model (LoRA)** — train on incident history for
  cheap local reasoning.
- [ ] **S4 Policy-as-code generation** — Security Agent writes Kyverno/Rego
  rules for gaps it discovers.
- [ ] **S5 Chaos engineering hook** — Sentinel + Chaos Mesh runs game-days
  and learns from the failures.
- [ ] **S6 Voice mode for on-call** — talk to Sentinel over the phone.

---

# Quick reference: phase → primary deliverable

| Phase | Primary deliverable |
|---|---|
| 0 | `git push` → deployed, observable demo app |
| 1 | `/ask` answers questions about your codebase with citations |
| 2 | Chat agent inspects live cluster + uses KB |
| 3 | End-to-end self-healing with operator + human approval |
| 4 | Security autopilot + signed/policy-gated deploys |
| 5 | Portfolio-ready: repo, video, blog posts, evals dashboard |
