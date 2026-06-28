# GitOps layout for Sentinel

This directory is the **source of truth for the cluster**. ArgoCD watches it
(via the root Application in `argocd/apps/root.yaml`) and continuously drives
the cluster to match what is committed here.

```
gitops/
├── argocd/              ArgoCD itself + the App-of-Apps bootstrap
│   └── apps/            One Application manifest per deployed thing
│       └── root.yaml    The root Application (watches this folder)
├── base/                Shared Kustomize bases (namespace + common labels)
│   ├── namespaces/      Cluster-wide Namespace definitions
│   └── labels/          Reusable Component injecting app.kubernetes.io labels
├── components/          Cluster-wide infrastructure (Helm releases)
│   ├── argocd/          ArgoCD Helm values
│   ├── ingress-nginx/   ingress-nginx Helm values
│   ├── observability/   (later) Prometheus/Grafana/Loki/Tempo values
│   ├── qdrant/          (later) Qdrant vector DB Helm values
│   └── ...
└── projects/            The actual apps we ship
    ├── demo-api/        (later) FastAPI demo app + Helm chart
    ├── frontend/        (later) Next.js chat UI
    └── operator/        (later) Sentinel K8s operator
```

## Convention

### `components/` vs `projects/`

- **`components/`** = cluster-wide infrastructure that doesn't belong to one
  app. Examples: ingress controller, observability stack, cert-manager,
  Qdrant. These are usually whole Helm charts.
- **`projects/`** = the things Sentinel actually *produces* and ships. These
  are *our* apps. Examples: the FastAPI backend, the Next.js frontend, the
  Go operator.

If you would be sad to lose it (because it's our code, not a vendor's), it
belongs in `projects/`. If installing it from upstream is reproducible from
a single `helm install`, it goes in `components/`.

### Per-app layout

Every app — whether in `components/` or `projects/` — should have:

```
<app>/
├── Chart.yaml          Helm chart metadata (or kustomization.yaml if Kustomize)
├── values.yaml         Default values (dev/local)
├── values-prod.yaml    (optional) production overrides
└── templates/          Helm templates (if using Helm)
```

And it gets one ArgoCD Application manifest in `argocd/apps/<app>.yaml` that
points ArgoCD at its folder in this repo.

### Why namespaces are centralised

`base/namespaces/namespaces.yaml` defines every namespace Sentinel uses.
Each project's `kustomization.yaml` references this via `resources:` so we
have **one** place to add/remove namespaces and one place to enforce
Pod Security Standards labels. Never create a Namespace inside a project
template — always edit `base/namespaces/namespaces.yaml` instead.

### Why a shared labels Component

`base/labels/labels.yaml` is a Kustomize **Component** (not a resource).
Pulling it in via:

```yaml
# in some project's kustomization.yaml
components:
  - ../../base/labels
```

…injects `app.kubernetes.io/part-of: sentinel` (and a couple of others)
onto every resource the project renders. This means you can always do:

```bash
kubectl get all -A -l app.kubernetes.io/part-of=sentinel
```

…to discover every Sentinel-managed object in the cluster, regardless of
namespace.

## Adding a new app

1. Create `gitops/projects/<new-app>/` with your chart or manifests.
2. Create `gitops/argocd/apps/<new-app>.yaml` — an ArgoCD Application
   pointing at your new folder with `syncPolicy.automated: { prune: true,
   selfHeal: true }`.
3. `git commit && git push`.
4. The root Application in `argocd/apps/root.yaml` picks up the new file
   within ~30 seconds (ArgoCD's default repo poll interval) and starts
   syncing your app automatically. No `kubectl apply` needed.

## Removing an app

1. Delete `gitops/argocd/apps/<app>.yaml`.
2. Optionally delete `gitops/projects/<app>/`.
3. `git push`.
4. The root Application prunes the deleted Application (because
   `prune: true`); ArgoCD then prunes the child resources.

`selfHeal: true` ensures that even if someone manually `kubectl edit`s a
resource, ArgoCD reverts it back to what Git says within seconds.
