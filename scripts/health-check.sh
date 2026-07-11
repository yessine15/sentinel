#!/usr/bin/env bash
# =============================================================
# health-check.sh — Sentinel Full System Health Check
#
# Usage:
#   ./scripts/health-check.sh
#
# Checks:
#   - Kubernetes nodes
#   - Required namespaces exist
#   - ArgoCD pods and Applications
#   - Ingress endpoints (sentinel, grafana, qdrant, argocd)
#   - Observability stack (Prometheus, Grafana, Loki, Tempo, etc.)
#   - Qdrant vector DB
#   - Postgres + pgvector
#   - Demo API
#
# Exit code: number of failed checks (0 = all pass)
# =============================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASS=0
FAIL=0
WARN=0

check() {
  local label="$1" status="$2" detail="$3"
  if [[ "$status" == "PASS" ]]; then
    echo -e "  ${GREEN}✅${NC} $label — $detail"
    PASS=$((PASS + 1))
  elif [[ "$status" == "WARN" ]]; then
    echo -e "  ${YELLOW}⚠️  ${NC} $label — $detail"
    WARN=$((WARN + 1))
  else
    echo -e "  ${RED}❌${NC} $label — $detail"
    FAIL=$((FAIL + 1))
  fi
}

echo ""
echo "============================================"
echo "  🛡️  Sentinel — Full System Health Check"
echo "  $(date)"
echo "============================================"
echo ""

# ── Cluster ──
echo "📦 Cluster"
NODES=$(kubectl get nodes --no-headers 2>/dev/null | wc -l)
NOT_READY=$(kubectl get nodes --no-headers 2>/dev/null | { grep -v Ready || true; } | wc -l)
if [[ "$NODES" -ge 3 && "$NOT_READY" -eq 0 ]]; then
  check "Nodes" "PASS" "$NODES nodes, all Ready"
elif [[ "$NODES" -ge 3 ]]; then
  check "Nodes" "WARN" "$NODES nodes, $NOT_READY not Ready"
elif [[ "$NODES" -gt 0 ]]; then
  check "Nodes" "WARN" "$NODES nodes (expected 3)"
else
  check "Nodes" "FAIL" "No nodes found — cluster may be down"
fi

# ── Namespaces ──
echo ""
echo "📂 Required Namespaces"
for ns in ingress-nginx argocd observability qdrant postgres sentinel; do
  if kubectl get namespace "$ns" &>/dev/null; then
    check "Namespace: $ns" "PASS" "Exists"
  else
    check "Namespace: $ns" "FAIL" "Missing"
  fi
done

# ── ArgoCD ──
echo ""
echo "🔄 ArgoCD"
ARGOCD_PODS=$(kubectl get pods -n argocd --no-headers 2>/dev/null | wc -l)
if [[ "$ARGOCD_PODS" -ge 3 ]]; then
  check "ArgoCD pods" "PASS" "$ARGOCD_PODS pods running"
  APPS=$(kubectl get applications -n argocd --no-headers 2>/dev/null | wc -l)
  check "ArgoCD apps" "PASS" "$APPS Applications registered"
elif [[ "$ARGOCD_PODS" -gt 0 ]]; then
  check "ArgoCD pods" "WARN" "$ARGOCD_PODS pods (expected ≥ 3)"
else
  check "ArgoCD pods" "FAIL" "No pods found"
fi

# ── Ingress ──
echo ""
echo "🌐 Ingress Endpoints"
declare -A ENDPOINTS=(
  ["sentinel (sentinel.local/ping)"]="http://sentinel.local/ping"
  ["Grafana (grafana.local)"]="http://grafana.local"
  ["Qdrant (qdrant.local)"]="http://qdrant.local/collections"
  ["ArgoCD (argocd.local)"]="https://argocd.local"
)
for name in "${!ENDPOINTS[@]}"; do
  url="${ENDPOINTS[$name]}"
  code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 3 "$url" 2>/dev/null || echo "FAIL")
  if [[ "$code" =~ ^(200|301|302|404)$ ]]; then
    check "Ingress: $name" "PASS" "HTTP $code"
  else
    check "Ingress: $name" "FAIL" "HTTP $code"
  fi
done

# ── Observability ──
echo ""
echo "📊 Observability Stack"
declare -A OBSERVABILITY=(
  ["Prometheus"]="app.kubernetes.io/name=prometheus"
  ["Grafana"]="app.kubernetes.io/name=grafana"
  ["Alertmanager"]="app.kubernetes.io/name=alertmanager"
  ["Loki"]="app.kubernetes.io/name=loki"
  ["Promtail"]="app.kubernetes.io/name=promtail"
  ["Tempo"]="app.kubernetes.io/name=tempo"
  ["OTel Collector"]="app.kubernetes.io/name=otel-collector"
)
for name in "${!OBSERVABILITY[@]}"; do
  selector="${OBSERVABILITY[$name]}"
  count=$(kubectl get pods -n observability --no-headers -l "$selector" 2>/dev/null | wc -l)
  ready=$(kubectl get pods -n observability -l "$selector" --no-headers 2>/dev/null | awk '{print $2}' | { grep -cE '^[0-9]+/[0-9]+$' || true; })
  if [[ "$count" -ge 1 && "$ready" -ge 1 ]]; then
    check "Obs: $name" "PASS" "Pod running"
  else
    check "Obs: $name" "FAIL" "No ready pod found"
  fi
done

# ── Qdrant ──
echo ""
echo "🗄️  Qdrant"
QDRANT_READY=$(kubectl get pods -n qdrant --no-headers 2>/dev/null | awk '{print $2}' | { grep -cE '^1/1$' || true; })
if [[ "$QDRANT_READY" -ge 1 ]]; then
  check "Qdrant pod" "PASS" "1/1 Running"
  QDRANT_API=$(curl -sS -o /dev/null -w "%{http_code}" http://qdrant.local/collections 2>/dev/null || echo "FAIL")
  if [[ "$QDRANT_API" == "200" ]]; then
    check "Qdrant API" "PASS" "HTTP 200, collections accessible"
  else
    check "Qdrant API" "FAIL" "HTTP $QDRANT_API"
  fi
else
  check "Qdrant pod" "FAIL" "Not running"
fi

# ── Postgres + pgvector ──
echo ""
echo "🐘 Postgres + pgvector"
PG_READY=$(kubectl get pods -n postgres --no-headers 2>/dev/null | awk '{print $2}' | { grep -cE '^1/1$' || true; })
if [[ "$PG_READY" -ge 1 ]]; then
  check "Postgres pod" "PASS" "1/1 Running"
  # Verify the pgvector extension is installed (T1.2 "Done when" criterion).
  if kubectl exec -n postgres deploy/postgres -- psql -U sentinel -d sentinel -tAc \
       "SELECT 1 FROM pg_extension WHERE extname='vector'" 2>/dev/null | grep -q 1; then
    check "pgvector extension" "PASS" "\\dx shows vector"
  else
    check "pgvector extension" "FAIL" "vector extension not found in \\dx"
  fi
else
  check "Postgres pod" "FAIL" "Not running"
fi

# ── Demo API ──
echo ""
echo "🚀 Demo API"
DEMO_CODE=$(curl -sS -o /dev/null -w "%{http_code}" http://sentinel.local/ping 2>/dev/null || echo "FAIL")
if [[ "$DEMO_CODE" == "200" ]]; then
  check "Demo API" "PASS" "HTTP 200 on /ping"
else
  check "Demo API" "FAIL" "HTTP $DEMO_CODE"
fi

# ── Summary ──
echo ""
echo "============================================"
echo -e "  ${GREEN}${PASS} passed${NC}, ${YELLOW}${WARN} warnings${NC}, ${RED}${FAIL} failed${NC}"
echo "============================================"
echo ""

exit $FAIL
