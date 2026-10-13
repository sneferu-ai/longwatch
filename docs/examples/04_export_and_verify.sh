#!/usr/bin/env bash
# LongWatch example 4 — cut a signed bundle, download it, verify it offline.
#
# Creates an export, waits for the job, downloads the ZIP, extracts it, and runs
# the bundle's own verifier plus the independent OpenSSL signature check.
#
# Usage:
#   BASE_URL=http://localhost:8000 ./04_export_and_verify.sh
#
# Requires: curl, python3 (stdlib only). `unzip` is not required — extraction
# uses `python3 -m zipfile`. The OpenSSL leg is skipped when openssl is absent
# or older than 3.0.
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
WORKDIR="${WORKDIR:-$(mktemp -d)}"
mkdir -p "$WORKDIR"

lw_curl() {
  if [ -n "${LONGWATCH_AUTH_TOKEN:-}" ]; then
    curl -fsS -H "Authorization: Bearer ${LONGWATCH_AUTH_TOKEN}" "$@"
  else
    curl -fsS "$@"
  fi
}

echo "== POST ${BASE_URL}/api/v1/exports =="
created="$(lw_curl -X POST "${BASE_URL}/api/v1/exports" \
  -H 'Content-Type: application/json' \
  -d '{"states": ["accepted", "flagged"], "cut_precision": "keyframe"}')"
printf '%s\n' "$created" | python3 -m json.tool

export_id="$(printf '%s' "$created" | python3 -c 'import json,sys; print(json.load(sys.stdin)["export_id"])')"
job_id="$(printf '%s' "$created" | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])')"

echo
echo "== waiting for export job ${job_id} =="
for _ in $(seq 1 200); do
  line="$(lw_curl "${BASE_URL}/api/v1/jobs/${job_id}" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("%-9s %-7s %3d%%" % (d["state"], d["stage"] or "-", d["pct"]))
')"
  echo "  ${line}"
  case "$line" in
    succeeded*) break ;;
    failed*) echo "export failed — the error column names the cause:" >&2
             lw_curl "${BASE_URL}/api/v1/jobs/${job_id}" >&2; exit 1 ;;
  esac
  sleep 2
done

echo
echo "== downloading ${BASE_URL}/api/v1/exports/${export_id}/download =="
zip_path="${WORKDIR}/longwatch-export-${export_id:0:8}.zip"
lw_curl -o "$zip_path" "${BASE_URL}/api/v1/exports/${export_id}/download"
echo "  wrote ${zip_path} ($(wc -c < "$zip_path") bytes)"

echo
echo "== extracting =="
python3 -m zipfile -e "$zip_path" "${WORKDIR}/bundle"
bundle_dir="$(find "${WORKDIR}/bundle" -mindepth 1 -maxdepth 1 -type d | head -1)"
echo "  bundle directory: ${bundle_dir}"
ls -1 "${bundle_dir}"

echo
echo "== verifying from the bundle alone: python3 verify_bundle.py . =="
(
  cd "$bundle_dir"
  python3 verify_bundle.py .
)
echo "  verify_bundle.py exit code: 0 (verified)"

echo
echo "== independent signature check (OpenSSL >= 3.0) =="
if command -v openssl >/dev/null 2>&1 && openssl version | grep -qE 'OpenSSL (3|4)'; then
  (
    cd "$bundle_dir"
    openssl pkeyutl -verify -pubin -inkey public.pem -rawin \
      -in manifest.canonical -sigfile manifest.sig
  )
else
  echo "  openssl >= 3.0 not found — skipped (the python verifier above already checked the signature)"
fi

echo
echo "== checksum recomputation =="
(
  cd "$bundle_dir"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum --check checksums.sha256 >/dev/null && echo "  checksums.sha256 recomputes clean"
  else
    python3 - <<'PY'
import hashlib, pathlib
for line in pathlib.Path("checksums.sha256").read_text().splitlines():
    digest, name = line.split("  ", 1)
    actual = hashlib.sha256(pathlib.Path(name).read_bytes()).hexdigest()
    assert actual == digest, f"mismatch: {name}"
print("  checksums.sha256 recomputes clean (python leg)")
PY
  fi
)

echo
echo "Bundle verified. Remember the one thing it does not prove: compare the key"
echo "fingerprint printed above with the operator out of band before trusting it."
