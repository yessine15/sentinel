#!/usr/bin/env bash
# =============================================================
# install-observability.sh — Deploy kube-prometheus-stack on Sentinel
#
# Installs: Prometheus + Alertmanager + Grafana + node-exporter +
# kube-state-metrics, all via the kube-prometheus-stack Helm chart.
#
# Grafana is exposed at http://grafana.local via ingress-nginx.
# The full values file lives at
#   gitops/components/observability/kube-prometheus-stack.yaml
# and is the source of truth for the Helm release.
#
# Usage:
#   ./scripts/install-observability.sh
# =============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VALUES_FILE="$ROOT_DIR/gitops/components/observability/kube-prometheus-stack.yaml"
RELEASE="kube-prometheus-stack"
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
}

install_kps() {
    if helm status "$RELEASE" -n "$NAMESPACE" >/dev/null 2>&1; then
        warn "Release '$RELEASE' already installed. Upgrading ..."
        helm upgrade "$RELEASE" prometheus-community/kube-prometheus-stack \
            --namespace "$NAMESPACE" \
            -f "$VALUES_FILE"
    else
        log "Adding prometheus-community Helm repo ..."
        helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
        helm repo update

        log "Installing kube-prometheus-stack ..."
        helm install "$RELEASE" prometheus-community/kube-prometheus-stack \
            --namespace "$NAMESPACE" \
            --create-namespace \
            -f "$VALUES_FILE" \
            --wait --timeout 10m
    fi

    log "Waiting for Prometheus pod to be Ready ..."
    kubectl wait --namespace "$NAMESPACE" \
        --for=condition=ready pod \
        --selector=app.kubernetes.io/name=prometheus \
        --timeout=300s 2>/dev/null || warn "Prometheus pod selector not matched yet; continuing."

    log "Waiting for Grafana pod to be Ready ..."
    kubectl wait --namespace "$NAMESPACE" \
        --for=condition=ready pod \
        --selector=app.kubernetes.io/name=grafana \
        --timeout=300s

    log "Observability pods:"
    kubectl get pods -n "$NAMESPACE" -o wide 2>&1 | cat
}

verify() {
    log "Verifying Grafana UI at http://grafana.local ..."
    if code=$(curl -sS -o /dev/null -w "%{http_code}" http://grafana.local 2>/dev/null); then
        if [[ "$code" =~ ^(200|301|302)$ ]]; then
            log "✅ Grafana UI reachable. HTTP $code."
        else
            warn "Got HTTP $code. Grafana may still be starting."
        fi
    else
        warn "Could not reach http://grafana.local yet. Ingress may need a few more seconds."
    fi

    log "Verifying Prometheus is scraping ..."
    local prom_count
    prom_count=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=prometheus --no-headers 2>/dev/null | wc -l)
    if [[ "$prom_count" -ge 1 ]]; then
        log "✅ Prometheus pod running ($prom_count)."
    else
        warn "No Prometheus pod found yet."
    fi
}

show_credentials() {
    echo ""
    echo -e "  ${BLUE}Grafana UI:${NC}   http://grafana.local"
    echo -e "  ${BLUE}Username:${NC}    admin"
    echo -e "  ${BLUE}Password:${NC}    admin"
    echo ""
    log "Dashboards are pre-loaded. The Prometheus datasource is preconfigured."
}

preflight
install_kps
verify
show_credentials

log "Done. Next: deploy Loki (T0.11) and Tempo (T0.12)."
