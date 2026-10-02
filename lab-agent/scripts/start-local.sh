#!/usr/bin/env bash
set -euo pipefail

project_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_dir"
"$project_dir/scripts/bootstrap-local.sh"
exec "$project_dir/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port "${LAB_AGENT_PORT:-8000}"
