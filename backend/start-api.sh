#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

HOST="${API_HOST:-127.0.0.1}"
PORT="${API_PORT:-8001}"

# Load local secrets for testing (GEMINI_API_KEY etc.)
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ -z "${GEMINI_API_KEY:-}" ]]; then
  echo "WARNING: GEMINI_API_KEY is empty. Put it in backend/.env before extracting."
fi

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -U pip
  .venv/bin/pip install -r requirements.txt
fi

echo "OmniParse Maintenance API → http://${HOST}:${PORT}"
echo "Docs: http://${HOST}:${PORT}/docs"
# Default: no --reload (reload wipes in-memory extract jobs mid-run).
# Set API_RELOAD=1 for development auto-reload.
if [[ "${API_RELOAD:-0}" == "1" ]]; then
  exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
fi
exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT"
