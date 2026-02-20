#!/usr/bin/env bash
# Keep laptop awake and run the full HRV pipeline. Ctrl+C stops everything.
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"
export PYTHONPATH="$ROOT"
if [ -x "$ROOT/.venv/bin/python" ]; then
  PYTHON="$ROOT/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

echo "Keeping system awake (caffeinate -di). Pipeline: polar_h10_stream | hrv_calc | graph_server --port 8765"
echo "Open http://localhost:8765 — Ctrl+C to stop."
echo ""

exec caffeinate -di env \
  PYTHONPATH="$ROOT" \
  "$PYTHON" -m src.polar_h10_stream \
  | env PYTHONPATH="$ROOT" "$PYTHON" -m src.hrv_calc \
  | env PYTHONPATH="$ROOT" "$PYTHON" -m src.graph_server --port 8765
