#!/usr/bin/env bash
# =============================================================
# install-check.sh — Verify all Sentinel prerequisites are installed
# Run: bash scripts/install-check.sh
# =============================================================

set -e

# Ensure Go is in PATH (installed to /usr/local/go/bin)
export PATH="$PATH:/usr/local/go/bin"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

pass=0
fail=0

check() {
    local name="$1"
    local cmd="$2"

    if version=$(eval "$cmd" 2>/dev/null); then
        echo -e "  ${GREEN}✅${NC} $name: $version"
        pass=$((pass + 1))
    else
        echo -e "  ${RED}❌${NC} $name: NOT INSTALLED"
        fail=$((fail + 1))
    fi
}

echo ""
echo "======================================"
echo "  Sentinel — Prerequisites Check"
echo "======================================"
echo ""

echo "📦 Container & Kubernetes:"
check "Docker"     "docker --version | head -1"
check "kind"       "kind --version"
check "kubectl"    "kubectl version --client 2>/dev/null | head -1"
check "helm"       "helm version --short"

echo ""
echo "🔧 Infrastructure:"
check "Terraform"  "terraform --version | head -1"
check "Go"         "go version"

echo ""
echo "🐍 Languages & Runtimes:"
check "Python"     "python3 --version"
check "uv"         "uv --version"
check "Node.js"    "node --version"
check "npm"        "npm --version"

echo ""
echo "🤖 AI / LLM:"
check "Ollama"     "ollama --version"

echo ""
echo "🔒 Security:"
check "Trivy"      "trivy --version | head -1"
check "Cosign"     "cosign version 2>/dev/null | head -1"

echo ""
echo "🛠️  Version Control:"
check "Git"        "git --version"

echo ""
echo "======================================"
echo -e "  Results: ${GREEN}${pass} passed${NC}, ${RED}${fail} failed${NC}"
echo "======================================"

if [ $fail -gt 0 ]; then
    echo ""
    echo -e "${RED}Some required tools are missing. Install them before proceeding.${NC}"
    exit 1
else
    echo ""
    echo -e "${GREEN}All required tools are installed. You're ready to go! 🚀${NC}"
    exit 0
fi