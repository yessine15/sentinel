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
- [x] **TASKS.md** — full build plan decomposed into mini-tasks.
- [x] **Monorepo initialized** — git repo, folder structure, .gitignore,
  README.md (with Mermaid architecture diagram), MIT License.
  Remote: `https://github.com/yessine15/sentinel.git`
- [x] **Prerequisites installed** — Docker, kind, kubectl, helm, terraform,
  go, Python 3.12, Node 22, ollama, trivy, cosign, uv. Verified via
  `scripts/install-check.sh`.
- [x] **Python & Node env hygiene** — `pyproject.toml` with uv, phase-based
  optional dependency groups, `.python-version`, frontend `package.json` +
  `.nvmrc` + `.prettierrc.json`, `.pre-commit-config.yaml` (ruff, mypy,
  prettier, golangci-lint).
- [x] **kind cluster created** — `infra/kind-cluster.yaml` + `scripts/kind-up.sh`.
  3 nodes (1 control-plane + 2 workers), K8s v1.32.0, port mappings for
  80/443/30080-30083. Cluster name: `sentinel`, context: `kind-sentinel`.
- [x] **Ingress installed** — ingress-nginx via Helm with kind-specific
  values (`hostNetwork`, `nodeSelector: ingress-ready`, `enable-ssl-passthrough`).
  `/etc/hosts` entries added for `sentinel.local`, `grafana.local`,
  `loki.local`, `argocd.local`, `qdrant.local`, `llm.local`.
  `curl http://sentinel.local` returns HTTP 404 (default backend — expected).
- [x] **ArgoCD installed + App-of-Apps bootstrap** — ArgoCD via Helm with
  Ingress at `argocd.local` (HTTPS, ssl-passthrough). The root Application
  in `gitops/argocd/apps/root.yaml` watches `gitops/argocd/apps/` and
  auto-syncs every child Application added there. Bootstrap applied via
  `scripts/install-argocd.sh`. UI reachable at https://argocd.local
  (self-signed cert — accept in browser). Initial admin password:
  `kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d`.
- [x] **GitOps folder layout** — convention: `gitops/base/` (Kustomize bases
  for namespaces + a shared labels Component), `gitops/components/` (cluster-
  wide Helm releases: argocd, ingress-nginx, observability), `gitops/projects/`
  (Sentinel's own apps: demo-api skeleton in place, frontend/operator later).
  `gitops/README.md` documents the convention. demo-api Helm chart skeleton
  + ArgoCD Application (`gitops/argocd/apps/demo-api.yaml`) committed; ArgoCD
  will auto-discover and sync it. Real app code lands in T0.13.
- [x] **CI workflow skeleton** — `.github/workflows/ci.yml` with 5 parallel
  jobs triggered on push to main + any PR: `python` (ruff check + ruff
  format --check + pytest), `go` (gofmt + vet + test on operator/),
  `frontend` (prettier --check + next lint + tsc + next build, all
  `continue-on-error: true` until the real Next.js app exists in T2.6),
  `manifests` (helm lint all charts + kustomize build bases + YAML syntax
  check via inline Python), `security` (Trivy filesystem scan CRITICAL-only
  + tfsec --soft-fail). Smoke tests: sentinel_api/sentinel_rag/
  sentinel_agents package __init__.py + 2 pytest cases each; operator/
  main.go + main_test.go. pyproject.toml now has `pythonpath` in pytest
  config. frontend/tsconfig.json minimal placeholder.
- [x] **Container build pipeline** — `api/sentinel_api/main.py` is a minimal
  FastAPI app (`/ping`, `/healthz`, `/readyz`, `/`) — enough to build, run,
  and let ArgoCD deploy. `Dockerfile`: multi-stage uv-based build, slim
  runtime, non-root `sentinel` user (uid 1001), HEALTHCHECK wired,
  `PYTHONPATH=/app/api`. `.dockerignore` keeps context tiny.
  `.github/workflows/build.yml`: triggers on push to main + tags `v*`;
  builds linux/amd64 via Docker Buildx; pushes to
  `ghcr.io/yessine15/sentinel-demo-api` with tags `vX.Y.Z` + `X.Y` +
  `main` + `latest` via docker/metadata-action; auto-auths to GHCR with
  the built-in `GITHUB_TOKEN` (`packages: write`). Tag `v0.1.0` pushed;
  first real build running on GitHub Actions. Verified locally: image
  builds, container runs, all 4 endpoints return HTTP 200.
- [x] **kube-prometheus-stack deployed** — Helm install via
  `scripts/install-observability.sh` using values in
  `gitops/components/observability/kube-prometheus-stack.yaml`. All pods
  running (Prometheus, Alertmanager, Grafana, node-exporter, kube-state-
  metrics, operator). Prometheus scraping 28 targets. Grafana serving at
  http://grafana.local (admin/admin), with Prometheus pre-configured as
  the default datasource (chart ships it natively — we removed our
  duplicate because it caused a "only one datasource can be default"
  provisioning crash). Note: a few kube-* targets (controller-manager,
  etcd) show "down" because kind doesn't expose them scrape-friendly —
  normal for local kind clusters.
- [x] **demo-api Application fix** — removed the bogus `plugin:` source
  in `gitops/argocd/apps/demo-api.yaml` and replaced with the native
  ArgoCD `helm:` source (`valueFiles: [values.yaml]`) so ArgoCD auto-
  renders the chart instead of failing with "could not find plugin
  supporting the given repository". The demo-api Application should now
  pick up the v0.1.0 image from GHCR on its next sync.
- [x] **Tempo + OTel collector deployed** — Tempo via `grafana/tempo` chart
  with filesystem storage + metrics generator. OTel collector via
  `open-telemetry/opentelemetry-collector` chart (contrib build). Tempo
  datasource added to Grafana. Tempo verified (HTTP 200 on /ready).
  Full observability stack complete (metrics + logs + traces).
- [x] **Demo API instrumented & deployed** — the demo FastAPI app in
  `api/sentinel_api/main.py` now has full OpenTelemetry instrumentation:
  FastAPIInstrumentor for traces, structlog for structured JSON logs, and
  request timing middleware. The v0.1.2 image is live via ArgoCD, serving
  at `http://sentinel.local/ping`. Structured logs flow to Loki (via
  Promtail), traces flow to Tempo (via OTel collector), and the app
  exposes all standard health endpoints. GHCR pull secret configured.
- [x] **Phase 0 complete** — all tasks T0.1–T0.14 are done.
  Foundation: kind cluster, ingress-nginx, ArgoCD (App-of-Apps), CI
  (5 jobs), container build pipeline, full observability stack
  (Prometheus/Grafana/Loki/Tempo/OTel), demo API instrumented and
  deployed. Ready for Phase 1 (RAG core).
- [x] **T1.1 Qdrant deployed** — vector DB running in `qdrant` namespace,
  single-replica with 2Gi persistent storage, exposed at
  http://qdrant.local via ingress-nginx. REST API returns
  `{"result":{"collections":[]},"status":"ok"}`. Helm chart wrapper at
  `gitops/components/qdrant/`, ArgoCD Application at
  `gitops/argocd/apps/qdrant.yaml`, walkthrough at
  `docs/T1.1_qdrant_walkthrough.md`.
- [x] **T1.2 Postgres + pgvector deployed** — single-replica Postgres 16
  with the `pgvector` extension in the `sentinel` database, running in the
  `postgres` namespace. Self-contained Helm chart at
  `gitops/components/postgres/` (no upstream dependency). Uses the
  `pgvector/pgvector:pg16` image; `CREATE EXTENSION vector` runs on first
  boot via an initdb ConfigMap. ClusterIP Service on :5432 (port-forward
  for host access). Walkthrough at `docs/T1.2_postgres_walkthrough.md`.
- [x] **T1.3 Source connectors defined** — pluggable loaders in
  `rag/sentinel_rag/sources/` that turn each source type into
  `Document` objects (frozen dataclass with `doc_id`, `source_type`,
  `path`, `line_start/end`, `text`, `metadata`):
  `markdown.py` (all `.md` under a dir), `runbook.py` (front-matter-aware
  runbook markdown under `docs/runbooks/`), `code.py` (Python/Go/TS/JS/
  YAML/HCL/shell, prunes `node_modules`/`__pycache__`/`.venv` etc.),
  `postgres_incident.py` (rows from the `incidents` table via psycopg,
  with a bundled JSON sample fallback when the DB is unreachable).
  Each connector has a `__main__` so it runs standalone and prints a
  preview. 14 unit tests in `rag/tests/test_sources.py`; all pass.
  Two sample runbooks seeded under `docs/runbooks/`. Walkthrough at
  `docs/T1.3_source_connectors_walkthrough.md`.
- [x] **T1.4 AST-aware chunkers** — two chunkers in
  `rag/sentinel_rag/chunkers/`: `ProseChunker` (sentence-aware sliding
  window with configurable chunk_size + overlap, for markdown/runbook/
  incident prose) and `CodeChunker` (tree-sitter AST parser that splits
  Python/Go/TypeScript/JavaScript at function/class/method boundaries;
  unsupported languages fall back to blank-line-based chunking). Each
  chunk records exact `line_start`/`line_end` from AST positions. 20 unit
  tests in `rag/tests/test_chunkers.py`; all pass. Both chunkers run
  standalone via `python -m sentinel_rag.chunkers.{prose,code}`.
  Depends on `tree-sitter` + language grammars (python, go, typescript,
  javascript). Walkthrough at `docs/T1.4_chunkers_walkthrough.md`.
- [x] **T1.5 Embedding service** — `rag/sentinel_rag/embed.py` with two
  providers: `OllamaEmbedder` (BGE-M3, 1024-dim, local via Ollama's
  `/api/embed` endpoint) and `OpenAIEmbedder` (text-embedding-3-small/
  large, configurable dimensions, OpenAI-compatible API). Factory function
  `get_embedder()` selects provider via `EMBED_PROVIDER` env var. Each
  embedder has `embed(text)` and `embed_batch(texts)` with true batch
  API calls (not sequential loops). 29 unit tests in
  `rag/tests/test_embed.py`; all pass. Standalone smoke-test via
  `python -m sentinel_rag.embed "hello world"`.
- [x] **T1.6 Ingest CLI** — `rag/sentinel_rag/ingest.py` wires together the
  full pipeline: source connectors (T1.3) → chunkers (T1.4) → embedder
  (T1.5) → Qdrant (T1.1). Subcommands: ``code``, ``markdown``, ``runbook``.
  Stores points with two named vectors: ``dense`` (from the embedder) and
  ``sparse`` (corpus-wide TF-IDF via :class:`SparseEncoder`). 35 unit tests
  in ``rag/tests/test_ingest.py``; all pass. Usage:
  ``python -m sentinel_rag.ingest code ./api``.
- [x] **T1.7 Hybrid retriever** — `rag/sentinel_rag/retrieve.py` queries
  Qdrant with dual prefetch (dense cosine + sparse dot-product) fused via
  Reciprocal Rank Fusion (RRF). Returns top-50 :class:`RetrievedPoint`
  results with scores. Shared sparse utilities extracted to
  `rag/sentinel_rag/sparse.py` (deterministic CRC32 hash-based indices,
  same tokenization for ingest and retrieval). ``SparseEncoder`` in ingest
  updated to use hash-based indices for cross-module compatibility.
  20 new tests in ``rag/tests/test_sparse.py``, 21 in
  ``rag/tests/test_retrieve.py``; all 140 RAG tests pass. Usage:
  ``python -m sentinel_rag.retrieve "how does auth work?"``.
- [x] **T1.8 Cross-encoder reranker** — `rag/sentinel_rag/reranker.py`
  wraps BGE reranker-v2-m3 via `sentence-transformers.CrossEncoder` for
  on-device re-ranking of hybrid retrieval candidates. Lazy model loading;
  configurable via `RERANK_MODEL` / `RERANK_DEVICE` env vars. The
  `retrieve()` function now accepts an optional `reranker` parameter to
  pipe hybrid results through the cross-encoder. 32 tests in
  `rag/tests/test_reranker.py`; all 172 RAG tests pass. Usage:
  ``python -m sentinel_rag.reranker "how does auth work?"``.
- [x] **T1.9 Citation renderer** — `rag/sentinel_rag/citation.py` with
  `format_source_marker()` (produces `[path:lines]` markers),
  `render_citation_json()` (returns `{answer, sources:[{path, lines,
  snippet}]}`), `render_citation_block()` and `render_citation_text()`
  (numbered sources block for human-readable output), and CLI `main()`.
  38 tests in `rag/tests/test_citation.py`; all 210 RAG tests pass.
- [x] **T1.10 Golden Q&A set** — `rag/eval/golden.jsonl` with 16 hand-written
  Q&A pairs covering the full codebase: API endpoints, embedding models,
  chunker/reranker config, Qdrant collection, source connectors, runbooks,
  cluster topology, ArgoCD GitOps, observability stack, Docker build,
  Postgres incidents, sparse encoding, and citation format. Each entry has
  `id`, `question`, `answer`, and `sources` fields. All referenced source
  files verified to exist.
- [x] **T1.11 Eval script + metrics** — `rag/eval/run.py` with
  `load_golden()` (loads golden.jsonl with validation), `compute_recall_at_k()`
  (checks expected sources in top-k), `run_eval()` (full pipeline with
  injectable retriever), `format_metrics_table()` (coloured terminal table
  with HIT/MISS/PASS/FAIL), and CLI `main()` (argparse, -k/-t/--json/
  --skip-reranker flags). Configurable via `EVAL_GOLDEN_PATH`, `EVAL_K`,
  `EVAL_THRESHOLD`, `EVAL_SKIP_RERANKER` env vars. Exits 0 when recall@k
  >= threshold, 1 otherwise. 36 tests in `rag/tests/test_eval.py`;
  all 246 RAG tests pass.
- [x] **T1.12 LiteLLM gateway** — Lightweight Python proxy (~100KB, stdlib-only)
  running as a host process that provides OpenAI-compatible
  `/v1/chat/completions` and `/v1/embeddings` by forwarding to Ollama at
  `localhost:11434`.  Kubernetes Service (no selector) + manual Endpoints
  route traffic from ingress-nginx to the host via Docker bridge gateway
  (`172.18.0.1:4000`).  Exposed at `http://llm.local`.  Helm chart at
  `gitops/components/litellm/`, ArgoCD Application at
  `gitops/argocd/apps/litellm.yaml`, install script at
  `scripts/install-litellm.sh`.  Verified: chat completions return live
  responses from gemma4/qwen35/qwen36.
- [x] **T1.13 FastAPI `/ask` endpoint** — New route at
  `api/sentinel_api/routes/ask.py`.  `POST /ask` accepts `{"query": "..."}`,
  runs hybrid retrieval (dense + sparse, RRF fusion), cross-encoder reranking
  (BAAI/bge-reranker-v2-m3), builds a prompt with source chunks, calls the LLM
  gateway at `http://llm.local/v1/chat/completions`, and returns
  `{"answer": "...", "sources": [...], "model": "...", "latency_ms": ...}`
  with cited sources.  Verified: `curl http://localhost:8000/ask -d
  '{"query":"where is the /ping handler?"}'` returns a grounded answer citing
  `sentinel_api/main.py:102-108`.  Ingest populated Qdrant with 321 chunks
  from 34 code documents using `nomic-embed-text` (768-dim).  Fixed
  `OllamaEmbedder.dimension` to detect actual vector size dynamically.
  Fixed `_build_points` to use UUIDs for Qdrant point IDs.  Updated
  `CrossEncoderReranker.rerank` to handle numpy arrays from
  `sentence-transformers`.  All 248 tests pass.
- [x] **T1.14 CI runs the eval gate** — New `eval-gate` CI job spins up a Qdrant
  service container, ingests `./api` and `./rag` using a new `LocalEmbedder`
  class (sentence-transformers `all-MiniLM-L6-v2`, 384-dim, no Ollama needed),
  and runs `eval.run --skip-reranker`.  Fails if recall@k drops below threshold
  (0.4).  The `EMBED_PROVIDER=local` env var selects the in-process embedder.
  Gate catches chunker/retrieval regressions before they reach main.
  All 249 tests pass.
- [x] Phase 1: RAG core (complete — T1.1–T1.14 done).
- [x] Phase 2: Single agent SRE (complete — T2.1–T2.8 done).
- [x] Phase 3: Multi-agent + operator.
- [x] **T3.1 Triage Agent** — `triage_agent_node` classifies user
  queries into `sre` / `knowledge` / `general` via LLM (with keyword
  fallback).  `route_to_specialist` dispatches to the SRE agent, which
  adapts via routing-specific system prompts.  WebSocket emits new
  `classification` event.  93 tests pass (20 graph + 54 tools + 19 API).
  Live smoke test confirms correct classification: SRE queries route
  with kubectl tools, knowledge queries trigger rag_search, greetings
  get friendly responses.  Versions bumped to 0.3.0.
- [x] **T3.2 Security Agent** — four new allow-listed security tools:
  `trivy_scan` (image/fs/repo vuln+misconfig+secret scanning via the
  trivy CLI), `cve_lookup` (single-CVE lookup against the public
  OSV.dev API, canonical CVE-YYYY-NNNN id validated), `falco_events`
  (read-only Falco runtime alerts — "shell in container", "/etc/shadow"
  reads, etc.), `tetragon_events` (read-only eBPF exec/network/file/dns
  events).  All four validate inputs against frozenset allow-lists and
  degrade to stub mode when their backend isn't deployed.  Dedicated
  `security_agent_node` with a `SECURITY_TOOLS` subset
  (trivy+cve+falco+tetragon + kubectl get/describe + rag_search —
  promql/logql excluded), its own `sec_tools` ToolNode +
  `should_continue_security` router, and a `security` category added
  to the triage prompt + keyword fallback (checked first so a
  "suspicious exec in a pod" routes to security, not SRE).  Chat
  WebSocket streams `security_agent` events through the same
  token/tool flow as the SRE agent.  139 tests pass (35 graph + 70
  tools [incl. 30 security] + 19 API + 15 live skipped).  Live smoke
  test confirms "suspicious exec in a pod" → classification=security
  → `security_agent`; the `sec_tools` loop wiring is verified with a
  synthetic structured tool call (`falco_events` returns its "Terminal
  shell in container" stub payload).  Versions bumped to 0.4.0.
  Note: gemma4 via Ollama emits tool calls as TEXT (`<tool_code>…`)
  rather than structured LangChain tool_calls, so the live runtime
  tool loop doesn't fire end-to-end on gemma4 — see
  `/memories/repo/gemma4-tool-calling.md`; the T3.2 acceptance
  criterion (flagged security at triage) is satisfied.
- [ ] Phase 4: Security hardening.
- [ ] Phase 5: Polish, evals, portfolio.

> **We are at:** Phase 3 in progress. T3.1 + T3.2 done. T3.3 next.
> **Next up:** T3.3 — Cost Agent.
> **Foundation:** kind cluster, ingress-nginx, ArgoCD (App-of-Apps), full
> observability stack (Prometheus, Alertmanager, Grafana, Loki, Tempo),
> Qdrant vector DB, Postgres 16 + pgvector, LiteLLM gateway at
> http://llm.local, FastAPI `/ask` + WebSocket `/chat/ws` endpoints,
> LangGraph multi-agent graph (triage → SRE / Knowledge / Security
> specialists) with 9 allow-listed tools (kubectl get/describe, PromQL,
> LogQL, RAG search, trivy_scan, cve_lookup, falco_events,
> tetragon_events), Next.js 15 chat UI with streaming answers +
> clickable citation chips, Helm chart + ArgoCD Application for
> frontend deployment at http://sentinel.local.

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

### Task conventions (LLM behaviour)

- **After every task completion**, generate a detailed walkthrough doc under
  `docs/` with naming pattern `docs/T0.XX_shortname_walkthrough.md`. The doc
  explains what was added, what commands were executed, what went wrong, and
  how each problem was fixed — written for someone new to the subject.
  Examples: `docs/T0.10_observability_walkthrough.md`,
  `docs/T0.12_tempo_walkthrough.md`.
- **Commit and push after every completed task.** Once a task is marked `[x]`
  in `TASKS.md` and the walkthrough doc is written, stage all changes, commit
  with a descriptive message referencing the task ID, and push to the remote
  immediately. This keeps ArgoCD in sync (App-of-Apps watches the remote
  repo) and prevents local drift. Do **not** batch multiple tasks into a
  single push.
