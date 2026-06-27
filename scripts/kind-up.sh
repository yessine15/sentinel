#!/usr/bin/env bash
# =============================================================
# kind-up.sh — Create (or recreate) the Sentinel kind cluster
#
# Usage:
#   ./scripts/kind-up.sh          # create if missing
#   ./scripts/kind-up.sh --recreate   # delete + recreate
#   ./scripts/kind-up.sh --delete      # delete only
# =============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CLUSTER_CONFIG="$ROOT_DIR/infra/kind-cluster.yaml"
CLUSTER_NAME="sentinel"
DOCKER_PREFIX=""  # set by preflight() if direct docker access fails

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
    for cmd in kind kubectl; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            err "Required command not found: $cmd"
            err "Run scripts/install-check.sh to verify prerequisites."
            exit 1
        fi
    done

    if ! command -v docker >/dev/null 2>&1; then
        err "Required command not found: docker"
        exit 1
    fi

    # Make sure the docker daemon is actually running and reachable.
    # If the user is not yet in the 'docker' group (e.g. just added, needs
    # re-login), fall back to `sg docker -c` to use the group for this call.
    if ! docker info >/dev/null 2>&1; then
        if sg docker -c "docker info" >/dev/null 2>&1; then
            warn "Docker socket not accessible directly — using 'sg docker' wrapper."
            warn "To fix permanently: log out and log back in (or reboot) so the"
            warn "'docker' group membership takes effect for new shells."
            DOCKER_PREFIX="sg docker -c"
        else
            err "Docker daemon is not running or not accessible."
            err "Start it with: sudo systemctl start docker"
            exit 1
        fi
    else
        DOCKER_PREFIX=""
    fi

    if [[ ! -f "$CLUSTER_CONFIG" ]]; then
        err "kind config not found at: $CLUSTER_CONFIG"
        exit 1
    fi
}

# --- create cluster ---
create_cluster() {
    if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
        warn "Cluster '$CLUSTER_NAME' already exists."
        info "Use: $0 --recreate   to delete + recreate"
        info "Use: $0 --delete      to remove it"
        return 0
    fi

    log "Creating kind cluster '$CLUSTER_NAME' from $CLUSTER_CONFIG ..."
    if [[ -n "$DOCKER_PREFIX" ]]; then
        sg docker -c "kind create cluster --name '$CLUSTER_NAME' --config '$CLUSTER_CONFIG'"
    else
        kind create cluster --name "$CLUSTER_NAME" --config "$CLUSTER_CONFIG"
    fi

    log "Waiting for nodes to be Ready ..."
    kubectl wait --for=condition=Ready nodes --all --timeout=120s

    log "Cluster is up. Nodes:"
    kubectl get nodes -o wide
}

# --- delete cluster ---
delete_cluster() {
    if ! kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
        warn "Cluster '$CLUSTER_NAME' does not exist; nothing to delete."
        return 0
    fi
    log "Deleting kind cluster '$CLUSTER_NAME' ..."
    if [[ -n "$DOCKER_PREFIX" ]]; then
        sg docker -c "kind delete cluster --name '$CLUSTER_NAME'"
    else
        kind delete cluster --name "$CLUSTER_NAME"
    fi
    log "Done."
}

# --- main ---
ACTION="create"
case "${1:-}" in
    --recreate) ACTION="recreate" ;;
    --delete)   ACTION="delete"   ;;
    --help|-h)
        cat <<EOF
Usage: $0 [options]

Options:
  (no arg)        Create the kind cluster if it does not exist.
  --recreate      Delete and recreate the cluster.
  --delete        Delete the cluster and exit.
  --help, -h      Show this help.
EOF
        exit 0
        ;;
    "")
        ACTION="create"
        ;;
    *)
        err "Unknown option: $1"
        exit 1
        ;;
esac

preflight

case "$ACTION" in
    create)
        create_cluster
        ;;
    delete)
        delete_cluster
        ;;
    recreate)
        delete_cluster
        create_cluster
        ;;
esac

log "kubemaster! Cluster name: $CLUSTER_NAME"
log "kubectl context is: $(kubectl config current-context 2>/dev/null || echo 'unknown')"
log "Next: install ingress-nginx (T0.5), then ArgoCD (T0.6)."
