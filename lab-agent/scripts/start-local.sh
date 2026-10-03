#!/usr/bin/env bash
set -euo pipefail

project_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_dir"

web_build_is_stale() {
  [[ ! -f "$project_dir/web/dist/index.html" ]] ||
    find "$project_dir/web/src" \
      "$project_dir/web/index.html" \
      "$project_dir/web/package.json" \
      "$project_dir/web/package-lock.json" \
      "$project_dir/web/vite.config.ts" \
      -type f -newer "$project_dir/web/dist/index.html" -print -quit \
      | grep -q .
}

if [[ ! -x "$project_dir/.venv/bin/python" ]]; then
  "$project_dir/scripts/bootstrap-local.sh"
elif web_build_is_stale; then
  command -v npm >/dev/null 2>&1 || {
    echo "npm is required to rebuild the web application." >&2
    exit 1
  }
  (cd "$project_dir/web" && npm ci && npm run build)
fi

port="${LAB_AGENT_PORT:-8000}"
if [[ ! "$port" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
  echo "LAB_AGENT_PORT must be a valid TCP port: $port" >&2
  exit 1
fi

listener_pids() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -ti "TCP:$port" -sTCP:LISTEN 2>/dev/null || true
  elif command -v fuser >/dev/null 2>&1; then
    fuser -n tcp "$port" 2>/dev/null | tr ' ' '\n' || true
  else
    ss -ltnp "sport = :$port" 2>/dev/null \
      | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' \
      | sort -u
  fi
}

existing_pids=$(listener_pids)
if [[ -n "$existing_pids" ]]; then
  echo "Stopping existing listener(s) on port $port: $existing_pids"
  kill $existing_pids 2>/dev/null || true
  for _ in {1..20}; do
    [[ -z "$(listener_pids)" ]] && break
    sleep 0.1
  done
  remaining_pids=$(listener_pids)
  if [[ -n "$remaining_pids" ]]; then
    echo "Force-stopping remaining listener(s): $remaining_pids" >&2
    kill -KILL $remaining_pids 2>/dev/null || true
  fi
fi

exec "$project_dir/.venv/bin/python" -m uvicorn app.main:app --host "${LAB_AGENT_HOST:-0.0.0.0}" --port "$port"
