#!/usr/bin/env bash
# =============================================================
# install-ingress.sh — Install ingress-nginx on the Sentinel kind cluster
#
# Usage:
#   ./scripts/install-ingress.sh
#
# What it does:
#   1. Adds the ingress-nginx Helm repo
#   2. Installs ingress-nginx with kind-specific values
#   3. Waits for the controller pod to be Ready
#   4. Adds entries to /etc/hosts for sentinel.local, grafana.local, etc.
#   5. Verifies a curl reaches the default backend
# =============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VALUES_FILE="$ROOT_DIR/gitops/components/ingress-nginx/values.yaml"
RELEASE="ingress-nginx"
NAMESPACE="ingress-nginx"

# Hostnames we want reachable on the host browser.
HOSTNAMES=(
    "sentinel.local"
    "grafana.local"
    "loki.local"
    "argocd.local"
    "qdrant.local"
    "llm.local"
)

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
    for cmd in helm kubectl; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            err "Required command not found: $cmd"
            exit 1
        fi
    done

    if ! kubectl config current-context 2>/dev/null | grep -q "kind-sentinel"; then
        err "Not on kind-sentinel context. Run: kubectl config use-context kind-sentinel"
        err "Or create the cluster with: ./scripts/kind-up.sh"
        exit 1
    fi

    if [[ ! -f "$VALUES_FILE" ]]; then
        err "Helm values not found at: $VALUES_FILE"
        exit 1
    fi
}

# --- install ingress-nginx ---
install_ingress() {
    if helm status "$RELEASE" -n "$NAMESPACE" >/dev/null 2>&1; then
        warn "Release '$RELEASE' already installed in namespace '$NAMESPACE'."
        info "To upgrade: helm upgrade ..."
        info "To reinstall: helm uninstall $RELEASE -n $NAMESPACE, then re-run this script."
    else
        log "Adding ingress-nginx Helm repo ..."
        helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
        helm repo update

        log "Installing ingress-nginx ..."
        helm install "$RELEASE" ingress-nginx/ingress-nginx \
            --namespace "$NAMESPACE" \
            --create-namespace \
            -f "$VALUES_FILE"
    fi

    log "Waiting for ingress-nginx controller pod to be Ready ..."
    kubectl wait --namespace "$NAMESPACE" \
        --for=condition=ready pod \
        --selector=app.kubernetes.io/component=controller \
        --timeout=180s

    log "ingress-nginx controller is Ready:"
    kubectl get pods -n "$NAMESPACE" -o wide | cat
}

# --- update /etc/hosts ---
update_hosts() {
    local marker="# sentinel-ingress-managed"
    local need_update=false

    if ! grep -q "$marker" /etc/hosts 2>/dev/null; then
        need_update=true
    fi

    if $need_update; then
        log "Adding Sentinel hostnames to /etc/hosts (needs sudo) ..."
        {
            echo ""
            echo "$marker"
            for h in "${HOSTNAMES[@]}"; do
                echo "127.0.0.1 $h"
            done
        } | sudo tee -a /etc/hosts >/dev/null
        log "/etc/hosts updated."
    else
        warn "/etc/hosts already has Sentinel entries (marker found). Skipping."
    fi

    info "Current Sentinel entries in /etc/hosts:"
    grep -A ${#HOSTNAMES[@]} "$marker" /etc/hosts 2>/dev/null | cat || true
}

# --- verify ---
verify() {
    log "Verifying ingress responds on http://sentinel.local ..."
    if curl -sS -o /dev/null -w "%{http_code}" http://sentinel.local 2>/dev/null > /tmp/sentinel_ingress_code; then
        code=$(cat /tmp/sentinel_ingress_code)
        # 404 is the expected nginx default backend — no Ingress route matches yet.
        if [[ "$code" == "404" || "$code" == "503" ]]; then
            log "✅ ingress-nginx is reachable. HTTP $code (default backend, no route yet — expected)."
        else
            warn "Got HTTP $code. Expected 404 (default backend). Check the controller logs."
        fi
    else
        err "Could not reach http://sentinel.local. Ingress may not be up yet."
    fi
    rm -f /tmp/sentinel_ingress_code
}

# --- main ---
preflight
install_ingress
update_hosts
verify

log "Done. Next: deploy services and create Ingress objects pointing at them."
log "They'll be reachable as http://<service>.local from the host browser."
