# Sentinel — Detailed Beginner's Explanation

> A plain-English companion to `PROJECT_IDEA.md`.
> This document explains **every** term, technology, and concept used in the
> project, assuming you know how to program but are new to Kubernetes, DevOps,
> security, and AI agents. Read this alongside the original idea doc.

---

## 0. How to read this document

The original idea doc (`PROJECT_IDEA.md`) describes **what** we are going to
build. This document explains **what each piece actually means**.

When a term is introduced for the first time, it is explained in a short
"🗣️ What is X?" box. After that, the term is used normally. There is also a
full **Glossary** at the end (Section 16), so if you forget something, jump
there.

---

## 1. Why this project — explained in plain words

If you want a career in **DevOps**, **Platform Engineering**, **Site
Reliability Engineering (SRE)**, or **AI Engineering**, you usually need to show
hiring managers that you can work with several modern technologies at the same
time. Most people build a simple to-do app or a basic CI/CD pipeline. Those are
boring because everyone has them.

> 🗣️ **What is DevOps?**
> A way of working where the people who *write* software (Dev) and the people
> who *run* software (Ops) stop being separate teams and instead share tools
> and responsibilities. The goal is to ship changes faster and more safely.

> 🗣️ **What is SRE (Site Reliability Engineering)?**
> A discipline (invented at Google) that treats operations like a software
> problem. Instead of humans manually restarting things, SREs write code and
> define rules (like "this service must be available 99.9% of the time") so
> systems run themselves as much as possible.

> 🗣️ **What is Platform Engineering?**
> Building an internal "platform" (tools + services) so that other developers
> in the company can deploy and operate their apps easily, like using a public
> cloud (AWS, GCP) but built in-house.

The original idea doc gives a table of "disciplines" and where they appear in
Sentinel. Here is that same table with each discipline explained:

| Discipline | Plain-English meaning | Where it shows up in Sentinel |
|---|---|---|
| **DevOps / GitOps** | Shipping changes automatically, using a Git repository as the source of truth | ArgoCD + Terraform + GitHub Actions |
| **Kubernetes (advanced)** | Running lots of containers across many machines, and controlling them like one system | Custom operator, CRDs, autoscaling, policies |
| **Security / DevSecOps** | Baking security into every step of building and shipping software (not just at the end) | Trivy, Falco, Tetragon, Kyverno, Cosign |
| **Multi-AI agents** | Several specialized AI programs that talk to each other and divide the work | LangGraph orchestrator with specialized agents |
| **RAG** | A technique where an AI looks up real documents before answering, so it does not hallucinate | Vector database over runbooks / incidents / code |
| **Observability** | Being able to understand a system from the outside by looking at its metrics, logs, and traces | Prometheus, Loki, Tempo, Grafana, OpenTelemetry |
| **eBPF** | A way to run tiny programs inside the Linux kernel to watch network traffic and security events in real time | Cilium (networking) + Tetragon (runtime security) |
| **LLMOps** | The "DevOps of AI": managing models, prompts, evaluations, costs | Local + cloud models, evals, prompt management |
| **SRE / Platform eng.** | Running systems reliably with measurements and automation | SLOs, error budgets, self-healing, toil reduction |

> 🗣️ **What is a container?**
> A lightweight, standalone package that contains an application plus
> everything it needs to run (libraries, settings). A container runs the same
> way on your laptop and on a server. **Docker** is the most famous tool for
> building containers.

> 🗣️ **What is Kubernetes (often abbreviated "K8s")?**
> A system that manages **containers** for you. If you have 50 containers
> spread across 10 machines, Kubernetes decides where each container runs,
> restarts crashed ones, scales them up/down, and balances traffic between
> them. Think of it as an "operating system for a data center".

> 🗣️ **What is GitOps?**
> A style of DevOps where the desired state of your system is stored in a
> **Git repository**. A tool continuously compares your Git repo with the real
> cluster, and if they differ, it updates the cluster to match. So `git` is the
> single source of truth, not a person typing `kubectl edit`.

---

## 2. The problem Sentinel solves — explained in plain words

Small teams and solo developers usually cannot afford a 24/7 operations and
security team. When something breaks at 3am, several painful things happen:

- **Logs are scattered.** Each service writes logs in a different place, so
  nobody knows where to look first.

> 🗓️ **Logs** are timestamped text records of what an application did
> (e.g. "user 42 logged in", "database query took 800ms"). They are the
> first thing engineers look at when debugging.

- **Runbooks exist but nobody reads them under pressure.**

> 🗓️ **Runbook** = a written step-by-step guide that says "if symptom X
> happens, do steps A, B, C to fix it". Good teams write them, but at 3am
> nobody has the patience to read them carefully.

- **Security alerts pile up without triage.**

> 🗓️ **Triage** = the act of looking at a pile of problems and deciding which
> ones are urgent and which can wait. Comes from emergency medicine.

- **Repeat incidents occur because knowledge is never captured.**

The core idea of Sentinel: act like a **tier-1 on-call engineer** — the
junior-but-competent person who is first to respond to an incident. Sentinel:

1. **Detects** that something is wrong (an anomaly).
2. **Pulls** the relevant runbook or past incident using RAG (so it knows
   similar problems and their fixes).
3. **Diagnoses** the root cause by reasoning across multiple specialized AI
   agents.
4. **Proposes** a remediation, and with human approval, **executes** it.
5. **Writes a postmortem** and feeds the lesson back into the knowledge base.

> 🗓️ **Postmortem** = a document written *after* an incident is resolved,
> explaining what happened, why, what was the impact, and how to prevent it
> next time. Good teams write them blamelessly.

This creates a **closed learning loop**: every incident makes Sentinel smarter.
That "second time the same problem happens, it's solved 10× faster" is the
demonstration that impresses interviewers.

---

## 3. High-level architecture — explained piece by piece

The original doc shows an ASCII diagram. Let's narrate it from top to bottom.

```
You / Slack / Web  ──►  API Gateway + Chat UI  ──►  Agent Orchestrator
(FastAPI + WebSocket + Next.js)          (LangGraph state machine)
```

### 3.1 The user interface
- **You / Slack / Web**: you can talk to Sentinel from a web browser, from
  Slack, etc.
- **API Gateway + Chat UI**: the front door. It receives your question and
  streams the answer back token by token.

> 🗓️ **API (Application Programming Interface)** = a defined way for
> programs to talk to each other, usually over HTTP (like a website does).

> 🗓️ **FastAPI** = a Python web framework that is very fast and good at
> building APIs. We use it because Python is also the language of the AI
> ecosystem, so the API and the AI code can share the same world.

> 🗓️ **WebSocket** = a protocol that keeps a connection open so the server
> can push pieces of the answer to the browser live (like ChatGPT streaming
> words). Regular HTTP would wait and return the whole answer at once.

> 🗓️ **Next.js** = a popular framework for building web apps with **React**
> (a JavaScript library for UIs). Next.js adds server-side rendering, routing,
> and other production features on top of React.

### 3.2 The agent orchestrator
- **Agent Orchestrator (LangGraph)**: the brain that decides which agents to
  wake up and in what order.

> 🗓️ **What is an "agent"?**
> In the LLM world, an agent is an LLM (Large Language Model) that can take
> *actions*, not just chat. It is given tools (functions it can call) and a
> goal; it decides which tool to call, observes the result, and continues
> reasoning until the goal is reached.

> 🗓️ **LangGraph** = a library that lets you build agents as a **graph**
> (nodes + edges). Each node is a step (call an agent, run a tool, ask a
> human). Edges describe how to move from one step to the next. Treating it
> as a graph/state-machine (rather than a loose chain) makes the system robust:
> you can add loops ("retry"), parallel branches, and human approval gates.

> 🗓️ **State machine** = a model where the system is always in one of several
> defined "states" and can move to another state only via allowed transitions.
> Think of an order: `created → paid → shipped → delivered`. LangGraph works
> the same way but for agent steps.

### 3.3 The specialized agents
The orchestrator sends work to specialized agents. Think of them as a small
team of colleagues. Each one is best at one thing:

| Agent | Job |
|---|---|
| **Triage Agent** | Receives a raw alert, adds labels and severity, routes it to the right specialist. |
| **SRE Agent** | Reads metrics, logs, and traces; guesses the root cause. Tools: `kubectl`, PromQL, LogQL. |
| **Security Agent** | Decides if the alert is security-related, checks Tetragon/Falco events, looks up CVEs. |
| **RAG Agent** | Searches the knowledge base (runbooks, past incidents, code, docs) and returns ranked evidence with citations. |
| **Cost Agent** | Spots wasted money, idle resources, things that are too big for what they do. |
| **Code Review Agent** | On pull requests, reviews infra code (Helm, Terraform, Dockerfiles) for best practices and security. |
| **Executor Agent** | The *only* agent allowed to take real actions, and only after a human approves (or auto-approve in a sandbox). |
| **Postmortem Agent** | Writes the incident writeup, extracts lessons, and writes them **back into the vector database** so the system learns. |

> 🗓️ **PromQL** = the query language used by **Prometheus** (a metrics
> database). You write things like `rate(http_requests_total[5m])` to ask
> "how many HTTP requests per second over the last 5 minutes?".

> 🗓️ **LogQL** = the query language used by **Loki** (a logs database). It
> looks like PromQL mixed with grep: `{app="payments"} |= "error"`.

> 🗓️ **CVE (Common Vulnerabilities and Exposures)** = a publicly-listed,
> numbered security bug in a piece of software. For example "CVE-2024-12345:
> buffer overflow in libfoo 1.2". When a new vulnerability is found, it gets a
> CVE number so everyone can refer to it precisely.

> 🗓️ **Pull Request (PR)** = a proposal to change code in a Git repository.
> You "open a PR", other people review it, and once approved it is merged.
> GitHub calls these "Pull Requests", GitLab calls them "Merge Requests".

### 3.4 The shared backbone
Under the agents sit three big components:

1. **RAG Knowledge Base** (Qdrant + pgvector + reranker) — the memory.
2. **Tool / Action Bus** (kubectl, helm, terraform, gh) — the hands.
3. **LLM Gateway** (Ollama local + OpenAI/Anthropic) — the brain's language.

> 🗓️ **Vector database** = a database that stores things as lists of numbers
> called **vectors** (embeddings). Things that are semantically similar end up
> close together in vector space, so you can search by meaning, not only by
> keywords. **Qdrant** and **pgvector** (a Postgres extension) are two options.

> 🗓️ **Embedding** = a way to turn text (a sentence, a paragraph, a chunk of
> code) into a fixed-length list of numbers (e.g. 1024 floats). Similar texts
> produce similar vectors. Embeddings are what make "semantic search" possible.

> 🗓️ **Reranker (cross-encoder)** = a model that takes a query and a candidate
> document *together* and scores how relevant the document is. It is more
> accurate than the first-stage vector search but slower, so the typical
> pipeline is: fast vector search → get top 50 → reranker re-orders to top 5.

> 🗓️ **kubectl (kube-control)** = the command-line tool to talk to a
> Kubernetes cluster (e.g. `kubectl get pods`). It is how humans and scripts
> ask Kubernetes to do things.

> 🗓️ **helm** = the package manager for Kubernetes. A **Helm chart** is a
> bundle of templated Kubernetes manifests plus default values. Installing a
> complex app becomes `helm install ...` instead of writing dozens of YAML
> files by hand.

> 🗓️ **terraform** = a tool to provision cloud infrastructure (servers,
> databases, networks) by writing declarative code instead of clicking in a
> web console. You write "I want 3 VMs and a database", run `terraform apply`,
> and it creates them.

> 🗓️ **gh** = GitHub's official command-line tool. Used here to automate PRs,
> issues, comments.

> 🗓️ **Ollama** = a tool to run large language models locally on your own
> machine (privacy: nothing leaves your laptop). You can run e.g. `llama3`,
> `mistral`, etc.

> 🗓️ **LLM Gateway / LiteLLM** = a single API in front of many model
> providers (OpenAI, Anthropic, Google, Ollama). It handles fallbacks (if
> OpenAI is down, use Anthropic), rate limits, and cost tracking. We use
> **LiteLLM** as the gateway.

### 3.5 The Kubernetes Operator
Below the agents sits a **custom Kubernetes Operator**. It is the safe bridge
between the AI proposing an action and that action actually happening in the
cluster.

> 🗓️ **Operator pattern** = a way to extend Kubernetes with your own custom
> resource type and a small program ("controller") that watches those
> resources and does whatever they describe. Operators turn "operational
> knowledge" into code.

> 🗓️ **CRD (Custom Resource Definition)** = the way you tell Kubernetes "I
> want a new kind of object, called `RemediationPlan`, with these fields".
> After that, you can create instances of it just like `Pod` or `Deployment`.

> 🗓️ **Reconciliation loop** = the heart of any Kubernetes controller. It
> repeatedly asks: "what is the *desired* state? what is the *actual* state?
> what action do I take to make actual match desired?". Over and over, forever.

### 3.6 The observability layer
The bottom of the diagram shows the **observability stack**:

| Component | Plain-English role |
|---|---|
| **Prometheus** | Stores metrics (numbers over time: CPU, RAM, request rate, error rate). |
| **Loki** | Stores logs (text lines with timestamps). |
| **Tempo** | Stores traces (the path a single request takes across services). |
| **Grafana** | The dashboards website that visualizes all of the above. |
| **OpenTelemetry (OTel)** | A standard way for your own code to emit metrics, logs, and traces. |
| **Alertmanager** | Decides who gets notified and how when Prometheus fires an alert. |
| **Pyrra** | Computes SLOs and error budgets on top of Prometheus. |

> 🗓️ **Metric** = a number measured over time. Example: "requests per second
> = 250". Prometheus stores these.

> 🗓️ **Log** = a text line that something happened. Example: `2026-06-27
> 03:14:22 ERROR payments: connection refused`.

> 🗓️ **Trace** = a record of one request traveling through many services. It
> shows "the user clicked buy → API took 12ms → payments took 800ms → DB took
> 760ms", so you can see *where* time was spent. This is essential in
> microservices where one user action touches many services.

> 🗓️ **SLO (Service Level Objective)** = a target, e.g. "the API must be
> available 99.9% of the time over 30 days". The 0.1% is called the **error
> budget**: you can spend it on risky changes; once it's gone, you freeze
> risky deployments until the budget refills.

### 3.7 The security layer (inside the K8s box)
- **Cilium** — networking built on eBPF (better and more observable than
  classic Kubernetes networking).
- **Tetragon** — runtime security, also eBPF: detects suspicious syscalls live
  (e.g. a web server suddenly spawning a shell).
- **Falco** — rule-based runtime alerting (a longer-standing tool, similar goal
  to Tetragon).
- **Trivy** — scans container images, IaC files, and misconfigurations.
- **Kyverno / OPA Gatekeeper** — admission controllers (policy as code).
- **Cosign / Sigstore** — signs images and verifies signatures (supply chain
> security).

> 🗓️ **eBPF (extended Berkeley Packet Filter)** = a technology that lets you
> run tiny, sandboxed programs *inside the Linux kernel* without modifying the
> kernel or rebooting. It is "hot" because it lets you observe and secure a
> machine with almost no overhead.

> 🗓️ **Admission controller** = a checkpoint inside Kubernetes where, before
> any new object is created, an extra program can say "yes" or "no". This is
> where you enforce rules like "no container can run as root" or "all images
> must be signed".

> 🗓️ **Supply chain security / SLSA** = making sure the software you deploy
> is the software you intended: built from trusted source, by trusted
> builders, and signed so it cannot be tampered with on the way to production.

### 3.8 GitOps delivery
At the very bottom: **ArgoCD + Terraform + GitHub Actions**.

> 🗓️ **ArgoCD** = a GitOps tool that lives inside your cluster and watches a
> Git repo. When the repo changes, ArgoCD applies those changes to the cluster.

> 🗓️ **GitHub Actions** = GitHub's built-in CI/CD system. On every `git push`
> you can run workflows: lint, test, build, scan, push image, update Helm
> values. It's CI (Continuous Integration).

> 🗓️ **CI / CD**:
> - **CI (Continuous Integration)** = every change is automatically built and
>   tested, many times a day.
> - **CD (Continuous Delivery / Deployment)** = every change that passes CI can
>   be released to production, ideally automatically.

---

## 4. Technology stack & justification — explained

This section goes through every tool mentioned in the original doc's tech
table and explains *what it is* and *why we picked it*.

### 4.1 Platform / Orchestration
- **Kubernetes** — the substrate. Everything runs inside it. Mentioned as
  "advanced usage" because writing a custom operator/CRD is a clear senior
  signal (most beginners just deploy a few Pods).
- **Custom Operator (Go + Kubebuilder)** — the safe bridge between the AI's
  proposed remediation and actual cluster changes.

> 🗓️ **Go (Golang)** = a programming language from Google, strongly typed,
> compiled, good at concurrency (goroutines). Most of the Kubernetes ecosystem
> is written in Go, so operators are written in Go too.

> 🗓️ **Kubebuilder** = a framework/scaffolding tool for writing Kubernetes
> operators in Go. It generates the boilerplate (CRD types, controllers,
> manifests) so you focus on your reconcile logic.

- **ArgoCD** — GitOps delivery (industry standard).
- **Terraform / OpenTofu** — IaC for provisioning.

> 🗓️ **IaC (Infrastructure as Code)** = defining your infrastructure (VMs,
> networks, databases) in code files, versioned in Git, instead of clicking
> around in a web console. Reproducible and reviewable.

> 🗓️ **OpenTofu** = the open-source successor of Terraform after Terraform
> changed its license in 2023. Fully compatible syntax.

### 4.2 Networking & Security (DevSecOps)
- **Cilium** — eBPF networking + network policies.
- **Tetragon** — eBPF runtime security.
- **Falco** — runtime rule-based alerting.
- **Trivy** — image, IaC, and misconfig scanning in CI.
- **Kyverno / OPA Gatekeeper** — admission-control policies.
- **Cosign / Sigstore** — sign images and verify supply chain.
- **External Secrets + Vault** — no secrets in git.

> 🗓️ **NetworkPolicy** = a Kubernetes object that restricts which other
> pods/services a pod is allowed to talk to. Without these, every pod can
> talk to every other pod by default.

> 🗓️ **Vault** = HashiCorp's tool for storing secrets (passwords, API keys,
> certificates) securely, with audit logs and short-lived dynamic secrets.

> 🗓️ **External Secrets Operator** = the bridge that pulls secrets from
> Vault (or AWS Secrets Manager, GCP Secret Manager, etc.) and injects them
> into Kubernetes as native `Secret` objects, so apps can use them normally.

### 4.3 Observability
Already explained in Section 3.6.

> 🗓️ **OpenTelemetry (OTel)** = a vendor-neutral standard for producing
> metrics, logs, and traces. You instrument once and can send data to many
> backends (Prometheus, Datadog, Honeycomb, …). It's becoming the default.

> 🗓️ **Pyrra** = a tool that adds SLOs and error budgets to Prometheus.
> You define e.g. "99% of /payments requests must succeed over 28 days", and
> Pyrra computes the burn rate, alerts you if you're consuming the budget
> too fast, and shows you the error budget remaining.

### 4.4 AI / Agents / RAG
- **LangGraph** — multi-agent orchestration as a graph.
- **Ollama** — local models (privacy).
- **OpenAI / Anthropic / Gemini** — cloud models for hard reasoning.
- **LiteLLM** — unified LLM gateway.
- **Qdrant + pgvector** — vector stores (hybrid: one for code/docs, one for
  incidents).
- **LlamaIndex** — ingestion + reranking pipelines.
- **BGE / Cohere reranker** — retrieval quality.

> 🗓️ **LLM (Large Language Model)** = a model trained on huge amounts of
> text that can generate and reason about language. ChatGPT is powered by an
> LLM. "Large" means billions of parameters.

> 🗓️ **BGE-M3** = an open-source embedding model from BAAI, highly capable,
> supports multilingual, multi-granularity, and multi-functions (dense +
> sparse + colbert-style retrieval). We use it locally (via Ollama or
> SentenceTransformers) for privacy.

> 🗓️ **LlamaIndex** = a Python framework for building RAG pipelines:
> loaders, chunkers, embeddings, vector stores, retrievers, rerankers, and
> query engines. Think of it as "Lego for RAG".

> 🗓️ **Hybrid retrieval** = combining **dense** (vector) search ("find
> things with similar meaning") with **sparse** (keyword/BM25) search ("find
> things with matching words"). The two are good at different things, so
> combining them gives better results. BM25 is the classic keyword-search
> algorithm used by Lucene/Elasticsearch.

### 4.5 App / Interface
- **FastAPI** — backend API + WebSocket streaming.
- **Next.js** — chat UI + dashboards.
- **Postgres** — source of truth for incidents, postmortems, agent memory.
- **Redis** — task queue for async agent work.
- **n8n** — optional no-code glue for notifications.

> 🗓️ **PostgreSQL (Postgres)** = a powerful open-source relational database.
> Relational = data is stored in tables with rows and columns, and you query
> it with SQL.

> 🗓️ **Redis** = an in-memory key/value store. Extremely fast. We use it as
> a **task queue**: the API puts a long agent task on the queue, a worker
> picks it up and runs it in the background, so the HTTP request does not
> block.

> 🗓️ **Celery / RQ** = Python libraries for background task queues built on
> top of Redis. (RQ is simpler; Celery has more features.)

> 🗓️ **n8n** = an open-source "no-code" automation tool, similar to Zapier.
> You build little workflows visually (e.g. "when an alert arrives, post to
> Slack"). Optional convenience; not core to the project.

---

## 5. The multi-agent system — explained

The original doc stresses: do **not** build one giant prompt. Build a **state
machine** where specialized agents collaborate, with a human-in-the-loop gate.

### 5.1 Why specialized agents instead of one big agent
If you give one LLM everything to do, it gets confused, forgets context, and
makes mistakes. Splitting into specialized roles is how real teams work, and it
maps cleanly to LangGraph: each agent is a node, each handoff is an edge.

### 5.2 The agents (recap with extra context)
1. **Triage Agent** — turns a raw alert into a structured incident with labels,
   severity, and assignment. Ruthlessly quick, not deep.
2. **SRE Agent** — does the deep technical investigation using
   `kubectl`/PromQL/LogQL. Hypothesizes root cause, supports with evidence.
3. **Security Agent** — decides if security-relevant; cross-checks runtime
   events and CVE databases.
4. **RAG Agent** — searches the knowledge base and returns ranked evidence
   **with citations** (file path + line). Citing sources is what makes the
   system trustworthy.
5. **Cost Agent** — flags wasted money. (You'd be surprised how much a
   forgotten idle node costs.)
6. **Code Review Agent** — runs on pull requests, looks at Helm/Terraform/
   Docker, comments inline, and (with policy) blocks merge.
7. **Executor Agent** — the *only* agent that can act. Always dry-runs first.
   Always obeys an allow-list. Always logs.
8. **Postmortem Agent** — produces the writeup, extracts lessons, and writes
   them **back into the vector DB**, closing the loop.

### 5.3 The loop (why it's smart)
```
alert → triage → [SRE + Security + RAG in parallel] → synthesis →
   plan → human approval → executor heals → postmortem → embed in KB →
   next time the same alert fires, recall is instant.
```
The bolded part is the **learning loop**. The KB grows itself.

### 5.4 Safety
- **Allow-list of actions**: scale deployment, restart pod, rollback, cordon
  node, block IP. Nothing else. The executor literally cannot do anything
  outside the list.

> 🗓️ **Cordon a node** = tell Kubernetes "do not schedule any new pods on
> this machine" (used when a machine is misbehaving but you don't want to
> kick existing pods yet).

> 🗓️ **Rollback** = revert a deployment to its previous good version
> (Kubernetes keeps recent ReplicaSets specifically for this).

- **Dry-run first**: executor shows the *exact* command it would run.
- **Audit log**: every thought + action stored in Postgres, visible in the UI.
- **RBAC**: a dedicated ServiceAccount with least privilege.

> 🗓️ **RBAC (Role-Based Access Control)** = granting permissions to roles
> (not individuals). In Kubernetes this means a `ServiceAccount` (a non-human
> identity) bound to a `Role`/`ClusterRole` that says exactly which resources
> it can touch on which namespaces.

> 🗓️ **ServiceAccount** = a Kubernetes identity used by pods (not by humans)
> so they can be granted RBAC permissions. The operator pod runs under a
> dedicated, least-privileged ServiceAccount.

---

## 6. The RAG knowledge base — explained

> 🗓️ **RAG (Retrieval-Augmented Generation)** = a technique where, before
> the LLM answers a question, you retrieve relevant chunks from a knowledge
> base and feed them into the prompt. The model then answers based on those
> chunks. This reduces hallucinations and lets the model use private,
> up-to-date, organization-specific data.

### 6.1 The naive version vs the production version
**Naive RAG** (what a beginner builds) = upload a PDF, split into chunks, run
embedding search, paste top hits into the prompt. Works for demos, fails in
production (chunking is bad, no reranking, no citations, no evaluation).

**Production RAG** (what we are building) =
1. **Source-specific chunking** (prose documents split by paragraphs; code
   split by AST-aware boundaries so chunks never cut a function in half).
2. **Per-source metadata** (file path, line range, source type, doc id).
3. **Embeddings** with BGE-M3 locally (privacy) or OpenAI embeddings (quality).
4. **Hybrid storage**: dense + sparse in Qdrant.
5. **Hybrid retrieval + cross-encoder reranking**.
6. **Citations**: every answer links back to source file + line.
7. **Evaluation**: a small "golden set" of Q&A, with metrics recall@k and
   faithfulness, run in CI. If retrieval quality drops, CI fails.

> 🗓️ **Chunking** = splitting a large document into smaller pieces so each
> piece fits in the LLM's context window and so search is more precise (you
> retrieve the relevant *piece*, not the whole 200-page manual).

> 🗓️ **AST (Abstract Syntax Tree)** = a tree representation of code that
> captures its structure (functions, classes, blocks). Chunking by AST means
> we never cut a function in the middle of its body — much smarter than
> chunking by fixed character count.

> 🗓️ **Recall@k** = a retrieval quality metric: "of all the relevant
> documents, what fraction appears in the top-k returned?" Higher = better.

> 🗓️ **Faithfulness** = an answer quality metric: "is every claim in the
> answer actually supported by the retrieved evidence?" Detects
> hallucinations.

### 6.2 Sources ingested
- Runbooks (Markdown in git)
- Past incidents & postmortems (Postgres)
- Codebase (chunked by function/file, AST-aware)
- Helm charts, Terraform modules, Dockerfiles
- Live Kubernetes manifests (snapshotted)
- Chat history (agent memory)
- Security policies (Kyverno rules, CIS benchmarks)
- Cached vendor docs for the tools we use

> 🗓️ **CIS Benchmarks** = a set of widely-recognized, best-practice security
> configuration guidelines published by the Center for Internet Security
> (e.g. "do not allow anonymous kubelet access").

### 6.3 The killer feature
The KB is **self-updating**: every postmortem an agent writes is auto-embedded
immediately. So Sentinel literally learns from its own resolved incidents.
Demo: trigger the same incident twice — second time it resolves 10× faster
because the KB already has the answer.

---

## 7. The custom Kubernetes Operator — explained

We write this in **Go with Kubebuilder**. We define a CRD `RemediationPlan`:

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

> 🗓️ **OOMKilled** = "Out Of Memory Killed". Kubernetes kills a container
> when it tries to use more memory than its configured `limit`. This is one of
> the most common causes of pod restarts.

### The reconciliation flow in plain words
1. The Executor Agent creates a `RemediationPlan` object (state = `Proposed`).
2. A human (or auto-approve in sandbox mode) flips the state to `Approved`.
3. Our operator's controller notices the new `Approved` object (this is its
   reconcile loop ticking over).
4. The controller executes the proposed `kubectl` command.
5. It watches the relevant metrics to **verify** the fix worked → state
   `Verified`.
6. Eventually the incident is closed → state `Closed`.

> 🗓️ **Desired vs actual state** = Kubernetes is built around this pair.
> Desired = what you wrote (e.g. "3 replicas"). Actual = what really exists
> (e.g. "2 running, 1 starting"). The loop drives actual toward desired.

This single component proves you understand **controllers, reconciliation
loops, RBAC, and CRD design** — strong senior signal.

---

## 8. CI/CD & GitOps flow — explained

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

> 🗓️ **Registry (container registry)** = a server that stores container
> images, like Docker Hub, GitHub Container Registry, quay.io. CI pushes
> finished images here; the cluster pulls them from here.

> 🗓️ **Drift** = when the actual state of the cluster differs from what is
> declared in Git (someone manually edited something, or a deploy failed
> halfway). GitOps tools auto-correct drift back to Git.

> 🗓️ **Canary rollout** = releasing a new version to a small fraction of
> users first (e.g. 5%), watching metrics, then increasing the percentage.
> If metrics go bad, you abort before everyone is affected. **Argo Rollouts**
> is the tool we use for this on Kubernetes.

> 🗓️ **Auto-rollback** = automatically reverting to the previous good
> version when the new one fails an SLO or health check. No human needed.

Everything is **declarative and reproducible**: `git` is the source of truth,
not `kubectl edit` typed at 3am.

> 🗓️ **Declarative** = you describe *what* you want ("3 healthy replicas"),
> not *how* to achieve it (the system figures out the steps). Kubernetes is
> declarative. Shell scripts are usually imperative ("do this, then that").

---

## 9. Concrete demos (the "wow" list)

Pick 3–4 of these to demo in a 5-minute video:

1. **"Ask Sentinel" chat** — "Why did payments latency spike last night?" →
   correlates Prometheus + Loki + traces, cites the relevant runbook, points
   to the offending query. *(RAG + multi-source + agents)*
2. **Self-healing** — trigger an OOM or crashed pod → Sentinel detects, writes
   a `RemediationPlan`, with approval applies, verifies, writes postmortem.
   *(Operator + agents + observability)*
3. **Security autopilot** — Tetragon detects a reverse shell → Security Agent
   confirms, cordons node, opens a ticket with full evidence chain. *(eBPF +
   security agent + execution)*

> 🗓️ **Reverse shell** = a hacking technique where a compromised process
> opens a shell that connects *out* to the attacker, instead of the attacker
> connecting *in* (which would be blocked by firewalls). Tetragon can detect
> a web server suddenly spawning a shell — a tell-tale sign.

4. **Cost cop** — Cost Agent flags an over-provisioned node, suggests
   right-sizing, generates the exact PR to change Terraform. *(Cost agent +
   IaC)*
5. **Code review on PRs** — PR opened → Code Review Agent reviews the Helm
   chart, comments inline, blocks merge if it violates policy. *(CI + agents)*
6. **The learning loop demo** — fire the same incident twice, show the second
   resolution is near-instant because the KB already has the answer.

---

## 10. Roadmap — explained

### Phase 0 — Foundations (week 1–2)
- Provision cluster (`kind`/`k3s` locally, then a real cloud K8s).
- ArgoCD + Terraform skeleton + GitHub Actions CI.
- Observability stack: Prometheus, Loki, Grafana, OTel.
- Deliverable: a git push deploys an app with full metrics/logs.

> 🗓️ **kind** = "Kubernetes IN Docker" — runs a whole K8s cluster inside
> Docker containers on your laptop, in seconds. Great for dev and tests.

> 🗓️ **k3s** = a lightweight, certified Kubernetes distribution from Rancher
> (single binary, half the memory). Good for homelabs and edge devices.
> **k3d** = "k3s in Docker" — like kind but using k3s.

### Phase 1 — RAG core (week 3–5)
- Qdrant + pgvector; ingestion pipelines for docs + code.
- FastAPI `/ask` endpoint; simple retrieval + rerank.
- Eval set + CI check on retrieval quality.
- Deliverable: "ask your codebase" works with citations.

### Phase 2 — Single agent SRE (week 6–8)
- LiteLLM gateway + Ollama local model.
- One agent that can run safe `kubectl` tools, answer on live state.
- Chat UI (Next.js + WebSocket streaming).
- Deliverable: chat that can inspect the live cluster.

### Phase 3 — Multi-agent + operator (week 9–12)
- LangGraph orchestrator; add Security + Cost + Code-review agents.
- Build the Kubebuilder operator + CRDs.
- Human-in-the-loop approval flow.
- Deliverable: self-healing demo works end to end.

### Phase 4 — Security hardening (week 13–15)
- Cilium + Tetragon + Falco + Kyverno + Trivy + Cosign.
- Wire Security Agent to runtime events.
- Deliverable: security autopilot demo.

### Phase 5 — Polish, evals, portfolio (week 16–18)
- Postmortem agent + auto KB update (close the loop).
- SLOs with Pyrra, dashboards, on-call simulation.
- README, architecture diagrams, 5-min demo video, blog posts.
- Deliverable: portfolio-ready.

### Per-phase advice
Each phase is a valuable, deployable artifact on its own. You can start
interviewing after Phase 2 and keep building in parallel.

---

## 11. Portfolio strategy — explained

> 🗓️ **Portfolio** = a collection of projects and writing that demonstrates
> your skills to employers, usually linked from your resume and LinkedIn.

The original doc recommends:
- **Monorepo** with clear folders (`/operator`, `/agents`, `/rag`, `/infra`,
  `/gitops`, `/frontend`). Clean README with architecture.

> 🗓️ **Monorepo** = a single Git repository that contains multiple
> sub-projects, instead of many separate repos. Easier cross-cutting changes,
> one CI config, one issue tracker.

- **Architecture diagram** in the README (the ASCII one + a nicer one in
  Mermaid or Excalidraw).
- **5-minute demo video** walking through the 3 best features.
- **3 blog posts** (operator, RAG, eBPF). Post on dev.to / LinkedIn.
- **Live demo** (optional): read-only instance on a small cloud node.
- **Evals dashboard**: show retrieval quality + agent success-rate metrics.
> This screams "I build serious AI, not prompt hacks."

### Skills matrix this project proves
- Kubernetes (advanced) · Operator pattern · GitOps · IaC
- DevSecOps end-to-end · Supply chain security · eBPF
- Observability · SRE practices · SLOs
- LLM app architecture · Multi-agent systems · Production RAG · LLMOps/evals
- Go · Python · TypeScript · system design

That combination maps closely to the bullet points in Senior Platform /
AI Infra / DevSecOps / SRE job descriptions in 2026.

---

## 12. Stretch goals — explained

- **Multi-cluster** — manage several clusters from one Sentinel control plane.

> 🗓️ **Control plane** = the part of a system that decides what should
> happen; the **data plane** is what actually carries the work. In
> Kubernetes, the control plane is the API server + scheduler + controller
> manager + etcd.

- **MCP (Model Context Protocol)** server exposing Sentinel as tools to any
> agent/IDE. Very current in 2025–2026.

> 🗓️ **MCP** = an open standard (introduced by Anthropic in late 2024) that
> lets AI assistants (Claude, Copilot, custom agents) connect to external
> tools/data sources in a uniform way. By exposing Sentinel as an MCP server,
> any MCP-compatible IDE could call Sentinel's tools.

- **Fine-tuned small model (LoRA)** on your incident history for cheap local
> reasoning.

> 🗓️ **LoRA (Low-Rank Adaptation)** = a parameter-efficient fine-tuning
> technique that trains only small "adapter" weights (~1% of the model)
> instead of all parameters, making fine-tuning affordable on a single GPU.

- **Policy-as-code generation** — Security Agent writes Kyverno/Rego rules for
> gaps it discovers.

> 🗓️ **Rego** = the policy language used by **OPA** (Open Policy Agent).
> Kyverno uses its own YAML-based policy language; OPA uses Rego. Both are
> "policy as code".

- **Chaos engineering hook** — Sentinel + Litmus/Chaos Mesh runs game-days
> and learns from failures.

> 🗓️ **Chaos engineering** = deliberately breaking things in production
> (in a controlled way) to find weaknesses before a real incident does.
> Netflix's Chaos Monkey popularized it. **Chaos Mesh** and **Litmus** are
> the open-source chaos tools for Kubernetes.

> 🗓️ **Game day** = a scheduled exercise where you simulate incidents to
> practice response and find weak spots, *without* a real incident forcing
> it.

- **Voice mode** for on-call: talk to Sentinel over phone at 3am.

---

## 13. Getting-started checklist — with notes

- [ ] Pick local cluster: `kind` or `k3d` for dev, a small cloud node for "prod".
- [ ] Create the monorepo; set up GitHub Actions skeleton.
- [ ] Stand up observability stack (Phase 0) — get **one** app fully
  instrumented (metrics + logs + traces).
- [ ] Stand up Qdrant + LlamaIndex ingestion of your own repos/docs.
- [ ] Get Ollama + LiteLLM running locally.
- [ ] Build the first `/ask` endpoint with citations.
- [ ] Read the Kubebuilder docs; scaffold the operator + first CRD.
- [ ] Draft the agent architecture in `docs/agents.md` *before* coding
  LangGraph.

> Start narrow: a single agent that can answer "what's wrong with my cluster
> right now" using RAG over your runbooks. Expand outward. Do **not** try to
> build all 8 agents first — the orchestration will collapse under its own
> weight.

---

## 14. Adapt to your level

- **Junior / early-career**: stop at Phase 2–3 (RAG + one agent + GitOps +
  observability). That alone beats 95% of portfolios.
- **Mid-level**: add the operator + multi-agent + security stack (Phases 3–4).
- **Senior target**: complete the loop, add evals, multi-cluster, MCP, and the
  fine-tuned model (the stretches).

Each phase is a valuable, deployable artifact on its own. You can start
interviewing after Phase 2 and keep building in parallel.

---

## 15. A short suggested reading order (if you're new to all of this)

If several of these concepts are new, learn them in this order — it matches how
they built on top of each other:

1. **Containers & Docker** — how a single app is packaged and run anywhere.
2. **Kubernetes basics** — Pods, Deployments, Services, how containers are
   orchestrated.
3. **Kubernetes networking & RBAC** — how Pods talk, how access is controlled.
4. **GitOps with ArgoCD** — how `git` drives the cluster.
5. **Observability fundamentals** — metrics, logs, traces, and what each is
   good for.
6. **Relational databases & SQL** (Postgres) — the source of truth side.
7. **Vector databases & embeddings** — the semantic-search side.
8. **RAG basics** — retrieve → rerank → answer with citations.
9. **LLMs & tool-use / agents** — single agent, multiple tools.
10. **Multi-agent orchestration (LangGraph)** — many agents as a graph.
11. **CRDs and the operator pattern (Kubebuilder)** — extending Kubernetes.
12. **Policy & supply chain security** — Kyverno, Cosign, Trivy.
13. **eBPF security** — Cilium, Tetragon, Falco.
14. **LLMOps & evals** — measuring quality, not vibes.

Don't try to memorize everything. As you build each phase, the relevant terms
will become muscle memory.

---

## 16. Glossary (alphabetical quick reference)

- **Admission controller** — a K8s checkpoint that can approve/reject new
  objects based on policy (Kyverno, OPA Gatekeeper).
- **Agent** — an LLM that can call tools to take actions toward a goal.
- **Alertmanager** — the alert routing/notification component of Prometheus.
- **API** — a defined way for programs to talk to each other.
- **ArgoCD** — GitOps tool that syncs a Git repo to a cluster.
- **Argo Rollouts** — progressive delivery (canary, blue/green) for K8s.
- **AST** — Abstract Syntax Tree; structured representation of code.
- **BGE-M3** — open-source embedding model (multilingual, multi-function).
- **BM25** — classic keyword-based scoring for sparse search.
- **Canary rollout** — release to a small slice first, monitor, then expand.
- **Celery / RQ** — Python task queue libraries (backed by Redis).
- **Chunking** — splitting documents into small, retrievable pieces.
- **CIS Benchmarks** — security best-practice configuration guidelines.
- **CI / CD** — Continuous Integration / Continuous Delivery or Deployment.
- **Cilium** — eBPF-based K8s networking + network policies.
- **Container** — a portable package of an app + its dependencies.
- **Control plane** — the part of a system that makes decisions (vs data plane).
- **Cosign / Sigstore** — image signing for supply chain security.
- **Cordon** — mark a node as not-scheduling new pods.
- **CRD** — Custom Resource Definition; a new object type you add to K8s.
- **CVE** — a numbered, publicly-listed software vulnerability.
- **DevOps** — merging Dev and Ops responsibilities and tooling.
- **DevSecOps** — adding security into every stage of DevOps.
- **Drift** — actual state diverging from the declared state.
- **eBPF** — run tiny sandboxed programs inside the Linux kernel.
- **Embedding** — turning text into a vector for similarity search.
- **Error budget** — the tiny fraction of time an SLO allows to be "bad".
- **External Secrets** — operator that syncs secrets from Vault to K8s Secret.
- **Falco** — runtime rule-based security alerting.
- **FastAPI** — fast Python web framework for building APIs.
- **Faithfulness** — answer-only-contains-claims-supported-by-evidence metric.
- **Game day** — scheduled incident rehearsal exercise.
- **gh** — GitHub's command-line tool.
- **GitOps** — using a Git repo as the source of truth for system state.
- **Go / Golang** — compiled language used for most K8s ecosystem code.
- **Grafana** — dashboards/visualization tool.
- **Helm** — package manager for K8s; charts are its packages.
- **Hybrid retrieval** — combining dense (vector) + sparse (keyword) search.
- **IaC** — Infrastructure as Code.
- **kind** — Kubernetes IN Docker, for local dev clusters.
- **k3s / k3d** — lightweight / dockerized Kubernetes variants.
- **Kubebuilder** — framework for writing K8s operators in Go.
- **Kubernetes (K8s)** — orchestrator for containers across machines.
- **Kyverno** — policy-as-code admission controller for K8s.
- **LangGraph** — library to build agents as graphs/state machines.
- **LiteLLM** — unified gateway in front of many LLM providers.
- **LLM** — Large Language Model.
- **LLMOps** — the "DevOps" of LLM apps: models, prompts, evals, cost.
- **LoRA** — parameter-efficient fine-tuning technique.
- **Log** — text record of what an app did, timestamped.
- **LogQL** — Loki's query language.
- **Loki** — log aggregation system, Grafana-aligned.
- **LlamaIndex** — Python RAG framework.
- **MCP** — Model Context Protocol; standard for supplying tools to AI.
- **Metric** — a numeric value measured over time.
- **Monorepo** — one Git repo containing multiple sub-projects.
- **n8n** — open-source no-code automation tool (like Zapier).
- **Next.js** — React framework for production web apps.
- **Node (K8s)** — a machine in the cluster that runs pods.
- **OOMKilled** — container killed for exceeding its memory limit.
- **Ollama** — run LLMs locally on your own machine.
- **OPA / Rego** — Open Policy Agent and its Rego policy language.
- **OpenTofu** — open-source successor of Terraform.
- **Operator pattern** — custom controller + CRD that encodes operational
  know-how.
- **OTel (OpenTelemetry)** — vendor-neutral standard for metrics/logs/traces.
- **Postmortem** — blameless writeup after an incident.
- **Postgres (PostgreSQL)** — powerful open-source relational database.
- **Prometheus** — metrics database + query engine.
- **PromQL** — Prometheus query language.
- **PR (Pull Request)** — proposed change in a Git repo awaiting review.
- **Pyrra** — SLO/error-budget tool on top of Prometheus.
- **Qdrant** — open-source vector database.
- **RAG** — Retrieval-Augmented Generation.
- **RBAC** — Role-Based Access Control.
- **Recall@k** — fraction of relevant items found in top-k results.
- **Reconciliation loop** — controller repeatedly driving actual → desired.
- **Rego** — OPA's policy language.
- **Redis** — in-memory key/value store; here used as a job queue.
- **Reranker (cross-encoder)** — re-orders top candidates by query-doc relevance.
- **Reverse shell** — compromised process opens a shell to the attacker.
- **Rollback** — revert a deployment to its previous version.
- **Runbook** — step-by-step guide for handling a known problem.
- **ServiceAccount** — a K8s identity used by pods (non-human).
- **SLO** — Service Level Objective (e.g. 99.9% availability).
- **SLSA** — supply-chain security level framework.
- **State machine** — model using states and allowed transitions.
- **Tempo** — distributed tracing backend from Grafana Labs.
- **Terraform** — IaC tool to provision cloud resources.
- **Tetragon** — eBPF-based runtime security tool.
- **Trace** — record of one request flowing across multiple services.
- **Trivy** — scanner for images, IaC, and misconfigurations.
- **Triage** — prioritizing incoming problems by urgency.
- **Vector database** — DB optimized for similarity search over embeddings.
- **WebSocket** — protocol for live two-way connection between browser and
  server.