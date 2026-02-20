#!/usr/bin/env bash
# One-shot setup: ensure Python 3.14, create .venv, install deps.
# Usage: ./scripts/setup.sh   (from repo root) or  bash scripts/setup.sh
set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

REQUIRED_MAJOR=3
REQUIRED_MINOR=14

find_python() {
  # Prefer python3.14, then python3 (if 3.14+)
  for candidate in python3.14 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c "import sys; exit(0 if (sys.version_info.major, sys.version_info.minor) >= ($REQUIRED_MAJOR, $REQUIRED_MINOR) else 1)" 2>/dev/null; then
        echo "$candidate"
        return
      fi
    fi
  done
  # Homebrew python@3.14 (after brew install python@3.14)
  if command -v brew >/dev/null 2>&1; then
    prefix="$(brew --prefix python@3.14 2>/dev/null)" || true
    if [[ -n "$prefix" && -x "$prefix/bin/python3" ]]; then
      echo "$prefix/bin/python3"
      return
    fi
  fi
  echo ""
}

install_python_macos() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    return 1
  fi
  if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew not found. Install Python 3.14 from https://www.python.org/downloads/ or install pyenv and run: pyenv install 3.14"
    return 1
  fi
  echo "Installing Python 3.14 via Homebrew (this may take a minute)..."
  if brew install python@3.14; then
    echo "Python 3.14 installed."
    return 0
  fi
  echo "Homebrew install failed. Install Python 3.14 from https://www.python.org/downloads/ or: pyenv install 3.14"
  return 1
}

PYTHON="$(find_python)"
if [[ -z "$PYTHON" ]]; then
  echo "Python ${REQUIRED_MAJOR}.${REQUIRED_MINOR}+ not found."
  if install_python_macos; then
    PYTHON="$(find_python)"
  fi
fi
if [[ -z "$PYTHON" ]]; then
  echo "Install Python 3.14 and re-run this script, or use pyenv: pyenv install 3.14"
  exit 1
fi

echo "Using: $PYTHON ($($PYTHON --version 2>&1))"
echo "Removing existing .venv (if any)..."
rm -rf .venv
echo "Creating .venv..."
"$PYTHON" -m venv .venv
echo "Installing dependencies..."
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements-dev.txt
.venv/bin/python scripts/check_python.py

echo ""
echo "Setup complete. Activate the venv with:"
echo "  source .venv/bin/activate"
echo "Then run tests:  PYTHONPATH=. python -m pytest tests/ -v"
echo "Or run the pipeline:  PYTHONPATH=. python -m src.polar_h10_stream | PYTHONPATH=. python -m src.hrv_calc | PYTHONPATH=. python -m src.graph_server --port 8765"
