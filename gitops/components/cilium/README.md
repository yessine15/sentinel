# Cilium — eBPF CNI (T4.1)

Replaces the default kind CNI (**kindnet**) with **Cilium 1.20.0**:
eBPF-based networking, network policies (used by T4.2+), and Hubble
observability (flow map UI).

## ⚠️ Why NOT an ArgoCD Application

Cilium is **bootstrap infrastructure** (like `observability/` and
`ingress-nginx/`): it is installed manually with `helm install`, NOT via an
ArgoCD Application. The CNI must never be deleted, reverted, or drifted by a
GitOps sync — if ArgoCD ever rolled Cilium back, the cluster would lose all
pod networking. The chart wrapper lives here for reproducibility and
documentation; the cluster is the source of truth for the running version.

## Install / upgrade

```bash
cd gitops/components/cilium
helm dependency update          # fetch the cilium/cilium subchart
helm upgrade --install cilium . -n kube-system
cilium status --wait            # all components OK
```

### Uninstall (only if you must)

```bash
helm uninstall cilium -n kube-system   # reinstall kindnet afterwards!
```

## values.yaml — what each block does

| Block | Setting | Why |
|---|---|---|
| `cluster.name/id` | `sentinel` / `1` | Cluster identity used for Cilium security identities |
| `kubeProxyReplacement` | `false` | Keep kube-proxy (least-risk migration); Services still work exactly as before |
| `operator.replicas` | `1` | Single-replica operator for the local cluster |
| `ipam.mode` | `kubernetes` | Reuse the pod CIDRs Kubernetes already assigned (10.244.0.0/16) |
| `routingMode` | `native` | Direct routing (kind nodes share one L2) |
| `autoDirectNodeRoutes` | `true` | Route pod CIDRs directly via node routes |
| `ipv4NativeRoutingCIDR` | `10.244.0.0/16` | **Required** with native routing + k8s IPAM (agent fatal-errors without it) |
| `hubble.*` | enabled + relay + UI | Flow capture, relay, and the web UI (port-forward below) |

## Post-install steps

1. **Remove kindnet and restart everything onto Cilium:**

   ```bash
   kubectl -n kube-system delete ds kindnet
   kubectl delete pods -A --all        # recreate all pods with Cilium IPs
   ```

   > After a CNI swap, existing pods keep their old sandbox until recreated;
   > restarting everything picks up Cilium cleanly.

2. **Hubble UI (flow map):**

   ```bash
   kubectl port-forward -n kube-system svc/hubble-ui 12000:80
   # open http://localhost:12000 → pick a namespace → Visual tab
   ```

3. **Verify:**

   ```bash
   cilium status                      # Cilium/Operator/Envoy/Hubble Relay: OK
   cilium connectivity test --timeout 15m   # full pod/node/service matrix
   ```

## Known gotchas found during T4.1

1. **`native routing cidr must be configured`** — the agent fatal-errors at
   startup when `routingMode: native` + `ipam: kubernetes` without
   `ipv4NativeRoutingCIDR`. Fix: set it to the cluster pod CIDR
   (kind default `10.244.0.0/16`).
2. **`too many open files` (inotify)** — on a long-lived kind cluster the
   agent crashed with `couldn't initialize inotify: too many open files`
   because the nodes had `fs.inotify.max_user_instances=128` (default).
   Fix (per node, persists until the node restarts):

   ```bash
   docker exec <node> sysctl -w fs.inotify.max_user_instances=1024 \
                             fs.inotify.max_user_watches=524288
   ```

   For a permanent fix on kind, add the sysctls to
   `infra/kind-cluster.yaml` (or recreate the cluster).
3. **kubeProxyReplacement value** — Cilium ≥1.20 requires a boolean
   (`true`/`false`), not the old `disabled` string.
4. **Pod restarts after the CNI swap** — images that exist only in the kind
   node cache (e.g. the local `sentinel-frontend` image, a private GHCR repo
   → `403 Forbidden` on re-pull) must be re-loaded with
   `kind load docker-image` before recreating pods.
