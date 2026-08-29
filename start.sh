#!/usr/bin/env bash
# OmniParse IDP UI Server (default port 8000)
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
echo "OmniParse IDP UI server running at http://localhost:${PORT}"
exec python3 -m http.server "$PORT"
