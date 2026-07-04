#!/usr/bin/env bash
# =============================================================
# setup-ghcr-pull.sh — Configure GHCR image pull for kind
#
# Creates a kubernetes docker-registry secret in the sentinel
# namespace so the demo-api pod can pull images from GHCR.
#
# GHCR requires authentication even for public images. Without
# this, the pod stays in ImagePullBackOff.
#
# The secret is created from the GitHub CLI's cached token
# ($GH_TOKEN, or interactive if not set).
#
# Usage:
#   ./scripts/setup-ghcr-pull.sh
# =============================================================

set -euo pipefail

NAMESPACE="sentinel"
SECRET_NAME="ghcr-pull"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[$(date +%H:%M:%S)]${NC} $*"; }
warn() { echo -e "${YELLOW}[$(date +%H:%M:%S)] WARN:${NC} $*"; }
err()  { echo -e "${RED}[$(date +%H:%M:%S)] ERROR:${NC} $*" >&2; }

# Determine the GitHub token. Prefer GH_TOKEN env var, then interactive.
GITHUB_TOKEN="${GH_TOKEN:-}"

if [[ -z "$GITHUB_TOKEN" ]]; then
    if command -v gh &>/dev/null; then
        GITHUB_TOKEN=$(gh auth token 2>/dev/null || true)
    fi
fi

if [[ -z "$GITHUB_TOKEN" ]]; then
    err "No GitHub token found. Set GH_TOKEN or authenticate with 'gh auth login'."
    exit 1
fi

log "Creating / updating docker-registry secret '$SECRET_NAME' in namespace '$NAMESPACE' ..."

kubectl create secret docker-registry "$SECRET_NAME" \
    --namespace "$NAMESPACE" \
    --docker-server=ghcr.io \
    --docker-username=yessine15 \
    --docker-password="$GITHUB_TOKEN" \
    --dry-run=client -o yaml | kubectl apply -f -

log "✅ Secret '$SECRET_NAME' created/updated in namespace '$NAMESPACE'."
log ""
log "Now patch the demo-api service account to use it:"
log "  kubectl patch serviceaccount default -n sentinel \\"  
log "    -p '{\"imagePullSecrets\": [{\"name\": \"ghcr-pull\"}]}'"
