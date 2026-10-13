#!/usr/bin/env python3
"""Verify a LongWatch export bundle from the bundle alone.

Pure standard library (``hashlib``, ``json``, ``csv``) plus the in-repo,
MIT-licensed ``ed25519_verifier.py`` that ships beside this file.  No
database, no Settings page, no trust in the vendor is required.

Checks performed, in order:

1. every file digest matches the signed manifest's ``files`` map — including
   this script and ``ed25519_verifier.py``;
2. ``manifest.json`` re-canonicalizes byte-equal to ``manifest.canonical``;
3. the Ed25519 signature over ``manifest.canonical`` verifies (exit 2 with
   "SIGNATURE NOT CHECKED" when the verifier cannot be loaded);
4. the ``ledger.jsonl`` chain walks clean from the byte-exact preimage;
5. ``candidates.csv`` agrees with the manifest's candidate list;
6. ``checksums.sha256`` recomputes.

The trust root, stated honestly: the signature proves the bundle's contents are
exactly what the signing key signed and have not changed since.  It does **not**
prove the key belongs to the operator.  Compare the key fingerprint
(SHA-256 of ``public.pem``, shown in LongWatch's Settings page and printed at
startup) with the operator over a channel of your choosing before trusting
anything.

Usage:  python3 verify_bundle.py [bundle_dir]
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER_VERSION = "longwatch-ledger-v1"
GENESIS_PREV = "0" * 64

OK = "PASS"
FAIL = "FAIL"


class Report:
    def __init__(self) -> None:
        self.checks: list[tuple[str, str, str]] = []
        self.exit_code = 0
        # Once the signature is declared un-checkable the run is *not verified*,
        # and that is a different, stronger statement than "a check failed":
        # no later check may overwrite exit 2 with 1 (the bundle is unverified
        # either way, but a caller must be able to tell "not checked" from
        # "checked and failed").
        self.signature_checked = True

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append((name, OK if ok else FAIL, detail))
        if not ok and self.exit_code == 0:
            self.exit_code = 1
        return ok

    def print(self) -> None:
        for name, status, detail in self.checks:
            line = f"[{status}] {name}"
            if detail:
                line += f" — {detail}"
            print(line)


def canonical_json(document) -> str:
    """The canonical form of §7.1 — reproduced here so the bundle is standalone."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def load_verifier():
    """Import the in-repo verify-only Ed25519 implementation that ships here."""
    try:
        import importlib.util

        path = os.path.join(HERE, "ed25519_verifier.py")
        spec = importlib.util.spec_from_file_location("longwatch_ed25519_verifier", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def check_files(bundle: str, manifest: dict, report: Report) -> None:
    files = manifest.get("files") or {}
    missing, mismatched = [], []
    for name, digest in sorted(files.items()):
        path = os.path.join(bundle, name)
        if not os.path.isfile(path):
            missing.append(name)
            continue
        if sha256_file(path) != digest:
            mismatched.append(name)
    report.add("every file digest matches the signed manifest",
               not missing and not mismatched,
               (f"missing: {missing} " if missing else "") +
               (f"mismatched: {mismatched}" if mismatched else "") or f"{len(files)} files")
    # nothing may ride along unlisted: an unlisted file is exactly how an
    # un-pixelated excerpt could smuggle itself into a pixelated bundle
    manifest_artifacts = {"manifest.json", "manifest.canonical", "manifest.sig",
                          "public.pem", "checksums.sha256"}
    on_disk = {
        os.path.relpath(os.path.join(root, name), bundle).replace(os.sep, "/")
        for root, _dirs, names in os.walk(bundle)
        for name in names
    }
    unlisted = sorted(on_disk - set(files) - manifest_artifacts)
    report.add("no unlisted file rides in the bundle", not unlisted,
               f"unlisted: {unlisted}" if unlisted else f"{len(on_disk)} files")
    for script in ("verify_bundle.py", "ed25519_verifier.py"):
        report.add(f"verifier {script} digest is signed",
                   script in files, f"sha256 {files.get(script, 'ABSENT')[:16]}")


def check_manifest_bytes(bundle: str, report: Report) -> dict | None:
    pretty_path = os.path.join(bundle, "manifest.json")
    canonical_path = os.path.join(bundle, "manifest.canonical")
    if not os.path.isfile(pretty_path) or not os.path.isfile(canonical_path):
        report.add("manifest.json and manifest.canonical exist", False)
        return None
    try:
        with open(pretty_path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as exc:
        report.add("manifest.json parses as JSON", False, f"{type(exc).__name__}: {exc}")
        return None
    if not isinstance(document, dict):
        report.add("manifest.json is a JSON object", False, type(document).__name__)
        return None
    with open(canonical_path, "rb") as handle:
        expected = handle.read()
    actual = canonical_json(document).encode("utf-8")
    report.add("manifest.json re-canonicalizes byte-equal to manifest.canonical",
               actual == expected,
               f"{len(actual)} bytes" if actual == expected else
               f"canonical {len(expected)} bytes vs re-canonicalized {len(actual)} bytes")
    return document


def check_signature(bundle: str, manifest: dict, report: Report) -> None:
    verifier = load_verifier()
    if verifier is None:
        # The signature cannot be checked: the bundle is NOT VERIFIED, and that
        # verdict outranks any later failed check (exit 2, never 1).
        report.checks.append(("Ed25519 signature", FAIL, "SIGNATURE NOT CHECKED"))
        report.signature_checked = False
        report.exit_code = 2
        return
    with open(os.path.join(bundle, "manifest.canonical"), "rb") as handle:
        message = handle.read()
    with open(os.path.join(bundle, "manifest.sig"), "rb") as handle:
        signature = handle.read()  # raw 64 bytes: openssl -sigfile reads raw
    pem_path = os.path.join(bundle, "public.pem")
    with open(pem_path, "r", encoding="utf-8") as handle:
        pem = handle.read()
    try:
        public_key = verifier._load_public_key(pem)
    except Exception as exc:  # noqa: BLE001
        report.add("public.pem parses", False, str(exc))
        return
    fingerprint = hashlib.sha256(open(pem_path, "rb").read()).hexdigest()
    report.add("Ed25519 signature verifies over manifest.canonical",
               verifier.verify(public_key, message, signature),
               f"key fingerprint (SHA-256 of public.pem) {fingerprint}")
    declared = manifest.get("public_key_sha256")
    report.add("the shipped public.pem is the key the manifest names",
               declared is not None and fingerprint == declared,
               "compare this fingerprint with the operator out of band before trusting it")


def check_ledger(bundle: str, manifest: dict, report: Report) -> None:
    path = os.path.join(bundle, "ledger.jsonl")
    if not os.path.isfile(path):
        report.add("ledger.jsonl exists", False)
        return
    prev = GENESIS_PREV
    count = 0
    first_divergence = None
    malformed = None
    with open(path, "r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                preimage = (
                    f"{LEDGER_VERSION}|{int(row['seq'])}|{row['prev_hash']}|{row['event_type']}|"
                    f"{row['actor']}|{int(row['recorded_at_ms'])}|{canonical_json(row['payload'])}"
                )
            except (ValueError, KeyError, TypeError) as exc:
                # A truncated or hand-edited transcript is exactly what this
                # walker exists to catch: name the row, never raise.
                malformed = f"line {lineno}: {type(exc).__name__}: {exc}"
                break
            count += 1
            recomputed = hashlib.sha256(preimage.encode("utf-8")).hexdigest()
            if first_divergence is None and (row["prev_hash"] != prev or recomputed != row["row_hash"]):
                first_divergence = int(row["seq"])
            prev = row["row_hash"]
    if malformed is not None:
        report.add("ledger.jsonl rows parse and carry the preimage fields", False, malformed)
        return
    head_ok = count > 0 and prev == manifest.get("head_hash")
    report.add("ledger.jsonl chain walks clean",
               first_divergence is None and head_ok,
               f"{count} rows, head {prev[:16]}"
               + (f", first divergence at seq {first_divergence}" if first_divergence else ""))
    try:
        declared_rows = int(manifest.get("ledger_head_seq"))
    except (TypeError, ValueError):
        declared_rows = None
    report.add("ledger head matches the manifest",
               declared_rows == count and head_ok,
               f"manifest says {manifest.get('ledger_head_seq')}, transcript has {count}")


def check_csv(bundle: str, manifest: dict, report: Report) -> None:
    path = os.path.join(bundle, "candidates.csv")
    if not os.path.isfile(path):
        report.add("candidates.csv exists", False)
        return
    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        # the columns live in the header, not the first data row — a legitimate
        # export whose states filter matched nothing still proves its columns
        columns = set(reader.fieldnames or [])
        rows = list(reader)
    candidates = manifest.get("candidates") or []
    required = {"decision_state", "reviewer", "src_viewed", "synthetic", "score", "pattern_slug"}
    report.add("candidates.csv row count equals the manifest's candidates",
               len(rows) == len(candidates), f"{len(rows)} rows vs {len(candidates)} candidates")
    report.add("candidates.csv carries decision_state/reviewer/src_viewed/synthetic",
               required.issubset(columns), f"columns: {sorted(columns)}")
    if rows and candidates:
        # A malformed row (a truncated CSV, a hand-edited score) must read as a
        # named FAIL, never as a traceback: this check exists to catch exactly
        # that tampering.
        by_id = {str(c.get("candidate_id")): c for c in candidates if isinstance(c, dict)}
        try:
            agreed = all(
                str(row.get("candidate_id")) in by_id
                and int(row["score"]) == int(by_id[str(row["candidate_id"])]["score"])
                for row in rows
            )
            detail = ""
        except (KeyError, TypeError, ValueError) as exc:
            agreed = False
            detail = f"malformed row: {type(exc).__name__}: {exc}"
        report.add("candidates.csv agrees with the manifest per candidate", agreed, detail)


def check_checksums(bundle: str, report: Report) -> None:
    path = os.path.join(bundle, "checksums.sha256")
    if not os.path.isfile(path):
        report.add("checksums.sha256 exists", False)
        return
    bad = []
    checked = 0
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line:
                continue
            digest, _, name = line.partition("  ")
            if not name:
                continue
            target = os.path.join(bundle, name)
            if not os.path.isfile(target):
                bad.append(f"{name} (absent)")
                continue
            checked += 1
            if sha256_file(target) != digest:
                bad.append(name)
    report.add("checksums.sha256 recomputes", not bad,
               f"{checked} files" + (f", bad: {bad}" if bad else ""))


def verify_bundle(bundle: str) -> Report:
    report = Report()
    for name in ("manifest.json", "manifest.canonical", "manifest.sig", "public.pem",
                 "candidates.csv", "ledger.jsonl", "checksums.sha256",
                 "verify_bundle.py", "ed25519_verifier.py", "VERIFY.md", "README.txt"):
        if not os.path.exists(os.path.join(bundle, name)):
            report.add(f"bundle contains {name}", False)
    manifest = check_manifest_bytes(bundle, report)
    if manifest is None:
        return report
    checks = (
        ("file digests", lambda: check_files(bundle, manifest, report)),
        ("signature", lambda: check_signature(bundle, manifest, report)),
        ("ledger", lambda: check_ledger(bundle, manifest, report)),
        ("csv", lambda: check_csv(bundle, manifest, report)),
        ("checksums", lambda: check_checksums(bundle, report)),
    )
    for label, check in checks:
        try:
            check()
        except Exception as exc:  # noqa: BLE001
            # The verifier's whole job is to survive a hostile or damaged
            # bundle: an unexpected error is a named FAIL, never a traceback.
            report.add(f"{label} check completes", False, f"{type(exc).__name__}: {exc}")
            if label == "signature" and report.signature_checked:
                # It crashed before it could check: that is still "not checked".
                report.signature_checked = False
                report.exit_code = 2
    return report


def main(argv: list[str]) -> int:
    bundle = argv[1] if len(argv) > 1 else HERE
    if not os.path.isdir(bundle):
        print(f"not a directory: {bundle}")
        return 2
    report = verify_bundle(bundle)
    report.print()
    if report.exit_code == 0:
        print("\nBundle verified. Compare the key fingerprint with the operator "
              "out of band before trusting the signature.")
    elif report.exit_code == 2:
        print("\nNOT VERIFIED: the Ed25519 signature could not be checked "
              "(exit 2 outranks any other failing check) — treat this bundle as "
              "unverified until a verifier that can check the signature passes.")
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
