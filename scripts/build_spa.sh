#!/usr/bin/env bash
# Build the SPA into longwatch/static/ (the same output the Dockerfile's spa
# stage produces).  Run this before `python -m longwatch serve` from source.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -d frontend/node_modules ]; then
  (cd frontend && npm ci)
fi
(cd frontend && npm run build)
echo "SPA built into longwatch/static/"
