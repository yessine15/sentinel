#!/usr/bin/env bash
# =============================================================
# install-argocd.sh — Install ArgoCD on the Sentinel kind cluster
#                     and bootstrap App-of-Apps GitOps
#
# Usage:
#   ./scripts/install-argocd.sh
#
# What it does:
#   1. Adds ArgoCD Helm repo
#   2. Installs ArgoCD with values from gitops/components/argocd/values.yaml
#   3. Waits for all ArgoCD components to be Ready
#   4. Prints the initial admin password
#   5. Applies the bootstrap root Application (App-of-Apps)
#   6. Verifies http://argocd.local reaches the UI
# =============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VALUES_FILE="$ROOT_DIR/gitops/components/argocd/values.yaml"
ROOT_APP_FILE="$ROOT_DIR/gitops/argocd/apps/root.yaml"
RELEASE="argocd"
NAMESPACE="argocd"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[$(date +%H:%M:%S)]${NC} $*"; }
warn() { echo -e "${YELLOW}[$(date +%H:%M:%S)] WARN:${NC} $*"; }
err()  { echo -e "${RED}[$(date +%H:%M:%S)] ERROR:${NC} $*" >&2; }
info() { echo -e "${BLUE}[$(date +%H:%M:%S)]${NC} $*"; }

# --- preflight ---
preflight() {
    for cmd in helm kubectl curl; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            err "Required command not found: $cmd"
            exit 1
        fi
    done

    if ! kubectl config current-context 2>/dev/null | grep -q "kind-sentinel"; then
        err "Not on kind-sentinel context."
        err "Run: kubectl config use-context kind-sentinel  (or ./scripts/kind-up.sh)"
        exit 1
    fi

    [[ -f "$VALUES_FILE" ]] || { err "Missing $VALUES_FILE"; exit 1; }
    [[ -f "$ROOT_APP_FILE" ]] || { err "Missing $ROOT_APP_FILE"; exit 1; }
}

# --- install ArgoCD ---
install_argocd() {
    if helm status "$RELEASE" -n "$NAMESPACE" >/dev/null 2>&1; then
        warn "Release '$RELEASE' already installed. Upgrading ..."
        helm upgrade "$RELEASE" argo/argo-cd \
            --namespace "$NAMESPACE" \
            -f "$VALUES_FILE"
    else
        log "Adding ArgoCD Helm repo ..."
        helm repo add argo https://argoproj.github.io/argo-helm
        helm repo update

        log "Installing ArgoCD ..."
        helm install "$RELEASE" argo/argo-cd \
            --namespace "$NAMESPACE" \
            --create-namespace \
            -f "$VALUES_FILE"
    fi

    log "Waiting for ArgoCD server pod to be Ready ..."
    kubectl wait --namespace "$NAMESPACE" \
        --for=condition=ready pod \
        --selector=app.kubernetes.io/name=argocd-server \
        --timeout=300s

    log "ArgoCD components:"
    kubectl get pods -n "$NAMESPACE" 2>&1 | cat
}

# --- print admin password ---
show_password() {
    log "Fetching initial ArgoCD admin password ..."
    if kubectl -n "$NAMESPACE" get secret argocd-initial-admin-secret >/dev/null 2>&1; then
        local pass
        pass=$(kubectl -n "$NAMESPACE" get secret argocd-initial-admin-secret \
            -o jsonpath="{.data.password}" | base64 -d)
        echo ""
        echo -e "  ${BLUE}ArgoCD UI:${NC}        http://argocd.local"
        echo -e "  ${BLUE}Username:${NC}        admin"
        echo -e "  ${BLUE}Password:${NC}        ${pass}"
        echo ""
        warn "(The argocd-initial-admin-secret is deleted once you change the password.)"
    else
        warn "argocd-initial-admin-secret not found. ArgoCD may still be starting."
    fi
}

# --- apply root Application (bootstrap App-of-Apps) ---
apply_root_app() {
    log "Applying bootstrap root Application (App-of-Apps) ..."

    # CRDs are installed by the Helm chart; ensure the ArgoCD CRD is present.
    if ! kubectl get crd applications.argoproj.io >/dev/null 2>&1; then
        err "Application CRD not found — ArgoCD install may have failed."
        exit 1
    fi

    # Apply the root Application. It points at gitops/argocd/apps/ in the repo.
    # ArgoCD will then create one child Application for each YAML there.
    kubectl apply -f "$ROOT_APP_FILE" 2>&1 | cat

    log "Waiting for ArgoCD to sync the root Application ..."
    sleep 5

    kubectl get applications -n "$NAMESPACE" 2>&1 | cat || true
}

# --- verify ---
verify() {
    log "Verifying ArgoCD UI at http://argocd.local ..."
    if code=$(curl -sS -o /dev/null -w "%{http_code}" http://argocd.local 2>/dev/null); then
        # ArgoCD UI may return 200, 302, or 401 — all fine.
        if [[ "$code" =~ ^(200|301|302|401)$ ]]; then
            log "✅ ArgoCD UI reachable. HTTP $code."
        else
            warn "Got HTTP $code. UI may still be starting."
        fi
    else
        warn "Could not reach http://argocd.local yet. Ingress may need a few more seconds."
    fi
}

# --- main ---
preflight
install_argocd
show_password
apply_root_app
verify

log "Done."
log "Open http://argocd.local in your browser to see the ArgoCD UI."
log "Add apps by creating subdirectories under gitops/argocd/apps/. 🚀"
