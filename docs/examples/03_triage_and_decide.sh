#!/usr/bin/env bash
# LongWatch example 3 — read the ranked queue and record one decision.
#
# Lists the queue, prints the top candidate's measured sentence, then records
# the decision and prints the ledger sequence number it got.
#
# Usage:
#   BASE_URL=http://localhost:8000 ./03_triage_and_decide.sh
#
# Set DECIDE=reject or DECIDE=flag to record a different action.
# A flag action also sends NOTE (default: a measured-observables sentence).
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
DECIDE="${DECIDE:-accept}"
if [ -z "${NOTE:-}" ]; then
  NOTE="motion held above the recording's p85 for the full span"
fi

lw_curl() {
  if [ -n "${LONGWATCH_AUTH_TOKEN:-}" ]; then
    curl -fsS -H "Authorization: Bearer ${LONGWATCH_AUTH_TOKEN}" "$@"
  else
    curl -fsS "$@"
  fi
}

echo "== GET ${BASE_URL}/api/v1/candidates?state=open&limit=5 =="
queue="$(lw_curl "${BASE_URL}/api/v1/candidates?state=open&limit=5")"

candidate_id="$(printf '%s' "$queue" | python3 -c '
import json, sys
entries = json.load(sys.stdin)["entries"]
candidates = [e for e in entries if e["kind"] == "candidate"]
if not candidates:
    print("")
else:
    top = candidates[0]
    print(top["candidate_id"])
    print("score %d · %s v%d" % (top["score"], top["pattern_slug"], top["pattern_version"]),
          file=sys.stderr)
    print("span %.0f-%.0fs · %s" % (top["start_s"], top["end_s"], top["source_name"]),
          file=sys.stderr)
    print("\"%s\"" % top["sentence"], file=sys.stderr)
')"

if [ -z "$candidate_id" ]; then
  echo "no open candidates — run 02_ingest_demo_and_watch.sh first" >&2
  exit 1
fi

echo
echo "== POST ${BASE_URL}/api/v1/candidates/${candidate_id}/decisions =="
body="$(python3 -c '
import json, sys
action = sys.argv[1]
note = sys.argv[2]
payload = {"action": action, "src_viewed": True}
if action == "flag":
    payload["note"] = note
print(json.dumps(payload))
' "$DECIDE" "$NOTE")"

printf 'request: %s\n' "$body"
body_file="$(mktemp)"
if [ -n "${LONGWATCH_AUTH_TOKEN:-}" ]; then
  http_code="$(curl -sS -o "$body_file" -w '%{http_code}' \
    -X POST "${BASE_URL}/api/v1/candidates/${candidate_id}/decisions" \
    -H 'Content-Type: application/json' \
    -H "Authorization: Bearer ${LONGWATCH_AUTH_TOKEN}" -d "$body")"
else
  http_code="$(curl -sS -o "$body_file" -w '%{http_code}' \
    -X POST "${BASE_URL}/api/v1/candidates/${candidate_id}/decisions" \
    -H 'Content-Type: application/json' -d "$body")"
fi
python3 -m json.tool < "$body_file"
rm -f "$body_file"

echo
case "$http_code" in
  200) echo "OK — the decision is saved and appended to the custody ledger." ;;
  422) echo "The request was refused with 422; the named reason above says why." ;;
  *)   echo "Unexpected HTTP ${http_code}." >&2; exit 1 ;;
esac
echo "Try the vocabulary guard: re-run with DECIDE=flag NOTE='this looks like cruelty' —"
echo "the API refuses with 422 vocabulary_breach and names the term."
