#!/usr/bin/env bash
# =============================================================
# install-qdrant.sh — Deploy Qdrant vector DB on Sentinel
#
# Installs Qdrant via its Helm chart with our overrides.
# Exposed at http://qdrant.local via ingress-nginx.
#
# The full values file lives at
#   gitops/components/qdrant/values.yaml
# and is the source of truth for the Helm release.
#
# Usage:
#   ./scripts/install-qdrant.sh
# =============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VALUES_FILE="$ROOT_DIR/gitops/components/qdrant/values.yaml"
CHART_DIR="$ROOT_DIR/gitops/components/qdrant"
RELEASE="qdrant"
NAMESPACE="qdrant"

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

preflight() {
    info "--- Preflight checks ---"
    for cmd in helm kubectl; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            err "Required command not found: $cmd"
            exit 1
        fi
    done
    if ! kubectl config current-context 2>/dev/null | grep -q "kind-sentinel"; then
        err "Not on kind-sentinel context. Run: kubectl config use-context kind-sentinel"
        exit 1
    fi
    [[ -f "$VALUES_FILE" ]] || { err "Missing $VALUES_FILE"; exit 1; }
    [[ -f "$CHART_DIR/Chart.yaml" ]] || { err "Missing $CHART_DIR/Chart.yaml"; exit 1; }
    log "Preflight OK"
}

install_qdrant() {
    info "--- Installing Qdrant ---"

    # Update Helm dependencies (pulls the upstream qdrant chart).
    log "Updating Helm dependencies ..."
    helm dependency update "$CHART_DIR"

    if helm status "$RELEASE" -n "$NAMESPACE" >/dev/null 2>&1; then
        warn "Release '$RELEASE' already installed. Upgrading ..."
        helm upgrade "$RELEASE" "$CHART_DIR" \
            --namespace "$NAMESPACE" \
            -f "$VALUES_FILE"
    else
        log "Installing Qdrant Helm chart ..."
        helm install "$RELEASE" "$CHART_DIR" \
            --namespace "$NAMESPACE" \
            --create-namespace \
            -f "$VALUES_FILE" \
            --wait --timeout 5m
    fi

    log "Waiting for Qdrant pod to be Ready ..."
    kubectl wait --namespace "$NAMESPACE" \
        --for=condition=ready pod \
        --selector=app.kubernetes.io/name=qdrant \
        --timeout=300s 2>/dev/null || warn "Qdrant pod selector not matched; continuing."
}

verify() {
    info "--- Verification ---"

    # Check pod status.
    log "Pod status:"
    kubectl get pods -n "$NAMESPACE" -o wide

    # Check service.
    log "Service:"
    kubectl get svc -n "$NAMESPACE"

    # Check ingress.
    log "Ingress:"
    kubectl get ingress -n "$NAMESPACE"

    # Test the REST API via port-forward (Ingress DNS may need /etc/hosts).
    log "Testing Qdrant REST API via port-forward ..."
    kubectl port-forward -n "$NAMESPACE" "svc/$RELEASE" 6333:6333 &>/dev/null &
    PF_PID=$!
    sleep 3

    if curl -s http://localhost:6333/collections 2>/dev/null; then
        echo ""
        log "Qdrant is healthy — /collections endpoint responded."
    else
        warn "Could not reach Qdrant on localhost:6333."
    fi

    kill "$PF_PID" 2>/dev/null || true

    # Check DNS hint.
    info "--- DNS hint ---"
    echo "Qdrant REST API: http://qdrant.local (ensure /etc/hosts has 127.0.0.1 qdrant.local)"
    echo "Or use port-forward: kubectl -n $NAMESPACE port-forward svc/$RELEASE 6333:6333"
}

# --- Main -------------------------------------------------------
preflight
install_qdrant
verify

log "Qdrant installation complete."
