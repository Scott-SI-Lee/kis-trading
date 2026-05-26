#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_PROFILE="${1:-local}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  echo
  echo "Stopping dev servers..."
  if [[ -n "${FRONTEND_PID}" ]] && kill -0 "${FRONTEND_PID}" 2>/dev/null; then
    kill "${FRONTEND_PID}" 2>/dev/null || true
  fi
  if [[ -n "${BACKEND_PID}" ]] && kill -0 "${BACKEND_PID}" 2>/dev/null; then
    kill "${BACKEND_PID}" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

echo "Starting backend: http://localhost:8000  (env: ${ENV_PROFILE})"
(
  cd "${ROOT_DIR}/backend"
  KIS_ENV_PROFILE="${ENV_PROFILE}" python3 -m uvicorn main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --reload \
    --reload-dir "${ROOT_DIR}/backend"
) &
BACKEND_PID=$!

echo "Starting frontend: http://localhost:${FRONTEND_PORT}"
(
  cd "${ROOT_DIR}/frontend"
  python3 -m http.server "${FRONTEND_PORT}"
) &
FRONTEND_PID=$!

echo
echo "Dashboard: http://localhost:${FRONTEND_PORT}"
echo "Press Ctrl+C to stop both servers."

while true; do
  if ! kill -0 "${BACKEND_PID}" 2>/dev/null; then
    echo "Backend process stopped."
    exit 1
  fi
  if ! kill -0 "${FRONTEND_PID}" 2>/dev/null; then
    echo "Frontend process stopped."
    exit 1
  fi
  sleep 2
done
