#!/usr/bin/env bash
# Local development runner for OmniParse IDP (UI + Backend API)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

UI_PORT="${PORT:-8000}"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8001}"

# Ensure .env exists from template
if [[ ! -f backend/.env ]]; then
  if [[ -f .env.example ]]; then
    echo "Creating backend/.env from .env.example template..."
    cp .env.example backend/.env
  else
    echo "Error: .env.example template is missing."
    exit 1
  fi
fi

# Load environment configuration
set -a
# shellcheck disable=SC1091
source backend/.env
set +a

# Free up ports if previous instances were running
for port in "$UI_PORT" "$API_PORT"; do
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${pids}" ]]; then
    echo "Stopping existing process on port :${port}"
    kill ${pids} 2>/dev/null || true
    sleep 0.5
  fi
done

# Backend setup
if [[ ! -d backend/.venv ]]; then
  echo "Setting up Python virtual environment..."
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install -U pip
  backend/.venv/bin/pip install -r backend/requirements.txt
fi

echo "Starting Backend API on http://${API_HOST}:${API_PORT}..."
(
  cd backend
  exec .venv/bin/uvicorn app.main:app --host "$API_HOST" --port "$API_PORT"
) > /tmp/omniparse-api.log 2>&1 &
API_PID=$!

echo "Starting Frontend UI on http://localhost:${UI_PORT}..."
(
  exec python3 -m http.server "$UI_PORT"
) > /tmp/omniparse-ui.log 2>&1 &
UI_PID=$!

cleanup() {
  echo "Shutting down servers..."
  kill "$API_PID" "$UI_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Wait for backend health check
for i in $(seq 1 30); do
  if curl -fsS "http://${API_HOST}:${API_PORT}/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.4
done

if ! curl -fsS "http://${API_HOST}:${API_PORT}/api/health" >/dev/null 2>&1; then
  echo "Backend API failed to start. Last log lines:"
  tail -n 30 /tmp/omniparse-api.log || true
  exit 1
fi

echo ""
echo "OmniParse IDP is running:"
echo "  UI:   http://localhost:${UI_PORT}"
echo "  API:  http://${API_HOST}:${API_PORT}/docs"
echo ""
echo "Press Ctrl+C to stop both servers."

wait
