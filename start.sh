#!/usr/bin/env bash
# Maintenance IDP local UI server (port 8000; invoice app uses 8080)
cd "$(dirname "$0")"
PORT="${PORT:-8000}"
echo "OmniParse Maintenance IDP → http://localhost:${PORT}"
exec python3 -m http.server "$PORT"
