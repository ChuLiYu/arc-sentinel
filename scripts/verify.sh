#!/usr/bin/env bash
# Canonical verification for Arc Sentinel (AGENTS.md rule).
# Runs ruff + full pytest suite (integration spins its own throwaway Postgres).
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
[ -x "$PY" ] || { echo "no .venv — run: uv sync"; exit 1; }

echo "==> ruff"
"$PY" -m ruff check app tests

echo "==> pytest (all suites; integration containers start as needed)"
"$PY" -m pytest tests/ -q

echo "==> verify OK"