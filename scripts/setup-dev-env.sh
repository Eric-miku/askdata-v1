#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" == "Darwin" ]]; then
  if [[ -e .venv && ! -L .venv ]]; then
    rm -rf .venv
  elif [[ -L .venv && "$(readlink .venv)" != "venv.nosync" ]]; then
    rm .venv
  fi
  mkdir -p venv.nosync
  if [[ ! -L .venv ]]; then
    ln -s venv.nosync .venv
  fi
fi

uv sync
uv run askdata --help >/dev/null

echo "AskData environment is ready. Use: uv run askdata --help"
