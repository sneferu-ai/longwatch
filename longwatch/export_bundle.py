"""The export bundle (§9) — fixed assembly order, Ed25519-signed, self-verifying.

Assembly order (fixed, stated):

1. re-hash every contributing source; mismatch or absence → hard fail;
2. cut excerpts per exported candidate (``keyframe`` or ``exact``) + pixelation;
3. **short transaction:** append ``EXPORT_PRODUCED`` and update the exports row;
4. render ``ledger.jsonl`` from the DB — rows ``1..head``, a frozen snapshot;
5. write ``candidates.csv``, ``clips/``, ``VERIFY.md``, ``README.txt`` and copy
   the two verifier scripts from the app tree;
6. build the manifest object including the ``files`` map;
7. serialize canonical bytes → ``manifest.canonical``; write ``manifest.json``
   as a pretty rendering of the same object;
8. sign ``manifest.canonical`` → ``manifest.sig``; copy ``public.pem``;
9. ``checksums.sha256`` over every bundle file except itself;
10. **short transaction:** the ``exports`` row receives the digest, signature,
    public key, ``signed_at`` and ``bundle_path``.

No ffmpeg invocation, ZIP assembly or file I/O ever runs inside a DB
transaction: steps 1–2 and 4–9 happen outside, and only the two short SQL-only
transactions bracket them.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import json
import logging
import os
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import (INTEGRITY_STATEMENT, TIME_BASIS, __version__, canonical, faces,
               jobs as jobs_mod, review_medium)
from . import ledger, media, signing

log = logging.getLogger("longwatch.export")

BUNDLE_SCHEMA = "longwatch-export-manifest-v1"
DEFAULT_STATES = ("accepted", "flagged")
ALL_STATES = ("open", "accepted", "rejected", "flagged")
MODEL_LICENSE_NOTE = (
    "The bundled detector weights are AGPL-3.0 (Ultralytics YOLOv8n COCO export); "
    "the repository is MIT and ships a pointer, a digest and a model card, never the "
    "weights.  The detector was trained on general COCO imagery: farm, wildlife, night "
    "and overhead performance is unverified and detections are coarse ranking inputs."
)
REPO_ROOT = Path(__file__).resolve().parent.parent


class ExportError(RuntimeError):
    """The export hard-failed; the reason is named and preserved."""


def _now_ms() -> int:
    import time
    return time.time_ns() // 1_000_000


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _sha256(path: Path) -> str:
    return media.sha256_file(path)


# --------------------------------------------------------------- selection
def select_candidates(db, states: tuple[str, ...], top_n: int) -> list[dict]:
    """Decision-scoped, never version-scoped: any decided candidate stays eligible."""
    return db.fetch_all(
        """
        SELECT c.id AS candidate_id, c.window_id, c.pattern_slug, c.pattern_version,
               c.score, c.sentence, c.evidence, c.state,
               w.source_id, w.start_s, w.end_s, w.idx AS window_idx,
               s.sha256 AS source_sha256, s.storage_path, s.original_name,
               s.size_bytes, s.duration_s, s.probe, s.origin
          FROM candidates c
          JOIN windows w ON w.id = c.window_id
          JOIN sources s ON s.id = w.source_id
         WHERE c.state = ANY(%s)
         ORDER BY c.score DESC, w.source_id, w.start_s, c.pattern_slug
         LIMIT %s
        """,
        (list(states), int(top_n)),
    )


def latest_decisions(db, candidate_ids: list[str]) -> dict[str, dict]:
    if not candidate_ids:
        return {}
    rows = db.fetch_all(
        """
        SELECT DISTINCT ON (candidate_id) candidate_id, action, note, reviewer,
               src_viewed, created_at
          FROM decisions
         WHERE candidate_id = ANY(%s)
         ORDER BY candidate_id, created_at DESC
        """,
        (candidate_ids,),
    )
    return {str(row["candidate_id"]): row for row in rows}


def ml_failed_summary(db) -> dict:
    rows = db.fetch_all(
        """
        SELECT w.id, w.source_id, w.idx, w.start_s, w.end_s, w.ml_failure_reason,
               w.ml_acked_at IS NOT NULL AS acknowledged
          FROM windows w
         WHERE w.ml_state = 'failed'
         ORDER BY w.source_id, w.idx
        """
    )
    return {
        "count": len(rows),
        "acknowledged": sum(1 for row in rows if row["acknowledged"]),
        "windows": [
            {
                "window_id": str(row["id"]),
                "source_id": str(row["source_id"]),
                "idx": int(row["idx"]),
                "start_ms": media.seconds_to_ms(float(row["start_s"])),
                "end_ms": media.seconds_to_ms(float(row["end_s"])),
                "reason": row["ml_failure_reason"] or "",
                "acknowledged": bool(row["acknowledged"]),
            }
            for row in rows
        ],
    }


# ----------------------------------------------------------------- cutting
def _cut_excerpt(source_path: str, out_path: Path, start_s: float, end_s: float,
                 cut_precision: str, cfg) -> dict:
    duration = max(0.0, end_s - start_s)
    if cut_precision == "exact":
        argv = media.exact_cut_command(source_path, out_path, start_s, duration)
        mode = "reencoded"
        actual_start = start_s
    else:
        keyframe = media.keyframe_before(source_path, start_s, 60.0,
                                         cfg.ffmpeg_hang_base_s, cfg.ffmpeg_hang_factor)
        actual_start = keyframe if keyframe is not None else start_s
        argv = media.keyframe_cut_command(source_path, out_path, actual_start,
                                          duration + (start_s - actual_start))
        mode = "keyframe_snapped"
    media.run_ffmpeg(argv, media.hang_timeout(cfg.ffmpeg_hang_base_s,
                                              cfg.ffmpeg_hang_factor, duration))
    return {"actual_start_s": float(actual_start), "cut_mode": mode,
            "ffmpeg_cmd": " ".join(argv)}


# ------------------------------------------------------------------ bundle
def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def run_export(db, job: jobs_mod.Job, cfg) -> dict:
    detail = job.detail or ""
    # the lease reclaimer appends " [lease reclaimed]" to the detail, so the
    # id is matched, not split — a reclaimed export must still find its row
    match = re.search(r"export_id=([0-9a-fA-F-]{36})", detail)
    export_id = match.group(1) if match else None
    if not export_id:
        raise ExportError("export job carries no export_id")
    export = db.fetch_one("SELECT * FROM exports WHERE id = %s", (export_id,))
    if export is None:
        raise ExportError(f"export {export_id} does not exist")

    states = tuple(export["states"] if isinstance(export["states"], list)
                   else json.loads(export["states"]))
    top_n = int(export["top_n"])
    blur_faces = bool(export["blur_faces"])
    cut_precision = export["cut_precision"] or "keyframe"
    reviewer = export["reviewer"]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_dir = Path(cfg.data_dir) / "exports" / f"export_{stamp}_{str(export_id)[:8]}"
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    (bundle_dir / "clips").mkdir(parents=True, exist_ok=True)

    # 1. re-hash every contributing source -----------------------------------
    candidates = select_candidates(db, states, top_n)
    source_ids = sorted({str(row["source_id"]) for row in candidates})
    sources: dict[str, dict] = {}
    for source_id in source_ids:
        row = db.fetch_one("SELECT * FROM sources WHERE id = %s", (source_id,))
        if row is None:
            raise ExportError(f"source {source_id} vanished")
        path = Path(row["storage_path"])
        actual = _sha256(path) if path.is_file() else "absent"
        if actual != row["sha256"]:
            with db.transaction() as cur:
                ledger.append(cur, "SOURCE_DIGEST_MISMATCH", reviewer, {
                    "source_id": str(row["id"]),
                    "expected_sha256": row["sha256"],
                    "actual": actual,
                    "export_id": str(export_id),
                })
            raise ExportError(
                f"source {row['original_name']} digest mismatch ({actual}); export refused")
        sources[source_id] = row

    decisions = latest_decisions(db, [str(row["candidate_id"]) for row in candidates])

    # 2. cut excerpts ---------------------------------------------------------
    # Every excerpt is cut to scratch under $DATA/tmp first and only the
    # artifact that will ship is moved into the bundle: a pixelated export must
    # never carry the un-pixelated pixels beside it.
    scratch = Path(cfg.data_dir) / "tmp" / f"export-{str(export_id)[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)
    clips: list[dict] = []
    face_detector = None
    try:
        for row in candidates:
            candidate_id = str(row["candidate_id"])
            scratch_path = scratch / f"{candidate_id}.mp4"
            cut = _cut_excerpt(sources[str(row["source_id"])]["storage_path"],
                               scratch_path, float(row["start_s"]), float(row["end_s"]),
                               cut_precision, cfg)
            requested_start_ms = media.seconds_to_ms(float(row["start_s"]))
            actual_start_ms = media.seconds_to_ms(cut["actual_start_s"])
            end_ms = media.seconds_to_ms(float(row["end_s"]))
            transform = None
            face_model = None
            if blur_faces:
                model_path = Path(cfg.face_model_path)
                if not model_path.is_file():
                    raise ExportError(
                        "face pixelation was requested but no usable face model is present at "
                        f"{cfg.face_model_path} — refusing rather than silently skipping")
                if face_detector is None:
                    try:
                        face_detector = faces.FaceDetector(
                            model_path, expected_sha256=cfg.face_model_sha256)
                    except faces.FaceModelError as exc:
                        raise ExportError(str(exc)) from exc
                face_model = {"basename": model_path.name, "sha256": _sha256(model_path)}
                shipped_path = bundle_dir / "clips" / f"{candidate_id}_blurred.mp4"
                try:
                    counts = faces.pixelate_clip(scratch_path, shipped_path,
                                                 face_detector, cfg)
                except faces.FaceModelError as exc:
                    raise ExportError(str(exc)) from exc
                # the record states what the transform actually did: the face
                # model ran, and these are the regions it mosaicked
                transform = {"operation": "face_pixelate", "block": faces.FACE_BLOCK,
                             "regions_pixelated": counts["regions_pixelated"],
                             "frames_processed": counts["frames_processed"],
                             "max_faces_per_frame": counts["max_faces_per_frame"]}
                clip_name = f"clips/{candidate_id}_blurred.mp4"
                kind = "blurred_excerpt"
            else:
                shipped_path = bundle_dir / "clips" / f"{candidate_id}.mp4"
                os.replace(scratch_path, shipped_path)
                clip_name = f"clips/{candidate_id}.mp4"
                kind = "export_excerpt"
            digest = _sha256(shipped_path)
            clips.append({
                "clip_name": clip_name,
                "candidate_id": candidate_id,
                "window_id": str(row["window_id"]),
                "source_id": str(row["source_id"]),
                "kind": kind,
                "sha256": digest,
                "size_bytes": shipped_path.stat().st_size,
                "requested_start_ms": requested_start_ms,
                "actual_start_ms": actual_start_ms,
                "end_ms": end_ms,
                "cut_mode": cut["cut_mode"],
                "transform": transform,
                "face_model": face_model,
                "ffmpeg_cmd": cut["ffmpeg_cmd"],
            })
            with db.transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO clips (window_id, kind, requested_start_ms, actual_start_ms,
                                       end_ms, path, sha256, size_bytes, cut_mode, ffmpeg_cmd,
                                       transform)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (row["window_id"], kind, requested_start_ms, actual_start_ms,
                     end_ms, str(shipped_path), digest, clips[-1]["size_bytes"],
                     cut["cut_mode"], cut["ffmpeg_cmd"],
                     jobs_mod.jsonb(transform) if transform else None),
                )
                clip_id = str(cur.fetchone()["id"])
                ledger.append(cur, "CLIP_DERIVED", reviewer, {
                    "source_id": str(row["source_id"]),
                    "window_id": str(row["window_id"]),
                    "clip_id": clip_id,
                    "kind": kind,
                    "sha256": digest,
                    "requested_start_ms": requested_start_ms,
                    "actual_start_ms": actual_start_ms,
                    "end_ms": end_ms,
                    "cut_mode": cut["cut_mode"],
                })
                if transform is not None:
                    ledger.append(cur, "TRANSFORM_APPLIED", reviewer, {
                        "clip_id": clip_id,
                        "window_id": str(row["window_id"]),
                        "source_id": str(row["source_id"]),
                        "transform": transform,
                        "face_model": face_model,
                    })
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    # 3. short transaction: EXPORT_PRODUCED ----------------------------------
    key = signing.load_or_create_key(cfg.key_path)
    public_pem = signing.public_pem(key)
    public_fingerprint = signing.fingerprint_of_pem(public_pem)
    with db.transaction() as cur:
        ledger.append(cur, "EXPORT_PRODUCED", reviewer, {
            "export_id": str(export_id),
            "reviewer": reviewer,
            "top_n": top_n,
            "states": list(states),
            "blur_faces": blur_faces,
            "cut_precision": cut_precision,
            "candidates_count": len(candidates),
            "clips_count": len(clips),
            "public_key_sha256": public_fingerprint,
        })

    # 4. frozen ledger transcript --------------------------------------------
    with db.connect() as conn:
        head_seq, head_hash = ledger.head(conn)
        transcript = ledger.transcript_rows(conn, head_seq)
    ledger_path = bundle_dir / "ledger.jsonl"
    with open(ledger_path, "w", encoding="utf-8") as handle:
        for row in transcript:
            handle.write(canonical.canonical_json(row) + "\n")

    # 5. payload files --------------------------------------------------------
    created_ms = _now_ms()
    csv_path = bundle_dir / "candidates.csv"
    _write_csv(csv_path, candidates, decisions, clips)
    shutil.copy2(REPO_ROOT / "verify_bundle.py", bundle_dir / "verify_bundle.py")
    shutil.copy2(REPO_ROOT / "ed25519_verifier.py", bundle_dir / "ed25519_verifier.py")
    (bundle_dir / "VERIFY.md").write_text(_verify_md(), encoding="utf-8")
    (bundle_dir / "README.txt").write_text(
        _readme_txt(cfg, cut_precision, blur_faces), encoding="utf-8")

    # 6. the manifest object --------------------------------------------------
    manifest = _build_manifest(
        bundle_dir=bundle_dir, export=export, candidates=candidates, decisions=decisions,
        clips=clips, sources=sources, cfg=cfg, created_ms=created_ms,
        head_seq=head_seq, head_hash=head_hash, rows=len(transcript),
        public_fingerprint=public_fingerprint, reviewer=reviewer,
        cut_precision=cut_precision, blur_faces=blur_faces, states=states,
        ml_failed=ml_failed_summary(db),
    )

    # 7. canonical bytes + pretty rendering -----------------------------------
    canonical_bytes = canonical.canonical_bytes(manifest)
    (bundle_dir / "manifest.canonical").write_bytes(canonical_bytes)
    (bundle_dir / "manifest.json").write_text(canonical.pretty_json(manifest), encoding="utf-8")

    # 8. signature + public key ----------------------------------------------
    signature = signing.sign(key, canonical_bytes)
    # manifest.sig carries the RAW 64-byte signature so the documented
    # independent check `openssl pkeyutl ... -sigfile manifest.sig` works
    # verbatim (openssl reads raw bytes, never base64).  The base64 rendering
    # travels on the exports row and in VERIFY.md's instructions.
    (bundle_dir / "manifest.sig").write_bytes(signature)
    (bundle_dir / "public.pem").write_text(public_pem, encoding="utf-8")

    # 9. checksums ------------------------------------------------------------
    checksums = []
    for path in sorted(bundle_dir.rglob("*")):
        if path.is_file() and path.name != "checksums.sha256":
            checksums.append(f"{_sha256(path)}  {path.relative_to(bundle_dir).as_posix()}")
    (bundle_dir / "checksums.sha256").write_text("\n".join(checksums) + "\n", encoding="utf-8")

    manifest_digest = hashlib.sha256(canonical_bytes).hexdigest()

    # 10. short transaction: the exports row ---------------------------------
    with db.transaction() as cur:
        cur.execute(
            """
            UPDATE exports
               SET bundle_path = %s, manifest_sha256 = %s, signature = %s,
                   public_key = %s, signed_at = now()
             WHERE id = %s
            """,
            (str(bundle_dir), manifest_digest, base64.b64encode(signature).decode("ascii"),
             public_pem, str(export_id)),
        )

    zip_path = bundle_dir.with_suffix(".zip")
    _zip_bundle(bundle_dir, zip_path)
    return {"export_id": str(export_id), "bundle_path": str(bundle_dir),
            "zip_path": str(zip_path), "candidates": len(candidates),
            "clips": len(clips), "manifest_sha256": manifest_digest}


def _zip_bundle(bundle_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(bundle_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(bundle_dir.parent).as_posix())


def _write_csv(path: Path, candidates: list[dict], decisions: dict[str, dict],
               clips: list[dict]) -> None:
    clip_by_candidate = {clip["candidate_id"]: clip["clip_name"] for clip in clips}
    fieldnames = ["rank", "candidate_id", "score", "pattern_slug", "pattern_version",
                  "sentence", "decision_state", "reviewer", "src_viewed", "synthetic",
                  "source_id", "window_id", "start_ms", "end_ms", "clip"]
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for rank, row in enumerate(candidates, start=1):
            decision = decisions.get(str(row["candidate_id"])) or {}
            evidence = row["evidence"]
            if isinstance(evidence, str):
                evidence = json.loads(evidence)
            writer.writerow({
                "rank": rank,
                "candidate_id": str(row["candidate_id"]),
                "score": int(row["score"]),
                "pattern_slug": row["pattern_slug"],
                "pattern_version": int(row["pattern_version"]),
                "sentence": row["sentence"],
                "decision_state": row["state"],
                "reviewer": decision.get("reviewer", ""),
                "src_viewed": "true" if decision.get("src_viewed") else "false",
                "synthetic": "true" if evidence.get("synthetic") else "false",
                "source_id": str(row["source_id"]),
                "window_id": str(row["window_id"]),
                "start_ms": media.seconds_to_ms(float(row["start_s"])),
                "end_ms": media.seconds_to_ms(float(row["end_s"])),
                "clip": clip_by_candidate.get(str(row["candidate_id"]), ""),
            })


def _build_manifest(*, bundle_dir: Path, export: dict, candidates: list[dict],
                    decisions: dict[str, dict], clips: list[dict], sources: dict[str, dict],
                    cfg, created_ms: int, head_seq: int, head_hash: str, rows: int,
                    public_fingerprint: str, reviewer: str, cut_precision: str,
                    blur_faces: bool, states: tuple[str, ...], ml_failed: dict) -> dict:
    files: dict[str, str] = {}
    for path in sorted(bundle_dir.rglob("*")):
        if not path.is_file():
            continue
        name = path.relative_to(bundle_dir).as_posix()
        if name in ("manifest.json", "manifest.canonical", "manifest.sig", "public.pem",
                    "checksums.sha256"):
            continue
        files[name] = _sha256(path)

    manifest_candidates = []
    for rank, row in enumerate(candidates, start=1):
        decision = decisions.get(str(row["candidate_id"])) or {}
        evidence = row["evidence"]
        if isinstance(evidence, str):
            evidence = json.loads(evidence)
        manifest_candidates.append({
            "rank": rank,
            "candidate_id": str(row["candidate_id"]),
            "source_id": str(row["source_id"]),
            "window_id": str(row["window_id"]),
            "window_idx": int(row["window_idx"]),
            "span_start_ms": media.seconds_to_ms(float(row["start_s"])),
            "span_end_ms": media.seconds_to_ms(float(row["end_s"])),
            "pattern_slug": row["pattern_slug"],
            "pattern_version": int(row["pattern_version"]),
            "score": int(row["score"]),
            "sentence": row["sentence"],
            "evidence": evidence,
            "decision_state": row["state"],
            "reviewer": decision.get("reviewer") or "",
            "src_viewed": bool(decision.get("src_viewed")),
            "synthetic": bool(evidence.get("synthetic")),
        })

    manifest_sources = []
    for source_id, row in sorted(sources.items()):
        probe = row["probe"]
        if isinstance(probe, str):
            probe = json.loads(probe)
        manifest_sources.append({
            "source_id": source_id,
            "original_name": row["original_name"],
            "origin": row["origin"],
            "sha256": row["sha256"],
            "size_bytes": int(row["size_bytes"]),
            "duration_ms": media.seconds_to_ms(float(row["duration_s"] or 0.0)),
            "probe": probe,
        })

    manifest_clips = []
    for clip in clips:
        entry = {
            "clip_name": clip["clip_name"],
            "candidate_id": clip["candidate_id"],
            "source_id": clip["source_id"],
            "window_id": clip["window_id"],
            "kind": clip["kind"],
            "sha256": clip["sha256"],
            "size_bytes": int(clip["size_bytes"]),
            "requested_start_ms": int(clip["requested_start_ms"]),
            "actual_start_ms": int(clip["actual_start_ms"]),
            "end_ms": int(clip["end_ms"]),
            "cut_mode": clip["cut_mode"],
            "transform": clip["transform"],
            "alignment_note": (
                "starts at or before the requested span; lead-in footage up to the "
                "previous keyframe is included"
                if clip["cut_mode"] == "keyframe_snapped"
                else "re-encoded; actual bounds equal the requested bounds"
            ),
        }
        if clip["transform"] is not None:
            # §9: the manifest records the same face model identity as the
            # TRANSFORM_APPLIED ledger row — a recipient must be able to tell
            # from the signed manifest which model produced the mosaic.
            entry["face_model"] = clip["face_model"]
        manifest_clips.append(entry)

    return {
        "schema": BUNDLE_SCHEMA,
        "generator": f"longwatch {__version__}",
        "created_at_ms": int(created_ms),
        "created_at_iso": _iso(created_ms),
        "time_basis": TIME_BASIS,
        "reviewer": reviewer,
        "review_medium": review_medium(cfg.proxy_long_edge),
        "integrity_statement": INTEGRITY_STATEMENT,
        "export_id": str(export["id"]),
        "top_n": int(export["top_n"]),
        "states": list(states),
        "cut_precision": cut_precision,
        "blur_faces": bool(blur_faces),
        "model": {
            "path_basename": os.path.basename(cfg.model_path),
            "sha256": cfg.model_sha256,
            "license": "AGPL-3.0",
            "license_note": MODEL_LICENSE_NOTE,
            "ort_threads": int(cfg.ort_threads),
            "class_map_basename": os.path.basename(cfg.class_map_path),
        },
        "sources": manifest_sources,
        "candidates": manifest_candidates,
        "clips": manifest_clips,
        "ml_failed": ml_failed,
        "ledger_head_seq": int(head_seq),
        "head_hash": head_hash,
        "ledger_rows": int(rows),
        "public_key_sha256": public_fingerprint,
        # The signed statement of the detached signature's own encoding.
        # manifest.sig holds the RAW 64-byte signature (openssl `-sigfile`
        # reads raw bytes and never base64); the base64 rendering is
        # `base64 -w0 manifest.sig` and rides the exports row.  Stating it
        # inside the signed document means a third party never has to guess
        # which encoding to decode.  The key is named `detached_signature`,
        # never `signature`: §9 step 7 states that no signature field exists
        # in the signed object — the signature itself lives in manifest.sig,
        # which is exactly what this block's `file` member says.
        "detached_signature": {
            "algorithm": "ed25519",
            "encoding": "raw",
            "bytes": 64,
            "file": "manifest.sig",
            "signed_document": "manifest.canonical",
        },
        "files": files,
    }


def _verify_md() -> str:
    return f"""# Verifying this bundle

This bundle is verifiable **from its own files alone**: no LongWatch install, no
database and no trust in the vendor is required.

## 1. One command (recommended)

    python3 verify_bundle.py .

It checks that every file digest matches the signed manifest, that
`manifest.json` re-canonicalizes byte-equal to `manifest.canonical`, that the
Ed25519 signature verifies, that the `ledger.jsonl` chain walks clean, that the
CSV agrees with the manifest, and that `checksums.sha256` recomputes.

Exit codes: `0` all checks pass, `1` a check failed, `2` the signature could not
be checked (treat this as *not verified*).

## 2. Independent paths (no LongWatch code)

    sha256sum --check checksums.sha256

    openssl pkeyutl -verify -pubin -inkey public.pem -rawin \\
        -in manifest.canonical -sigfile manifest.sig

The `openssl` command requires OpenSSL >= 3.0 (Ed25519 `-rawin`).  Note the
option is `-sigfile`; `openssl pkeyutl` does not accept `-sig`.  `manifest.sig`
holds the **raw 64-byte signature** — `openssl` reads raw bytes and never
base64 — so the command above runs verbatim.  For a text rendering:

    base64 -w0 manifest.sig

The encoding is stated inside the signed manifest itself
(`detached_signature.encoding = "raw"` — named so the signed object carries no
field called `signature`; the signature lives in `manifest.sig`).  The layout
sketch in the problem statement annotates this file `(base64)`, and that
annotation is superseded here and by the `openssl -sigfile` command the same
section requires to exit 0 — one encoding, raw bytes, everywhere.

The chain can be re-walked by hand: each `ledger.jsonl` row's `row_hash` is

    sha256("{{canonical_preimage}}")

where the preimage is

    "longwatch-ledger-v1|" + seq + "|" + prev_hash + "|" + event_type + "|" +
    actor + "|" + recorded_at_ms + "|" + canonical_json(payload)

and `canonical_json` is the integer-only canonical form: object keys sorted by
code point, no whitespace between tokens, UTF-8 with RFC 8259 minimal escaping.
Row N carries `prev_hash = row_hash(N-1)`; the first row's `prev_hash` is 64
zeros.  A re-deployment with an empty volume starts a new ledger — an old
bundle is still verified from its own transcript.

## 3. The trust root, stated honestly

The signature proves the bundle's contents are exactly what the signing key
signed and have not changed since.  It does **not** prove the key belongs to the
operator — no offline bundle can prove that.  Compare the key fingerprint
(SHA-256 of `public.pem`) with the operator over a channel of your choosing
before trusting anything.  The verifier scripts are signed too: their digests
are in the manifest's `files` map, so a tampered verifier is detectable.

## 4. What this bundle claims — and does not

{INTEGRITY_STATEMENT}

Time basis: {TIME_BASIS}.

Review medium: decisions were rendered on a lossy working copy.  Every accepted
decision records whether the reviewer opened the source-resolution pane
(`src_viewed` in `candidates.csv` and in the manifest).  Recipients should
spot-check accepted decisions against the excerpts.
"""


def _readme_txt(cfg, cut_precision: str, blur_faces: bool) -> str:
    # §9/§15: a re-encode is never labeled bit-identical.  With pixelation on
    # every clip was decoded and re-encoded, so the keyframe bullet must not
    # carry the stream-copy claim (README.txt's digest is signed via the
    # manifest's files map — a false claim there is an attested false claim).
    if blur_faces:
        keyframe_bullet = (
            "  keyframe -> starts at or before the requested span; lead-in footage up to the\n"
            "              previous keyframe is included.  blur_faces is on, so every shipped\n"
            "              clip was decoded and re-encoded with the detected face regions\n"
            "              mosaicked; the encoded frames are NOT bit-identical to the source's."
        )
    else:
        keyframe_bullet = (
            "  keyframe -> starts at or before the requested span; lead-in footage up to the\n"
            "              previous keyframe is included; the encoded frames are bit-identical\n"
            "              to the source's."
        )
    return f"""{INTEGRITY_STATEMENT}

TIME BASIS
{TIME_BASIS}

REVIEW MEDIUM
{review_medium(cfg.proxy_long_edge)}
Each decision records whether the reviewer opened the source-resolution pane
before deciding (`src_viewed` in candidates.csv).

EXCERPTS
cut_precision: {cut_precision}
{keyframe_bullet}
  exact    -> re-encoded; actual bounds equal the requested bounds; a re-encode
              is never labeled bit-identical.
blur_faces: {"true" if blur_faces else "false"}

MODEL DISCLOSURE
{MODEL_LICENSE_NOTE}

RECIPIENT GUIDANCE
Compare the key fingerprint (SHA-256 of public.pem) with the operator out of
band before trusting the signature.  See VERIFY.md.

SIGNATURE ENCODING
manifest.sig holds the raw 64-byte Ed25519 signature (the manifest itself says
so: detached_signature.encoding = "raw").  The independent check reads it raw:
    openssl pkeyutl -verify -pubin -inkey public.pem -rawin \\
        -in manifest.canonical -sigfile manifest.sig
`base64 -w0 manifest.sig` prints the text form.  One encoding everywhere; a
verifier that base64-decodes this file first will fail on a valid bundle.
"""
