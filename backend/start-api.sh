#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

HOST="${API_HOST:-127.0.0.1}"
PORT="${API_PORT:-8001}"

# Load environment configuration if present
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ ! -d .venv ]]; then
  echo "Setting up Python virtual environment..."
  python3 -m venv .venv
  .venv/bin/pip install -U pip
  .venv/bin/pip install -r requirements.txt
fi

echo "Starting OmniParse API server on http://${HOST}:${PORT}"
echo "API Docs: http://${HOST}:${PORT}/docs"

if [[ "${API_RELOAD:-0}" == "1" ]]; then
  exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
fi

exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT"
