# Tetragon — eBPF runtime security (T4.2)

Deploys **Tetragon 1.7.0** (Cilium's eBPF runtime security) with a starter
`suspicious-exec` TracingPolicy, and wires the event stream to the Security
Agent's `tetragon_events` tool.

## What it is

Tetragon captures raw kernel events (process exec, network, file access)
with full pod/namespace context. Sentinel only **observes** — no in-kernel
enforcement.

## Install

```bash
./scripts/install-tetragon.sh
```

This:
1. Installs the wrapper chart (agent DaemonSet + operator + CRDs).
2. Applies `policies/suspicious-exec.yaml` (logs `bash`/`sh`/`dash`/`ash`
   execs — the classic "shell in a pod" signal).
3. Verifies the event stream.

Like Cilium, Tetragon is **bootstrap infrastructure** — manual install, NOT
an ArgoCD Application.

## How the Security Agent reads events

The `tetragon_events` tool (agents/sentinel_agents/tools/tetragon_events.py)
has two live sources:

1. An optional HTTP bridge (`TETRAGON_URL`, default
   `http://tetragon.kube-system.svc:8081/events`) — for deployments that
   expose one.
2. The **cluster-wide kubectl stream** (default wiring, no extra components):
   the agent writes NDJSON to `/var/run/cilium/tetragon/tetragon.log` and
   the `hubble-export-stdout` sidecar tails it to the pod's stdout.  The
   tool enumerates the agent pods and reads each one's `export-stdout`
   logs, then filters + summarises (exec events sorted with shells first).

```text
trigger:  kubectl exec -n sentinel deploy/test-api -- sh -c "whoami"
   ↓
Tetragon agent (eBPF kprobe/tracepoint) → NDJSON export log → sidecar stdout
   ↓
tetragon_events tool → kubectl logs <tetragon-pod> -c export-stdout
   ↓
Security Agent: "exec ns=sentinel pod=test-api binary=/usr/bin/sh args=-c whoami"
```

## Troubleshooting

- **No events?** Trigger one and watch a specific node's stream:
  `kubectl logs -n kube-system <tetragon-pod> -c export-stdout --tail=10`.
  (Note: `kubectl logs ds/tetragon --tail/--since` aggregation is unreliable
  on kind + containerd — query per-pod.)
- **Policy selectors not firing (kernel 7.0.x)?** Tetragon 1.7.0's
  `matchArgs` string extraction returns empty on newer kernels and
  `matchBinaries` never matches — a known compatibility gap.  The base
  `process_exec` tracing (exported for EVERY exec) covers the acceptance:
  the tool filters the stream client-side for shell binaries.  See the
  policy file for details.
- **Event visible but tool says none?** The tool tries the HTTP bridge
  first; if `TETRAGON_URL` is unreachable it falls back to kubectl
  automatically (error-JSON from the HTTP layer is detected, not
  summarised as an event).
