#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

echo "Stopping dev servers..."

kill_port() {
  local port="$1"
  local pids
  pids="$(lsof -ti tcp:"${port}" 2>/dev/null || true)"
  if [[ -n "${pids}" ]]; then
    echo "Killing processes on port ${port}: ${pids}"
    kill ${pids} 2>/dev/null || true
  fi
}

kill_project_processes() {
  local pattern="$1"
  local pids
  pids="$(pgrep -f "${pattern}" 2>/dev/null || true)"
  if [[ -n "${pids}" ]]; then
    echo "Killing processes matching '${pattern}': ${pids}"
    kill ${pids} 2>/dev/null || true
  fi
}

kill_port "${BACKEND_PORT}"
kill_port "${FRONTEND_PORT}"

kill_project_processes "${ROOT_DIR}/backend/main.py"
kill_project_processes "${ROOT_DIR}/frontend"
kill_project_processes "uvicorn main:app"
kill_project_processes "python3 -m http.server ${FRONTEND_PORT}"

echo "Done."
