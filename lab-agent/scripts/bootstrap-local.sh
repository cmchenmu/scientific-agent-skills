#!/usr/bin/env bash
set -euo pipefail

project_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_dir"

command -v uv >/dev/null 2>&1 || {
  echo "uv is required. Install it from https://docs.astral.sh/uv/" >&2
  exit 1
}

uv venv --clear .venv --python 3.14
uv pip install --python .venv/bin/python -e . pytest pytest-asyncio ruff

if command -v npm >/dev/null 2>&1; then
  (cd web && npm ci && npm run build)
else
  echo "npm is unavailable; API dependencies are ready but the browser bundle was skipped." >&2
fi

mkdir -p data
.venv/bin/python -c "from app.demo import ensure_demo_data; from pathlib import Path; ensure_demo_data(Path('data/local-archive')); print('Local demo data is ready.')"
