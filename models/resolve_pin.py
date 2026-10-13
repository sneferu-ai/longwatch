#!/usr/bin/env python3
"""Resolve a model artifact's candidate URLs and digest for the models stage.

Precedence: an explicit build-arg wins; otherwise the value comes from
``models/pins.json`` beside this file; otherwise nothing is printed (the stage
treats no URL as "no fetch" and an empty digest as "unpinned").  The build
never invents a digest: an empty pin stays empty and the stage records
``fetched_unpinned`` rather than claiming a verification that did not run.

``url`` prints every candidate URL on its own line, in preference order; the
stage tries them until one answers.

Usage:  resolve_pin.py <artifact-name> <url|sha256> [build-arg-value]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PINS = Path(__file__).resolve().parent / "pins.json"


def resolve(name: str, field: str, override: str = "") -> list[str]:
    if override.strip():
        return [override.strip()]
    try:
        document = json.loads(PINS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    for row in document.get("models", []):
        if row.get("name") != name:
            continue
        if field == "url":
            urls = row.get("urls") or ([row["url"]] if row.get("url") else [])
            return [str(url).strip() for url in urls if str(url).strip()]
        return [str(row.get(field, "") or "").strip()]
    return []


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    for line in resolve(argv[1], argv[2], argv[3] if len(argv) > 3 else ""):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
