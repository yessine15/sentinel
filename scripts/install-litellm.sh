#!/usr/bin/env bash
# =============================================================
# install-litellm.sh — Deploy LiteLLM proxy on Sentinel
#
# Deploys LiteLLM via its self-contained Helm chart with values
# overrides.  The chart is at gitops/components/litellm/ and
# deploys a single-replica proxy that forwards requests to
# Ollama (on the host) and optionally to OpenAI.
#
# Uses hostNetwork on kind so the pod can reach the host's
# Ollama at localhost:11434.
#
# Exposed at http://llm.local via ingress-nginx.
# /etc/hosts should already have an entry for llm.local
# (added by install-ingress.sh).
#
# Usage:
#   ./scripts/install-litellm.sh
# =============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHART_DIR="$ROOT_DIR/gitops/components/litellm"
RELEASE="litellm"
NAMESPACE="litellm"
PROXY_PORT=4000

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
            err "$cmd is required but not installed."
            exit 1
        fi
    done

    if ! kubectl cluster-info --context kind-sentinel >/dev/null 2>&1; then
        err "Cannot connect to kind-sentinel cluster. Is kind-up.sh running?"
        exit 1
    fi

    # Check that Ollama is reachable from the host (needed by the pod)
    if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
        warn "Ollama is not reachable at localhost:11434."
        warn "LiteLLM will deploy but may not be able to proxy requests."
        warn "Start Ollama:  ollama serve"
    else
        log "Ollama is running at localhost:11434"
    fi

    # Check llm.local resolves
    if ! grep -q "llm.local" /etc/hosts 2>/dev/null; then
        warn "llm.local is not in /etc/hosts."
        warn "Add:  echo '127.0.0.1 llm.local' | sudo tee -a /etc/hosts"
    fi

    log "Preflight checks passed."
}

deploy_litellm() {
    info "--- Deploying LiteLLM Service & Endpoints ---"

    # Apply the Helm chart (Service + Endpoints + Ingress)
    helm upgrade --install "$RELEASE" "$CHART_DIR" \
        --namespace "$NAMESPACE" \
        --create-namespace \
        --wait \
        --timeout 2m

    log "Kubernetes resources deployed in namespace '$NAMESPACE'."

    # Start the host proxy if not already running
    info "--- Starting host proxy ---"
    if curl -sf http://localhost:"$PROXY_PORT"/health >/dev/null 2>&1; then
        log "Host proxy already running on port $PROXY_PORT"
    else
        PROXY_SCRIPT="$CHART_DIR/proxy.py"
        if [ ! -f "$PROXY_SCRIPT" ]; then
            err "Proxy script not found at $PROXY_SCRIPT"
            exit 1
        fi
        log "Starting host proxy on port $PROXY_PORT..."
        nohup python3 "$PROXY_SCRIPT" > /tmp/litellm-proxy.log 2>&1 &
        PROXY_PID=$!
        echo "$PROXY_PID" > /tmp/litellm-proxy.pid
        sleep 2
        if curl -sf http://localhost:"$PROXY_PORT"/health >/dev/null 2>&1; then
            log "Host proxy started (PID: $PROXY_PID)"
        else
            err "Host proxy failed to start. Check /tmp/litellm-proxy.log"
            exit 1
        fi
    fi
}

verify() {
    info "--- Verification ---"

    # Test the host proxy directly
    info "Testing host proxy on localhost:$PROXY_PORT ..."
    if curl -sf http://localhost:$PROXY_PORT/health >/dev/null 2>&1; then
        log "Host proxy is reachable"
    else
        warn "Host proxy not reachable"
    fi

    # Test the external endpoint
    info "Testing http://llm.local/health ..."
    if curl -sf http://llm.local/health >/dev/null 2>&1; then
        log "llm.local is reachable!"
    else
        warn "llm.local not reachable yet."
    fi

    # Test chat completion
    info "Testing http://llm.local/v1/chat/completions ..."
    if curl -sf http://llm.local/v1/chat/completions \
        -H "Content-Type: application/json" \
        -d '{"model":"gemma4","messages":[{"role":"user","content":"Say OK"}]}' \
        >/dev/null 2>&1; then
        log "Chat completions work!"
    else
        warn "Chat completions not working yet."
    fi
}

# =============================================================
# Main
# =============================================================

echo ""
echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}  Sentinel — LiteLLM Gateway Installer       ${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

preflight
deploy_litellm
verify

echo ""
log "LiteLLM installation complete."
info "  Internal URL:  http://litellm.litellm.svc:4000"
info "  External URL:  http://llm.local"
info "  Test chat:     curl http://llm.local/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"gemma4\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}]}'"
echo ""
