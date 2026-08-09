#!/usr/bin/env bash
# One-command local run for OmniParse IDP (UI + API).
# Usage: ./dev-local.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

UI_PORT="${PORT:-8000}"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8001}"

# Ensure backend/.env exists (copy from training env or example)
if [[ ! -f backend/.env ]]; then
  if [[ -f .tmp-bogel-train/.env ]]; then
    echo "Creating backend/.env from .tmp-bogel-train/.env"
    {
      echo "# Local testing only — do not commit"
      grep -E '^(GEMINI_API_KEY|GEMINI_MODEL)=' .tmp-bogel-train/.env || true
      echo "AUTH_REQUIRED=false"
      echo "CORS_ORIGINS=http://localhost:${UI_PORT},http://127.0.0.1:${UI_PORT}"
      echo "API_HOST=${API_HOST}"
      echo "API_PORT=${API_PORT}"
    } > backend/.env
  else
    echo "Missing backend/.env"
    echo "Copy backend/.env.example → backend/.env and set GEMINI_API_KEY=..."
    exit 1
  fi
fi

# Load .env (GEMINI_API_KEY optional — login/auth works without it; extract needs a key later).
set -a
# shellcheck disable=SC1091
source backend/.env
set +a

if [[ -z "${GEMINI_API_KEY:-}" ]]; then
  echo "Note: GEMINI_API_KEY empty — login/admin work; PDF extract needs a key later."
fi

# Free ports if stale servers are stuck
for port in "$UI_PORT" "$API_PORT"; do
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${pids}" ]]; then
    echo "Stopping old process on :${port}"
    kill ${pids} 2>/dev/null || true
    sleep 0.5
  fi
done

# API venv
if [[ ! -d backend/.venv ]]; then
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install -U pip
  backend/.venv/bin/pip install -r backend/requirements.txt
fi

echo ""
echo "Starting API → http://${API_HOST}:${API_PORT}"
(
  cd backend
  # No --reload by default: reload clears in-memory jobs and breaks extractions.
  if [[ "${API_RELOAD:-0}" == "1" ]]; then
    exec .venv/bin/uvicorn app.main:app --host "$API_HOST" --port "$API_PORT" --reload
  fi
  exec .venv/bin/uvicorn app.main:app --host "$API_HOST" --port "$API_PORT"
) > /tmp/omniparse-api.log 2>&1 &
API_PID=$!

echo "Starting UI  → http://localhost:${UI_PORT}"
(
  exec python3 -m http.server "$UI_PORT"
) > /tmp/omniparse-ui.log 2>&1 &
UI_PID=$!

cleanup() {
  kill "$API_PID" "$UI_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Wait for health
for i in $(seq 1 30); do
  if curl -fsS "http://${API_HOST}:${API_PORT}/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.4
done

if ! curl -fsS "http://${API_HOST}:${API_PORT}/api/health" >/dev/null 2>&1; then
  echo "API failed to start. Log:"
  tail -n 40 /tmp/omniparse-api.log || true
  exit 1
fi

KEY_LEN="$(python3 - <<'PY'
import os
print(len(os.environ.get("GEMINI_API_KEY","").strip()))
PY
)"

echo ""
echo "Ready."
echo "  UI:   http://localhost:${UI_PORT}"
echo "  API:  http://${API_HOST}:${API_PORT}/docs"
if [[ "$KEY_LEN" -gt 0 ]]; then
  echo "  Key:  loaded (${KEY_LEN} chars)"
else
  echo "  Key:  none (login/admin OK; extract needs GEMINI_API_KEY later)"
fi
echo "  Login: admin@omniparse.local / ChangeMeNow!"
echo ""
echo "Ctrl+C stops both servers."
echo ""

wait
