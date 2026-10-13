#!/usr/bin/env bash
# LongWatch container entrypoint: wait for the database → migrate (idempotent,
# the runner creates schema_migrations itself) → seed builtin patterns
# idempotently on (slug, version) → exec the server.
#
# No demo seeding happens here: demo data exists only behind the explicit
# operator action of §16.
set -euo pipefail

if [ -z "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL is required and is never defaulted" >&2
  exit 1
fi

PORT="${PORT:-8000}"
DATA_DIR="${LONGWATCH_DATA_DIR:-/data}"
mkdir -p "$DATA_DIR"/{inbox,originals,proxies,excerpts,exports,keys,tmp,fixtures} 2>/dev/null || true

# The image records the digest of every model it shipped beside the model file
# (the models stage's MODEL_SHA256 / FACE_MODEL_SHA256).  Export them so the
# detector and the face model verify at load that what runs is what shipped —
# an explicit operator value always wins.
MODELS_DIR="$(dirname "${LONGWATCH_MODEL_PATH:-/opt/longwatch/models/yolov8n.onnx}")"
if [ -z "${LONGWATCH_MODEL_SHA256:-}" ] && [ -s "$MODELS_DIR/MODEL_SHA256" ]; then
  LONGWATCH_MODEL_SHA256="$(cat "$MODELS_DIR/MODEL_SHA256")"
  export LONGWATCH_MODEL_SHA256
fi
if [ -z "${LONGWATCH_FACE_MODEL_SHA256:-}" ] && [ -s "$MODELS_DIR/FACE_MODEL_SHA256" ]; then
  LONGWATCH_FACE_MODEL_SHA256="$(cat "$MODELS_DIR/FACE_MODEL_SHA256")"
  export LONGWATCH_FACE_MODEL_SHA256
fi

# wait for the DB with a plain python loop (no netcat dependency)
python3 - <<'PY'
import os
import sys
import time

import psycopg

dsn = os.environ["DATABASE_URL"]
deadline = time.monotonic() + float(os.environ.get("LONGWATCH_DB_WAIT_S", "180"))
last = None
while time.monotonic() < deadline:
    try:
        conn = psycopg.connect(dsn, connect_timeout=5)
        conn.close()
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001 - a boot loop, not a failure
        last = exc
        time.sleep(2)
print(f"database did not become reachable: {last}", file=sys.stderr)
sys.exit(1)
PY

python3 -m longwatch migrate
exec python3 -m longwatch serve --host 0.0.0.0 --port "$PORT"
