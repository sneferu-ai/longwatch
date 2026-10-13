#!/usr/bin/env python3
"""LongWatch example 5 — watch the custody ledger from Python (stdlib only).

Fetches the full ledger verification and the operator status surface, prints a
short summary, and exits non-zero when the chain is broken — the shape you would
wire into a monitoring check.

Usage:
    BASE_URL=http://localhost:8000 python3 05_verify_ledger.py
    LONGWATCH_AUTH_TOKEN=… BASE_URL=http://localhost:8000 python3 05_verify_ledger.py

Requires: Python 3.9+ (urllib is stdlib). No third-party packages.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
TOKEN = os.environ.get("LONGWATCH_AUTH_TOKEN", "")


def get(path: str) -> dict:
    """GET one API route; raises SystemExit with the server's error detail."""
    request = urllib.request.Request(BASE_URL + path)
    if TOKEN:
        request.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"GET {path} -> HTTP {exc.code}: {detail}") from exc


def main() -> int:
    verify = get("/api/v1/ledger/verify")
    status = get("/api/v1/system/status")

    print("ledger:")
    print(f"  ok:               {verify['ok']}")
    print(f"  rows:             {verify['length']}")
    print(f"  head hash:        {verify['head_hash'][:16]}…")
    print(f"  first divergence: {verify['first_divergence']}")

    print("service:")
    print(f"  database:         {status['database']}")
    print(f"  model ok:         {status['model'].get('ok')} "
          f"(provenance: {status['model'].get('provenance')})")
    print(f"  media worker:     {status['workers']['media']['alive']}")
    print(f"  utility worker:   {status['workers']['utility']['alive']}")
    print(f"  unacked ml-failed windows: {status['ml_failed_count']}")

    if not verify["ok"]:
        print(f"\nLEDGER CORRUPTION at seq {verify['first_divergence']} — "
              f"do not mutate the table; report immediately.", file=sys.stderr)
        return 1

    print("\nOK — the custody ledger chain verifies clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
