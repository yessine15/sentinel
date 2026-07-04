#!/usr/bin/env bash
# =============================================================
# install-tempo.sh — Deploy Grafana Tempo + OTel collector
#
# Tempo = distributed tracing backend (stores spans).
# OTel collector = receives OTLP from apps, forwards to Tempo.
#
# After this script, Grafana "Explore > Tempo" can query traces.
# The demo API (T0.13) will be instrumented to emit spans.
#
# Usage:
#   ./scripts/install-tempo.sh
# =============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPO_VALUES="$ROOT_DIR/gitops/components/observability/tempo.yaml"
OTEL_VALUES="$ROOT_DIR/gitops/components/observability/otel-collector.yaml"
TEMPO_RELEASE="tempo"
OTEL_RELEASE="otel-collector"
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
        err "Not on kind-sentinel context."
        exit 1
    fi
    [[ -f "$TEMPO_VALUES" ]] || { err "Missing $TEMPO_VALUES"; exit 1; }
    [[ -f "$OTEL_VALUES" ]] || { err "Missing $OTEL_VALUES"; exit 1; }
}

install_tempo() {
    if helm status "$TEMPO_RELEASE" -n "$NAMESPACE" >/dev/null 2>&1; then
        warn "$TEMPO_RELEASE already installed. Upgrading ..."
        helm upgrade "$TEMPO_RELEASE" grafana/tempo \
            --namespace "$NAMESPACE" \
            -f "$TEMPO_VALUES"
    else
        log "Installing Grafana Tempo (single-binary mode) ..."
        helm install "$TEMPO_RELEASE" grafana/tempo \
            --namespace "$NAMESPACE" \
            -f "$TEMPO_VALUES"
    fi
}

install_otel_collector() {
    if helm status "$OTEL_RELEASE" -n "$NAMESPACE" >/dev/null 2>&1; then
        warn "$OTEL_RELEASE already installed. Upgrading ..."
        helm upgrade "$OTEL_RELEASE" open-telemetry/opentelemetry-collector \
            --namespace "$NAMESPACE" \
            -f "$OTEL_VALUES"
    else
        log "Installing OpenTelemetry Collector (contrib build) ..."
        helm install "$OTEL_RELEASE" open-telemetry/opentelemetry-collector \
            --namespace "$NAMESPACE" \
            -f "$OTEL_VALUES"
    fi
}

wait_for_pods() {
    log "Waiting for Tempo pod to be Ready ..."
    kubectl wait --namespace "$NAMESPACE" \
        --for=condition=ready pod \
        --selector=app.kubernetes.io/name=tempo \
        --timeout=180s 2>/dev/null || warn "Tempo pod not Ready yet — continuing."

    log "Waiting for OTel collector pod to be Ready ..."
    kubectl wait --namespace "$NAMESPACE" \
        --for=condition=ready pod \
        --selector=app.kubernetes.io/name=otel-collector \
        --timeout=180s 2>/dev/null || warn "OTel collector pod not Ready yet — continuing."
}

verify() {
    log "Observability pods (with Tempo + OTel collector):"
    kubectl get pods -n "$NAMESPACE" -o wide 2>&1 | cat

    log "Verifying Tempo is reachable ..."
    # Tempo's query frontend listens on 3200
    if kubectl run tempo-verify --rm -i --restart=Never --image=curlimages/curl:latest \
        -- -sS -o /dev/null -w "%{http_code}" http://tempo.observability:3200/ready 2>/dev/null | grep -q "200"; then
        log "✅ Tempo /ready returns 200."
    else
        warn "Tempo /ready check failed. May still be starting."
    fi

    log "Verifying OTel collector is reachable ..."
    # Collector's health check on 13133
    if kubectl run otel-verify --rm -i --restart=Never --image=curlimages/curl:latest \
        -- -sS -o /dev/null -w "%{http_code}" http://otel-collector.observability:13133/health 2>/dev/null | grep -q "200"; then
        log "✅ OTel collector /health returns 200."
    else
        warn "OTel collector /health check failed. May still be starting."
    fi
}

update_grafana() {
    log "Upgrading kube-prometheus-stack with Tempo datasource ..."
    helm upgrade kube-prometheus-stack prometheus-community/kube-prometheus-stack \
        --namespace "$NAMESPACE" \
        -f "$ROOT_DIR/gitops/components/observability/kube-prometheus-stack.yaml" 2>&1 | tail -3
    log "✅ Grafana Tempo datasource added (if not already present)."
}

show_info() {
    echo ""
    info "Tempo is now storing traces."
    info "OTel collector is receiving OTLP on :4317 (gRPC) and :4318 (HTTP)."
    info ""
    info "Quick check via Grafana:"
    info "  1. Open http://grafana.local → Explore → select 'Tempo' datasource"
    info "  2. Search for a trace ID (requires an instrumented app — T0.13)"
    info "  3. Or check 'Service Graph' for an overview"
    echo ""
    info "Demo API ('telemetry' group) will be instrumented in T0.13."
}

preflight
install_tempo
install_otel_collector
wait_for_pods
verify
update_grafana
show_info

log "Done. Next: instrument the demo API with OpenTelemetry (T0.13)."
