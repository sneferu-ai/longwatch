#!/usr/bin/env bash
# Pin the model artifacts the Dockerfile's models stage fetches.
#
# Run this once on a machine with network access: it fetches each artifact from
# its canonical URL (the first candidate that answers, exactly as the image
# build does), prints its SHA-256, and — with --write — records the digests in
# models/pins.json.  Commit that file; the no-build-args image build then
# verifies both artifacts and stamps MODEL_PROVENANCE=fetched.  Until then the
# build still fetches the real weights but stamps fetched_unpinned, and no
# surface claims a verification that did not run.
#
# Usage:  scripts/pin_models.sh [--write]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PINS="$REPO_ROOT/models/pins.json"
WRITE=0
case "${1:-}" in
  --write) WRITE=1 ;;
  "") ;;
  *) echo "usage: pin_models.sh [--write]" >&2; exit 2 ;;
esac

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

read_pins() { python3 -c "$1" "$PINS" "${2:-}"; }

mapfile -t NAMES < <(read_pins 'import json,sys
for row in json.load(open(sys.argv[1]))["models"]:
    print(row["name"])')

for name in "${NAMES[@]}"; do
  mapfile -t URLS < <(read_pins 'import json,sys
row = next(r for r in json.load(open(sys.argv[1]))["models"] if r["name"] == sys.argv[2])
for url in row.get("urls", []):
    print(url)' "$name")
  fetched=""
  for url in "${URLS[@]}"; do
    echo "fetching $name from $url"
    if curl -fsSL --retry 3 --max-time 600 -o "$WORK/$name" "$url"; then
      fetched="$url"
      break
    fi
    echo "  fetch failed: $url" >&2
  done
  if [ -z "$fetched" ]; then
    echo "no URL answered for $name — pins unchanged" >&2
    exit 1
  fi
  digest="$(sha256sum "$WORK/$name" | cut -d' ' -f1)"
  echo "$name sha256=$digest"
  printf '%s\t%s\n' "$name" "$digest" >> "$WORK/digests.tsv"
done

if [ "$WRITE" = "1" ]; then
  python3 - "$PINS" "$WORK/digests.tsv" <<'PY'
import json
import sys

pins_path, digests_path = sys.argv[1], sys.argv[2]
digests = {}
with open(digests_path, encoding="utf-8") as handle:
    for line in handle:
        name, digest = line.split()
        digests[name] = digest
with open(pins_path, encoding="utf-8") as handle:
    document = json.load(handle)
for row in document["models"]:
    if row["name"] in digests:
        row["sha256"] = digests[row["name"]]
with open(pins_path, "w", encoding="utf-8") as handle:
    json.dump(document, handle, indent=2)
    handle.write("\n")
print(f"wrote {pins_path}")
PY
else
  echo "run with --write to record these digests in models/pins.json"
fi
