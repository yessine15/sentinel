#!/usr/bin/env bash
# =============================================================
# install-tetragon.sh — Deploy Tetragon (eBPF runtime security)
#
# Installs Tetragon 1.7.0 (agent DaemonSet + operator + CRDs) via
# the wrapper chart at gitops/components/tetragon/, then applies
# the starter "suspicious-exec" TracingPolicy.
#
# Event stream: the hubble-export-stdout sidecar tails the agent's
# NDJSON export log to stdout — `kubectl logs ds/tetragon -c
# export-stdout` is the cluster-wide stream the Security Agent's
# tetragon_events tool reads.
#
# NOTE: bootstrap infrastructure (like Cilium) — installed
# manually, NOT via an ArgoCD Application.
#
# Usage:
#   ./scripts/install-tetragon.sh
#
# Prerequisites:
#   - Cilium installed (scripts/install-cilium.sh) — Tetragon
#     requires a working Cilium data path.
# =============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHART_DIR="$ROOT_DIR/gitops/components/tetragon"
NAMESPACE="kube-system"
POLICY_DIR="$CHART_DIR/policies"

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
    kubectl cluster-info >/dev/null 2>&1 || {
        err "Cannot reach the cluster — is kind up?"
        exit 1
    }
}

install_tetragon() {
    log "Fetching the cilium/tetragon subchart..."
    (cd "$CHART_DIR" && helm dependency update >/dev/null)

    log "Installing Tetragon into $NAMESPACE..."
    helm upgrade --install tetragon "$CHART_DIR" -n "$NAMESPACE"

    log "Waiting for the Tetragon agent DaemonSet..."
    kubectl rollout status ds/tetragon -n "$NAMESPACE" --timeout=300s
    kubectl rollout status deploy/tetragon-operator -n "$NAMESPACE" --timeout=300s
}

apply_policies() {
    log "Applying the starter 'suspicious-exec' TracingPolicy..."
    kubectl apply -f "$POLICY_DIR/suspicious-exec.yaml"
    kubectl wait --for=condition=established \
        tracingpolicy.cilium.io/suspicious-exec --timeout=60s 2>/dev/null \
        || warn "TracingPolicy established condition not reported (may still be active)"
}

verify() {
    log "Verification:"
    kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=tetragon \
        -o wide | head -5
    kubectl get tracingpolicy.cilium.io 2>/dev/null || true
    log "Event stream check (last export line per node):"
    for pod in $(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=tetragon \
        -o jsonpath='{.items[*].metadata.name}'); do
        echo "  $pod:"
        kubectl logs -n "$NAMESPACE" "$pod" -c export-stdout --tail=1 2>/dev/null \
            | cut -c1-120
    done
    log "Done."
    warn "Trigger a suspicious exec to see it in the stream:"
    warn "  kubectl exec -n sentinel deploy/test-api -- sh -c 'echo pwned'"
    warn "  PYTHONPATH=agents:api:rag RUN_MODE=live .venv/bin/python -c \\"
    warn "    \"from sentinel_agents.tools.tetragon_events import tetragon_events; \\"
    warn "     print(tetragon_events.invoke({'event_type': 'exec'}))\""
}

preflight
install_tetragon
apply_policies
verify
