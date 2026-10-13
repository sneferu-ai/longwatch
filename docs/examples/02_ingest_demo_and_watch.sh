#!/usr/bin/env bash
# LongWatch example 2 — bring footage in and watch the ingest job finish.
#
# Seeds the labeled synthetic demo fixture (idempotent — safe to re-run),
# then polls the ingest job until it succeeds or fails.
#
# Usage:
#   BASE_URL=http://localhost:8000 ./02_ingest_demo_and_watch.sh
#
# To ingest a real file instead, replace the seed call with:
#   curl -F "file=@/path/to/footage.mp4" "$BASE_URL/api/v1/uploads"
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"

lw_curl() {
  if [ -n "${LONGWATCH_AUTH_TOKEN:-}" ]; then
    curl -fsS -H "Authorization: Bearer ${LONGWATCH_AUTH_TOKEN}" "$@"
  else
    curl -fsS "$@"
  fi
}

echo "== POST ${BASE_URL}/api/v1/demo/seed =="
seed="$(lw_curl -X POST "${BASE_URL}/api/v1/demo/seed")"
printf '%s\n' "$seed" | python3 -m json.tool

source_id="$(printf '%s' "$seed" | python3 -c '
import json, sys
print(json.load(sys.stdin)["source_id"])
')"

job_id="$(printf '%s' "$seed" | python3 -c '
import json, sys
print(json.load(sys.stdin).get("job_id", ""))
')"

if [ -n "$job_id" ]; then
  echo
  echo "== polling GET ${BASE_URL}/api/v1/jobs/${job_id} =="
  for _ in $(seq 1 120); do
    line="$(lw_curl "${BASE_URL}/api/v1/jobs/${job_id}" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("%-9s %-8s %3d%%" % (d["state"], d["stage"] or "-", d["pct"]))
')"
    echo "  ${line}"
    case "$line" in
      succeeded*) break ;;
      failed*) echo "ingest job failed — the job detail names the cause:" >&2
               lw_curl "${BASE_URL}/api/v1/jobs/${job_id}" >&2; exit 1 ;;
    esac
    sleep 2
  done
else
  echo
  echo "an intact demo source already exists — nothing new to ingest."
fi

echo
echo "== GET ${BASE_URL}/api/v1/sources (this source) =="
lw_curl "${BASE_URL}/api/v1/sources" | python3 -c '
import json, sys
source_id = sys.argv[1]
for row in json.load(sys.stdin)["sources"]:
    if row["id"] == source_id:
        print("name:       ", row["original_name"])
        print("state:      ", row["state"])
        print("origin:     ", row["origin"])
        print("duration_s: ", row["duration_s"])
        print("windows:    ", row["window_count"])
        print("pending:    ", row["pending_jobs"])
        break
else:
    print("source not found in the list — unexpected", file=sys.stderr)
    sys.exit(1)
' "$source_id"

echo
echo "OK — source ${source_id} is ingested. Open Triage to review its queue."
