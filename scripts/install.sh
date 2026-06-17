#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON:-python3}"
SKIP_NODE_CHECK="${SKIP_NODE_CHECK:-0}"
INSTALL_DEV="${AUTOBCI_INSTALL_DEV:-0}"
INSTALL_ML="${AUTOBCI_INSTALL_ML:-0}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

step() {
  printf '\n==> %s\n' "$1"
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

step "Checking Python"
"$PYTHON_BIN" --version

if [[ "$SKIP_NODE_CHECK" != "1" ]]; then
  step "Checking Node.js and npm"
  if ! need_cmd node; then
    echo "Node.js was not found. Install Node.js 22+ from https://nodejs.org/ or your OS package manager." >&2
    exit 1
  fi
  if ! need_cmd npm; then
    echo "npm was not found. Install Node.js 22+ from https://nodejs.org/ or your OS package manager." >&2
    exit 1
  fi
  node --version
  npm --version
fi

step "Creating Python virtual environment"
if [[ ! -x ".venv/bin/python" ]]; then
  "$PYTHON_BIN" -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip

step "Installing AutoBCI Python package"
if [[ "$INSTALL_DEV" == "1" ]]; then
  .venv/bin/python -m pip install -e ".[dev]"
else
  .venv/bin/python -m pip install -e .
fi

if [[ "$INSTALL_ML" == "1" ]]; then
  step "Installing optional ML / BCI training dependencies"
  .venv/bin/python -m pip install -r requirements.txt
fi

step "Installing Pi runtime Node dependencies"
if [[ -f package.json ]]; then
  npm install
fi

step "Installing AutoResearch Node dependencies"
if [[ -f tools/autoresearch/package.json ]]; then
  npm --prefix tools/autoresearch install
fi

step "Running readiness checks"
.venv/bin/python -m bci_autoresearch.product_shell.cli doctor --json
if [[ "$(uname -s)" == "Linux" ]]; then
  .venv/bin/python -m bci_autoresearch.product_shell.cli linux doctor
fi

cat <<'EOF'

AutoBCI setup completed.
Start with:
  source .venv/bin/activate
  autobci doctor --json
  autobci status --json

On first launch, configure a model provider key. AutoBCI does not include a fake fallback model.
For historical BCI training scripts, rerun with AUTOBCI_INSTALL_ML=1 to install heavy ML dependencies.
EOF
