#!/usr/bin/env bash
# =============================================================
# install-loki.sh — Deploy Loki + Promtail on the Sentinel kind cluster
#
# Loki = log aggregation (stores + indexes log lines).
# Promtail = log shipper (reads container logs from disk, sends to Loki).
#
# After this script, Grafana "Explore > Loki" shows container logs.
#
# Usage:
#   ./scripts/install-loki.sh
# =============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOKI_VALUES="$ROOT_DIR/gitops/components/observability/loki.yaml"
PROMTAIL_VALUES="$ROOT_DIR/gitops/components/observability/promtail.yaml"
LOKI_RELEASE="loki"
PROMTAIL_RELEASE="promtail"
NAMESPACE="observability"

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
    for cmd in helm kubectl curl; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            err "Required command not found: $cmd"
            exit 1
        fi
    done
    if ! kubectl config current-context 2>/dev/null | grep -q "kind-sentinel"; then
        err "Not on kind-sentinel context."
        exit 1
    fi
    [[ -f "$LOKI_VALUES" ]] || { err "Missing $LOKI_VALUES"; exit 1; }
    [[ -f "$PROMTAIL_VALUES" ]] || { err "Missing $PROMTAIL_VALUES"; exit 1; }
}

install_loki() {
    log "Adding grafana Helm repo ..."
    helm repo add grafana https://grafana.github.io/helm-charts
    helm repo update

    # The loki-stack chart bundles Loki + Promtail in one release.
    # Simpler than managing them separately, and avoids the S3-storage
    # requirement of the standalone loki 3.x chart.
    if helm status "$LOKI_RELEASE" -n "$NAMESPACE" >/dev/null 2>&1; then
        warn "$LOKI_RELEASE already installed. Upgrading ..."
        helm upgrade "$LOKI_RELEASE" grafana/loki-stack \
            --namespace "$NAMESPACE" \
            -f "$LOKI_VALUES"
    else
        log "Installing Loki + Promtail (via loki-stack chart) ..."
        helm install "$LOKI_RELEASE" grafana/loki-stack \
            --namespace "$NAMESPACE" \
            -f "$LOKI_VALUES"
    fi
}

wait_for_pods() {
    log "Waiting for Loki pod to be Ready ..."
    kubectl wait --namespace "$NAMESPACE" \
        --for=condition=ready pod \
        --selector=app.kubernetes.io/name=loki \
        --timeout=180s 2>/dev/null || warn "Loki pod not Ready yet — continuing."

    log "Waiting for Promtail DaemonSet pods to be Ready ..."
    kubectl wait --namespace "$NAMESPACE" \
        --for=condition=ready pod \
        --selector=app.kubernetes.io/name=promtail \
        --timeout=180s 2>/dev/null || warn "Promtail pods not Ready yet — continuing."

    log "Observability pods (with Loki + Promtail):"
    kubectl get pods -n "$NAMESPACE" -o wide 2>&1 | cat
}

verify() {
    log "Verifying Loki is reachable from a curl inside the cluster ..."
    # kubectl run a temporary pod that curls Loki on its internal service URL.
    if kubectl run loki-verify --rm -i --restart=Never --image=curlimages/curl:latest \
        -- curl -sS -o /dev/null -w "%{http_code}" http://loki.observability:3100/ready 2>/dev/null | grep -q "200"; then
        log "✅ Loki internal endpoint: /ready returns 200."
    else
        warn "Loki /ready check failed. Pod may still be starting."
    fi

    log "Verifying Loki is reachable via Ingress at http://loki.local ..."
    if code=$(curl -sS -o /dev/null -w "%{http_code}" http://loki.local/ready 2>/dev/null); then
        if [[ "$code" == "200" ]]; then
            log "✅ Loki Ingress: /ready returns HTTP 200."
        else
            warn "Loki Ingress returned HTTP $code (expected 200)."
        fi
    else
        warn "Could not reach http://loki.local/ready yet."
    fi
}

show_quick_query() {
    echo ""
    info "Loki is now collecting logs from all pods."
    info ""
    info "Quick check via Grafana:"
    info "  1. Open http://grafana.local → Explore → select 'Loki' datasource"
    info "  2. Try a LogQL query: {namespace=\"observability\"}"
    info "     (shows logs from the observability namespace)"
    info "  3. Or: {app=\"loki\"}"
    info "     (shows Loki's own logs — dogfooding!)"
    echo ""
}

preflight
install_loki
wait_for_pods
verify
show_quick_query

log "Done. Next: deploy Tempo + OTel collector (T0.12)."
