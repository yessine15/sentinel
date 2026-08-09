#!/usr/bin/env bash
# =============================================================
# install-cilium.sh — Replace the default CNI (kindnet) with Cilium
#
# Installs Cilium 1.20.0 (eBPF networking + network policies +
# Hubble observability) into kube-system via the wrapper chart at
#   gitops/components/cilium/
# then removes kindnet and restarts all pods onto Cilium.
#
# NOTE: Cilium is bootstrap infrastructure (like ingress-nginx and
# the observability stack) — it is installed manually, NOT via an
# ArgoCD Application, because the CNI must never be deleted/reverted
# by a GitOps sync (that would cut all pod networking).
#
# Usage:
#   ./scripts/install-cilium.sh
#
# Prerequisites:
#   - kind cluster up (scripts/kind-up.sh)
#   - fs.inotify limits raised on nodes for long-lived clusters:
#       docker exec <node> sysctl -w fs.inotify.max_user_instances=1024 \
#                                fs.inotify.max_user_watches=524288
# =============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHART_DIR="$ROOT_DIR/gitops/components/cilium"
NAMESPACE="kube-system"

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

install_cilium() {
    log "Fetching the cilium/cilium subchart..."
    (cd "$CHART_DIR" && helm dependency update >/dev/null)

    log "Installing Cilium into $NAMESPACE..."
    helm upgrade --install cilium "$CHART_DIR" -n "$NAMESPACE"

    log "Waiting for Cilium to become healthy..."
    if command -v cilium >/dev/null 2>&1; then
        cilium status --wait
    else
        warn "cilium CLI not found — waiting on DaemonSet readiness instead."
        kubectl rollout status ds/cilium -n "$NAMESPACE" --timeout=300s
        kubectl rollout status deploy/cilium-operator -n "$NAMESPACE" --timeout=300s
    fi
}

remove_kindnet_and_restart() {
    if kubectl -n kube-system get ds kindnet >/dev/null 2>&1; then
        log "Removing the old kindnet CNI..."
        kubectl -n kube-system delete ds kindnet
    else
        info "kindnet already gone — nothing to remove."
    fi

    log "Restarting ALL pods so they come up with Cilium networking..."
    kubectl delete pods -A --all

    warn "If any pod fails with ImagePullBackOff (private/local image not on"
    warn "the node), re-load it first, e.g.: kind load docker-image <image>"
}

verify() {
    log "Verification:"
    if command -v cilium >/dev/null 2>&1; then
        cilium status || warn "cilium status reported problems — inspect with: cilium status --verbose"
        cilium connectivity test --timeout 10m || warn "connectivity test reported failures"
    fi
    kubectl get pods -A | awk '$4 !~ /Running|Completed/ {print "  NOT READY: " $0}'
    log "Done. Hubble UI: kubectl port-forward -n kube-system svc/hubble-ui 12000:80 → http://localhost:12000"
}

preflight
install_cilium
remove_kindnet_and_restart
verify
