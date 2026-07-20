#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

HOST="${API_HOST:-127.0.0.1}"
PORT="${API_PORT:-8001}"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -U pip
  .venv/bin/pip install -r requirements.txt
fi

echo "OmniParse Maintenance API → http://${HOST}:${PORT}"
echo "Docs: http://${HOST}:${PORT}/docs"
exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
