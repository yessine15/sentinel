#!/usr/bin/env bash
# =============================================================
# install-postgres.sh — Deploy Postgres 16 + pgvector on Sentinel
#
# Installs Postgres with the pgvector extension via our self-contained
# Helm chart at gitops/components/postgres/. The pgvector/pgvector:pg16
# image ships with the `vector` extension pre-compiled, and an initdb
# script runs `CREATE EXTENSION vector;` on first boot.
#
# No Ingress — Postgres speaks a binary TCP protocol, not HTTP.
# Use port-forward for local psql access:
#   kubectl -n postgres port-forward svc/postgres 5432:5432
#
# Usage:
#   ./scripts/install-postgres.sh
# =============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHART_DIR="$ROOT_DIR/gitops/components/postgres"
VALUES_FILE="$CHART_DIR/values.yaml"
RELEASE="postgres"
NAMESPACE="postgres"

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

install_postgres() {
    info "--- Installing Postgres + pgvector ---"

    if helm status "$RELEASE" -n "$NAMESPACE" >/dev/null 2>&1; then
        warn "Release '$RELEASE' already installed. Upgrading ..."
        helm upgrade "$RELEASE" "$CHART_DIR" \
            --namespace "$NAMESPACE" \
            -f "$VALUES_FILE"
    else
        log "Installing Postgres Helm chart ..."
        helm install "$RELEASE" "$CHART_DIR" \
            --namespace "$NAMESPACE" \
            --create-namespace \
            -f "$VALUES_FILE" \
            --wait --timeout 5m
    fi

    log "Waiting for Postgres pod to be Ready ..."
    kubectl wait --namespace "$NAMESPACE" \
        --for=condition=ready pod \
        --selector=app.kubernetes.io/name=postgres \
        --timeout=300s 2>/dev/null || warn "Postgres pod selector not matched; continuing."
}

verify() {
    info "--- Verification ---"

    log "Pod status:"
    kubectl get pods -n "$NAMESPACE" -o wide

    log "Service:"
    kubectl get svc -n "$NAMESPACE"

    log "PVC:"
    kubectl get pvc -n "$NAMESPACE"

    info "--- Checking pgvector extension ---"
    log "Running \\dx inside the Postgres pod ..."
    if kubectl exec -n "$NAMESPACE" deploy/"$RELEASE" -- \
        psql -U sentinel -d sentinel -c '\dx' 2>/dev/null; then
        log "pgvector extension check complete (look for 'vector' above)."
    else
        warn "Could not run psql inside the pod. Try manually:"
        echo "  kubectl -n $NAMESPACE exec deploy/$RELEASE -- psql -U sentinel -d sentinel -c '\\dx'"
    fi

    info "--- Access hint ---"
    echo "  Port-forward:  kubectl -n $NAMESPACE port-forward svc/$RELEASE 5432:5432"
    echo "  Then:          PGPASSWORD=sentinel psql -h localhost -U sentinel -d sentinel -c '\\dx'"
}

# --- Main -------------------------------------------------------
preflight
install_postgres
verify

log "Postgres + pgvector installation complete."
