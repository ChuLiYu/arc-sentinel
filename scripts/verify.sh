#!/usr/bin/env bash
# Canonical verification for Arc Sentinel (AGENTS.md rule).
# Runs ruff + full pytest suite (integration spins its own throwaway Postgres).
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
[ -x "$PY" ] || { echo "no .venv — run: uv sync"; exit 1; }

echo "==> project documentation"
"$PY" scripts/project_check.py

echo "==> ruff"
"$PY" -m ruff check app tests scripts/project_check.py

echo "==> pytest (all suites; integration containers start as needed)"
docker info > /dev/null
"$PY" -m pytest tests/ -q

echo "==> verify OK"
