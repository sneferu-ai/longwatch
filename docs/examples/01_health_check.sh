#!/usr/bin/env bash
# LongWatch example 1 — is the service up, and is it ready to rank?
#
# Checks /healthz (database + detector) and prints the operator status surface.
#
# Usage:
#   BASE_URL=http://localhost:8000 ./01_health_check.sh
#   LONGWATCH_AUTH_TOKEN=… BASE_URL=http://localhost:8000 ./01_health_check.sh
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"

lw_curl() {
  if [ -n "${LONGWATCH_AUTH_TOKEN:-}" ]; then
    curl -fsS -H "Authorization: Bearer ${LONGWATCH_AUTH_TOKEN}" "$@"
  else
    curl -fsS "$@"
  fi
}

echo "== GET ${BASE_URL}/healthz =="
health="$(curl -fsS "${BASE_URL}/healthz")"
printf '%s\n' "$health" | python3 -m json.tool

printf '%s' "$health" | python3 -c '
import json, sys
ok = json.load(sys.stdin)["ok"]
sys.exit(0 if ok else 1)
' || { echo "healthz reported ok=false — fix the named check before continuing" >&2; exit 1; }

echo
echo "== GET ${BASE_URL}/api/v1/system/status (selected fields) =="
lw_curl "${BASE_URL}/api/v1/system/status" | python3 -c '
import json, sys
s = json.load(sys.stdin)
print("database:        ", s["database"])
print("model ok:        ", s["model"].get("ok"), "provenance:", s["model"].get("provenance"))
print("workers media:   ", s["workers"]["media"]["alive"], "| utility:", s["workers"]["utility"]["alive"])
print("offline guard:   ", s["offline_guard"]["installed"], "|", s["offline_guard"]["sanctioned_egress"])
print("ml failed (unacked):", s["ml_failed_count"])
print("key fingerprint: ", s["key"].get("fingerprint"))
print("export defaults: ", s.get("export_defaults"))
'

echo
echo "OK — the service is up and the detector passed its contract check."
