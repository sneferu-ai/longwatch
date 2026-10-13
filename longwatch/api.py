"""The HTTP API (§10) — FastAPI, same origin as the SPA.

Every response — the SPA document, the API, both byte-range media routes —
carries ``Referrer-Policy: no-referrer``.  When ``LONGWATCH_AUTH_TOKEN`` is set,
every ``/api/*`` route requires ``Authorization: Bearer <token>`` compared with
``secrets.compare_digest``; because ``<video>`` elements cannot send headers,
the two byte-range media routes also accept ``?token=``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import INTEGRITY_STATEMENT, TIME_BASIS, review_medium
from . import demo as demo_mod
from . import jobs as jobs_mod
from . import ledger, media, patterns as patterns_mod, pipeline, scoring, vocab
from . import rules as rules_mod

log = logging.getLogger("longwatch.api")

SPA_DIR = Path(__file__).resolve().parent / "static"
MEDIA_ROUTES = ("/api/v1/clips/{clip_id}/proxy", "/api/v1/sources/{source_id}/original")
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")

def model_provenance(cfg) -> str:
    """Did this image fetch the pinned weights, or fall back to the fixture?

    The Dockerfile's models stage records the answer beside the model file; a
    surface that cannot name its provenance must not imply it fetched weights.
    """
    marker = Path(cfg.model_path).parent / "MODEL_PROVENANCE"
    try:
        if marker.is_file():
            return marker.read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        pass
    return "operator-supplied"


def provenance_note(provenance: str) -> str:
    """The honest sentence each MODEL_PROVENANCE value earns (§8.3)."""
    if provenance == "fixture":
        return (" This image loaded the in-repo contract fixture, NOT the pinned "
                "YOLOv8n weights: detections are placeholders.")
    if provenance == "fetched_unpinned":
        return (" This image fetched the detector weights from the canonical public "
                "source but had no published digest to verify them against; the "
                "fetched digest is recorded beside the model (run "
                "scripts/pin_models.sh and rebuild to pin and verify).")
    if provenance == "fetched":
        return (" The detector weights were fetched at image-build time and verified "
                "against the digest pin.")
    return ""


MODEL_LIMITS = (
    "The bundled detector (YOLOv8n, AGPL-3.0) was trained on general COCO imagery: "
    "farm, wildlife, night and overhead performance is unverified, and detections are "
    "coarse ranking inputs — not findings."
)


# ---------------------------------------------------------------- request models
class DecisionRequest(BaseModel):
    action: str
    note: str | None = None
    src_viewed: bool = False


class PatternRequest(BaseModel):
    title: str
    sentence_template: str
    rule: dict
    weight: int = Field(ge=1, le=100)
    enabled: bool = True


class ExportRequest(BaseModel):
    top_n: int | None = None
    blur_faces: bool | None = None
    states: list[str] | None = None
    cut_precision: str = "keyframe"


class TokenRequest(BaseModel):
    token: str


# ------------------------------------------------------------------- helpers
def _require_token(request: Request, allow_query: bool = False) -> None:
    """The optional bearer gate (§4).

    The token rides the ``Authorization`` header on every ``/api/*`` route.
    The ``?token=`` query form exists **solely** for the two byte-range media
    routes (``<video>`` elements cannot send headers) — every other route
    refuses it, so a URL-borne token has exactly two narrow uses and the
    ``Referrer-Policy: no-referrer`` header closes the leak there.
    """
    cfg = request.app.state.cfg
    if not cfg.auth_token:
        return
    provided = ""
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        provided = header[7:].strip()
    if not provided and allow_query:
        provided = request.query_params.get("token") or ""
    if not provided or not secrets.compare_digest(provided, cfg.auth_token):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(name: str | None) -> str:
    """A model- or operator-supplied filename is never trusted as a path."""
    cleaned = SAFE_NAME_RE.sub("_", Path(name or "upload").name).strip("._-")
    return (cleaned or "upload")[:120]


def _enqueue(db, kind: str, source_id: str | None = None, detail: str | None = None) -> str:
    """Queue one job on its own short transaction."""
    with db.connect() as conn:
        return jobs_mod.enqueue(conn, kind, source_id=source_id, detail=detail)


def _vocab_guard(*texts: str | None) -> None:
    for text in texts:
        term = vocab.find_forbidden(text)
        if term is not None:
            raise HTTPException(status_code=422, detail={
                "error": "vocabulary_breach",
                "term": term,
                "guidance": vocab.GUIDANCE,
            })


def _spa_response(path: str) -> Response:
    if not path or path == "/":
        path = "index.html"
    target = (SPA_DIR / path.lstrip("/")).resolve()
    if not str(target).startswith(str(SPA_DIR.resolve())) or not target.is_file():
        target = SPA_DIR / "index.html"
    if not target.is_file():
        return JSONResponse(
            {"error": "spa_not_built",
             "detail": "the SPA has not been built; run npm run build in frontend/"},
            status_code=503)
    media_type = None
    if target.suffix == ".js":
        media_type = "text/javascript"
    elif target.suffix == ".css":
        media_type = "text/css"
    elif target.suffix == ".html":
        media_type = "text/html"
    return FileResponse(str(target), media_type=media_type)


def range_response(path: str, range_header: str | None) -> Response:
    """HTTP byte-range serving for the two media routes."""
    file_path = Path(path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="media file is absent")
    size = file_path.stat().st_size
    if not range_header:
        return FileResponse(str(file_path), media_type="video/mp4")
    match = RANGE_RE.match(range_header.strip())
    if not match:
        raise HTTPException(status_code=416, detail="unsupported range")
    start_raw, end_raw = match.groups()
    if start_raw == "" and end_raw == "":
        raise HTTPException(status_code=416, detail="empty range")
    if start_raw == "":
        length = int(end_raw)
        start = max(0, size - length)
        end = size - 1
    else:
        start = int(start_raw)
        end = int(end_raw) if end_raw else size - 1
    end = min(end, size - 1)
    if start > end or start >= size:
        raise HTTPException(status_code=416, detail="range not satisfiable")
    length = end - start + 1

    def iterator():
        with open(file_path, "rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(1024 * 256, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(iterator(), status_code=206, media_type="video/mp4",
                             headers={
                                 "Content-Range": f"bytes {start}-{end}/{size}",
                                 "Accept-Ranges": "bytes",
                                 "Content-Length": str(length),
                             })


# -------------------------------------------------------------------- routes
def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    # ------------------------------------------------------------- uploads
    @router.post("/uploads")
    async def upload(request: Request, file: UploadFile):
        cfg = request.app.state.cfg
        db = request.app.state.db
        tmp_dir = Path(cfg.data_dir) / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp_dir / f"upload-{secrets.token_hex(8)}.part"
        hasher = media.StreamingHasher()
        limit = int(cfg.upload_limit_mb) * 1024 * 1024
        # LONGWATCH_UPLOAD_CONCURRENCY: at most N upload streams in flight
        semaphore = getattr(request.app.state, "upload_semaphore", None)
        if semaphore is None:  # tests driving the router without create_app
            import asyncio
            semaphore = request.app.state.upload_semaphore = asyncio.Semaphore(
                int(cfg.upload_concurrency))
        try:
            async with semaphore:
                with open(tmp_path, "wb") as handle:
                    while True:
                        chunk = await file.read(8 * 1024 * 1024)  # <= 8 MiB in flight
                        if not chunk:
                            break
                        hasher.update(chunk)
                        if hasher.size > limit:
                            handle.close()
                            tmp_path.unlink(missing_ok=True)
                            raise HTTPException(status_code=413, detail={
                                "error": "upload_too_large",
                                "limit_mb": int(cfg.upload_limit_mb),
                            })
                        handle.write(chunk)
        except HTTPException:
            raise
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        digest = hasher.hexdigest()
        import anyio

        def _register():
            """Everything DB- or file-touching runs off the event loop (§5).

            The duplicate sighting is a registration outcome like any other:
            its digest lookup and its SOURCE_DUPLICATE append are blocking DB
            calls, so they live in this closure beside ``register_source`` —
            no blocking DB call ever runs on the event loop.
            """
            existing = pipeline.find_source_by_digest(db, digest)
            if existing is not None:
                tmp_path.unlink(missing_ok=True)
                with db.transaction() as cur:
                    ledger.append(cur, "SOURCE_DUPLICATE", cfg.reviewer_name, {
                        "source_id": str(existing["id"]), "sha256": digest,
                        "new_path": str(tmp_path), "origin": "upload",
                        "original_name": file.filename or "",
                        "discarded": True,
                    })
                return existing, None
            originals = Path(cfg.data_dir) / "originals"
            originals.mkdir(parents=True, exist_ok=True)
            final = originals / f"{digest[:16]}-{_safe_name(file.filename)}"
            os.replace(tmp_path, final)
            source, created = pipeline.register_source(
                db, storage_path=str(final), origin="upload",
                original_name=file.filename or final.name, sha256=digest,
                size_bytes=hasher.size, actor=cfg.reviewer_name)
            if not created:
                return source, None  # a concurrent identical upload won the race
            return source, _enqueue(db, "ingest", source_id=str(source["id"]),
                                    detail="upload")

        try:
            source, job_id = await anyio.to_thread.run_sync(_register)
        except HTTPException:
            raise
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        if job_id is None:
            return JSONResponse({"source_id": str(source["id"]), "duplicate": True,
                                 "state": source["state"]},
                                status_code=200)
        return JSONResponse({"source_id": str(source["id"]), "job_id": job_id},
                            status_code=202)

    @router.post("/ingest/scan")
    def scan_inbox(request: Request):
        cfg = request.app.state.cfg
        db = request.app.state.db
        if not cfg.inbox_dir:
            raise HTTPException(status_code=400, detail={
                "error": "inbox_disabled",
                "detail": "LONGWATCH_INBOX_DIR is unset; the scan directory is disabled",
            })
        inbox = Path(cfg.inbox_dir)
        if not inbox.is_dir():
            raise HTTPException(status_code=400, detail={
                "error": "inbox_missing", "detail": f"{inbox} is not a directory"})
        results = pipeline.scan_inbox(db, cfg, cfg.reviewer_name)
        return JSONResponse(results, status_code=202)

    @router.post("/demo/seed")
    def demo_seed(request: Request):
        cfg = request.app.state.cfg
        db = request.app.state.db
        try:
            outcome = demo_mod.create_demo_source(db, cfg)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail={
                "error": "demo_seed_failed", "detail": str(exc)}) from exc
        source = outcome["source"]
        body = {"source_id": str(source["id"])}
        if outcome["existing"]:
            return JSONResponse(body, status_code=200)
        body["job_id"] = outcome["job_id"]
        return JSONResponse(body, status_code=202)

    # -------------------------------------------------------------- sources
    @router.get("/sources")
    def list_sources(request: Request):
        db = request.app.state.db
        rows = db.fetch_all(
            """
            SELECT s.id, s.origin, s.original_name, s.state, s.sha256, s.size_bytes,
                   s.duration_s, s.has_audio, s.created_at, s.failure_reason,
                   s.signal_summary,
                   (SELECT count(*) FROM windows w WHERE w.source_id = s.id) AS window_count,
                   (SELECT count(*) FROM jobs j WHERE j.source_id = s.id
                     AND j.state IN ('queued','running')) AS pending_jobs
              FROM sources s
             ORDER BY s.created_at DESC
            """
        )
        for row in rows:
            row["id"] = str(row["id"])
            if isinstance(row.get("signal_summary"), str):
                row["signal_summary"] = json.loads(row["signal_summary"])
        return {"sources": rows}

    @router.get("/sources/{source_id}")
    def source_detail(request: Request, source_id: str):
        db = request.app.state.db
        row = db.fetch_one("SELECT * FROM sources WHERE id = %s", (source_id,))
        if row is None:
            raise HTTPException(status_code=404, detail="source not found")
        row["id"] = str(row["id"])
        if isinstance(row.get("signal_summary"), str):
            row["signal_summary"] = json.loads(row["signal_summary"])
        job = db.fetch_one(
            "SELECT id, kind, state, stage, pct, detail, error FROM jobs "
            "WHERE source_id = %s ORDER BY created_at DESC LIMIT 1", (source_id,))
        if job:
            job["id"] = str(job["id"])
        row["job"] = job
        counts = db.fetch_one(
            """
            SELECT count(*) AS windows,
                   count(*) FILTER (WHERE ml_state = 'failed') AS ml_failed
              FROM windows WHERE source_id = %s
            """, (source_id,))
        row["window_count"] = int(counts["windows"])
        row["ml_failed_count"] = int(counts["ml_failed"])
        return row

    @router.get("/sources/{source_id}/timeline")
    def source_timeline(request: Request, source_id: str):
        db = request.app.state.db
        candidates = db.fetch_all(
            """
            SELECT c.id, c.pattern_slug, c.pattern_version, c.score, c.sentence, c.state,
                   w.start_s, w.end_s
              FROM candidates c JOIN windows w ON w.id = c.window_id
             WHERE w.source_id = %s
             ORDER BY c.score DESC, w.start_s
            """, (source_id,))
        for row in candidates:
            row["id"] = str(row["id"])
        events = db.fetch_all(
            "SELECT kind, t_start, t_end, value_milli FROM signal_events WHERE source_id = %s "
            "ORDER BY t_start NULLS FIRST", (source_id,))
        windows = db.fetch_all(
            "SELECT id, idx, start_s, end_s, boundary_kind, ml_state FROM windows "
            "WHERE source_id = %s ORDER BY idx", (source_id,))
        for row in windows:
            row["id"] = str(row["id"])
        return {"candidates": candidates, "events": events, "windows": windows}

    @router.get("/sources/{source_id}/signals")
    def source_signals(request: Request, source_id: str,
                       from_s: float = Query(0.0, alias="from"),
                       to: float | None = None):
        db = request.app.state.db
        series = pipeline.load_series(db, source_id)
        start = max(0, int(from_s))
        end = int(to) if to is not None else None
        return {"from": start, "to": end,
                "series": {name: values[start:end] for name, values in series.items()}}

    @router.get("/sources/{source_id}/original")
    def source_original(request: Request, source_id: str):
        _require_token(request, allow_query=True)
        db = request.app.state.db
        row = db.fetch_one("SELECT storage_path FROM sources WHERE id = %s", (source_id,))
        if row is None:
            raise HTTPException(status_code=404, detail="source not found")
        return range_response(row["storage_path"], request.headers.get("range"))

    # --------------------------------------------------------------- clips
    @router.get("/clips/{clip_id}/proxy")
    def clip_proxy(request: Request, clip_id: str):
        _require_token(request, allow_query=True)
        db = request.app.state.db
        row = db.fetch_one("SELECT path FROM clips WHERE id = %s", (clip_id,))
        if row is None:
            raise HTTPException(status_code=404, detail="clip not found")
        return range_response(row["path"], request.headers.get("range"))

    # ----------------------------------------------------------- candidates
    @router.get("/candidates")
    def list_candidates(request: Request, state: str | None = None, limit: int | None = None,
                        offset: int = 0):
        db = request.app.state.db
        cfg = request.app.state.cfg
        limit = int(limit or cfg.queue_limit)
        limit = max(1, min(int(cfg.queue_limit_max), limit))
        offset = max(0, int(offset))
        entries: list[dict] = []
        # ml_failed spans are pinned above the ranked candidates on every page
        # (a machine-blind span ranks before anything the machine ranked);
        # offset/limit paginate the candidate side, which is the corpus-scale
        # term, so the 2 s poll stays bounded no matter how large the queue is
        ml_failed = db.fetch_all(
            """
            SELECT w.id AS window_id, w.source_id, w.idx, w.start_s, w.end_s,
                   w.ml_failure_reason, s.original_name
              FROM windows w JOIN sources s ON s.id = w.source_id
             WHERE w.ml_state = 'failed' AND w.ml_acked_at IS NULL
             ORDER BY w.source_id, w.idx
            """)
        for row in ml_failed:
            entries.append({
                "kind": "ml_failed",
                "window_id": str(row["window_id"]),
                "source_id": str(row["source_id"]),
                "source_name": row["original_name"],
                "start_s": float(row["start_s"]),
                "end_s": float(row["end_s"]),
                "reason": row["ml_failure_reason"] or "",
                "label": "detection incomplete — manual review required",
            })
        total_ml_failed = len(entries)
        filters = ""
        params: list[Any] = []
        if state:
            filters = " AND c.state = %s"
            params.append(state)
        total_candidates = int(db.fetch_one(
            """
            SELECT count(*) AS n
              FROM candidates c
              JOIN patterns p ON p.slug = c.pattern_slug AND p.version = c.pattern_version
             WHERE p.is_current
            """ + filters, tuple(params))["n"])
        page = db.fetch_all(
            """
            SELECT c.id, c.window_id, c.pattern_slug, c.pattern_version, c.score,
                   c.sentence, c.state, c.evidence, w.source_id, w.start_s, w.end_s,
                   s.origin, s.original_name,
                   (SELECT cl.id FROM clips cl WHERE cl.window_id = w.id
                     AND cl.kind = 'review_proxy' LIMIT 1) AS clip_id
              FROM candidates c
              JOIN windows w ON w.id = c.window_id
              JOIN sources s ON s.id = w.source_id
              JOIN patterns p ON p.slug = c.pattern_slug AND p.version = c.pattern_version
             WHERE p.is_current
            """ + filters + """
             ORDER BY c.score DESC, w.source_id, w.start_s, c.pattern_slug
             LIMIT %s OFFSET %s
            """, tuple(params) + (limit, offset))
        for row in page:
            evidence = row["evidence"]
            if isinstance(evidence, str):
                evidence = json.loads(evidence)
            entries.append({
                "kind": "candidate",
                "candidate_id": str(row["id"]),
                "window_id": str(row["window_id"]),
                "source_id": str(row["source_id"]),
                "source_name": row["original_name"],
                "clip_id": str(row["clip_id"]) if row["clip_id"] else None,
                "start_s": float(row["start_s"]),
                "end_s": float(row["end_s"]),
                "pattern_slug": row["pattern_slug"],
                "pattern_version": int(row["pattern_version"]),
                "score": int(row["score"]),
                "sentence": row["sentence"],
                "state": row["state"],
                "synthetic": bool(evidence.get("synthetic")),
                "demo": row["origin"] == "demo",
            })
        return {"total": total_candidates + total_ml_failed, "entries": entries,
                "limit": limit, "offset": offset}

    @router.get("/candidates/{candidate_id}")
    def candidate_detail(request: Request, candidate_id: str):
        db = request.app.state.db
        row = db.fetch_one(
            """
            SELECT c.*, w.source_id, w.start_s, w.end_s, w.idx AS window_idx,
                   w.ml_state, w.detections_summary, s.origin, s.original_name,
                   (SELECT cl.id FROM clips cl WHERE cl.window_id = w.id
                     AND cl.kind = 'review_proxy' LIMIT 1) AS clip_id
              FROM candidates c
              JOIN windows w ON w.id = c.window_id
              JOIN sources s ON s.id = w.source_id
             WHERE c.id = %s
            """, (candidate_id,))
        if row is None:
            raise HTTPException(status_code=404, detail="candidate not found")
        evidence = row["evidence"]
        if isinstance(evidence, str):
            evidence = json.loads(evidence)
        decisions = db.fetch_all(
            "SELECT id, action, note, reviewer, src_viewed, created_at FROM decisions "
            "WHERE candidate_id = %s ORDER BY created_at DESC", (candidate_id,))
        for decision in decisions:
            decision["id"] = str(decision["id"])
        summary = row.get("detections_summary")
        if isinstance(summary, str):
            summary = json.loads(summary)
        return {
            "candidate_id": str(row["id"]),
            "window_id": str(row["window_id"]),
            "source_id": str(row["source_id"]),
            "source_name": row["original_name"],
            "clip_id": str(row["clip_id"]) if row["clip_id"] else None,
            "start_s": float(row["start_s"]),
            "end_s": float(row["end_s"]),
            "pattern_slug": row["pattern_slug"],
            "pattern_version": int(row["pattern_version"]),
            "score": int(row["score"]),
            "sentence": row["sentence"],
            "state": row["state"],
            "evidence": evidence,
            "synthetic": bool(evidence.get("synthetic")),
            "demo": row["origin"] == "demo",
            "ml_state": row["ml_state"],
            "detections_summary": summary,
            "decisions": decisions,
        }

    @router.post("/candidates/{candidate_id}/decisions")
    def record_decision(request: Request, candidate_id: str, body: DecisionRequest):
        cfg = request.app.state.cfg
        db = request.app.state.db
        if body.action not in ("accept", "reject", "flag"):
            raise HTTPException(status_code=422, detail={
                "error": "bad_action", "allowed": ["accept", "reject", "flag"]})
        _vocab_guard(body.note)
        row = db.fetch_one(
            """
            SELECT c.id, c.window_id, c.score, c.sentence, c.pattern_slug, c.pattern_version,
                   w.source_id
              FROM candidates c JOIN windows w ON w.id = c.window_id WHERE c.id = %s
            """, (candidate_id,))
        if row is None:
            raise HTTPException(status_code=404, detail="candidate not found")
        state = {"accept": "accepted", "reject": "rejected", "flag": "flagged"}[body.action]
        with db.transaction() as cur:
            cur.execute(
                """
                INSERT INTO decisions (candidate_id, action, note, reviewer, src_viewed)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
                """,
                (candidate_id, body.action, body.note, cfg.reviewer_name, bool(body.src_viewed)),
            )
            decision_id = str(cur.fetchone()["id"])
            cur.execute("UPDATE candidates SET state = %s WHERE id = %s", (state, candidate_id))
            seq = ledger.append(cur, "DECISION_RECORDED", cfg.reviewer_name, {
                "reviewer": cfg.reviewer_name,
                "candidate_id": candidate_id,
                "action": body.action,
                "note": body.note,
                "src_viewed": bool(body.src_viewed),
                "pattern_slug": row["pattern_slug"],
                "pattern_version": int(row["pattern_version"]),
                "window_id": str(row["window_id"]),
                "source_id": str(row["source_id"]),
                "score": int(row["score"]),
                "sentence": row["sentence"],
            })
        return {"decision_id": decision_id, "state": state, "ledger_seq": seq["seq"],
                "reviewer": cfg.reviewer_name, "src_viewed": bool(body.src_viewed)}

    @router.post("/windows/{window_id}/ml_ack")
    def acknowledge_ml_failure(request: Request, window_id: str):
        cfg = request.app.state.cfg
        db = request.app.state.db
        row = db.fetch_one(
            "SELECT id, source_id, idx, start_s, end_s, ml_failure_reason, ml_state, "
            "ml_acked_at FROM windows WHERE id = %s", (window_id,))
        if row is None:
            raise HTTPException(status_code=404, detail="window not found")
        if row["ml_state"] != "failed":
            raise HTTPException(status_code=409, detail={
                "error": "window_not_failed", "detail": "this window has no failed detection"})
        if row["ml_acked_at"] is not None:
            raise HTTPException(status_code=409, detail={
                "error": "already_acknowledged",
                "detail": "this window's detection failure was already acknowledged"})
        with db.transaction() as cur:
            cur.execute("UPDATE windows SET ml_acked_at = now() WHERE id = %s", (window_id,))
            ledger.append(cur, "ML_FAILURE_ACKED", cfg.reviewer_name, {
                "window_id": str(row["id"]),
                "source_id": str(row["source_id"]),
                "reviewer": cfg.reviewer_name,
                "reason": row["ml_failure_reason"] or "",
                "start_ms": media.seconds_to_ms(float(row["start_s"])),
                "end_ms": media.seconds_to_ms(float(row["end_s"])),
            })
        return {"window_id": str(row["id"]), "acknowledged": True}

    # ------------------------------------------------------------- patterns
    @router.get("/patterns")
    def list_patterns(request: Request):
        db = request.app.state.db
        with db.connect() as conn:
            rows = patterns_mod.list_patterns(conn)
        for row in rows:
            if isinstance(row["rule"], str):
                row["rule"] = json.loads(row["rule"])
        return {"patterns": rows, "schema_endpoint": "/api/v1/patterns/schema"}

    @router.get("/patterns/schema")
    def pattern_schema(request: Request):
        return rules_mod.pattern_schema()

    @router.get("/patterns/stats")
    def pattern_stats(request: Request):
        db = request.app.state.db
        with db.connect() as conn:
            return {"stats": patterns_mod.stats(conn)}

    @router.put("/patterns/{slug}")
    def update_pattern(request: Request, slug: str, body: PatternRequest):
        cfg = request.app.state.cfg
        db = request.app.state.db
        _vocab_guard(body.title, body.sentence_template)
        try:
            scoring.validate_template(body.sentence_template)
        except scoring.TemplateError as exc:
            raise HTTPException(status_code=422, detail={
                "error": "template_error", "detail": str(exc)}) from exc
        document = {"slug": slug, "title": body.title,
                    "sentence_template": body.sentence_template, "rule": body.rule,
                    "weight": body.weight, "enabled": body.enabled}
        try:
            rules_mod.validate_rule(document)
        except rules_mod.RuleError as exc:
            raise HTTPException(status_code=422, detail={
                "error": "rule_error", "detail": str(exc)}) from exc
        with db.connect() as conn:
            result = patterns_mod.create_version(conn, slug, document, cfg.reviewer_name,
                                                 ledger_append=ledger.append)
        job_id = _enqueue(db, "rescore", detail=f"slug={slug}")
        return JSONResponse({"version": result["version"], "rescore_job_id": job_id},
                            status_code=202)

    # -------------------------------------------------------------- exports
    @router.post("/exports")
    def create_export(request: Request, body: ExportRequest):
        cfg = request.app.state.cfg
        db = request.app.state.db
        states = tuple(body.states or cfg.export_states)
        for state in states:
            if state not in ("open", "accepted", "rejected", "flagged"):
                raise HTTPException(status_code=422, detail={
                    "error": "bad_state", "allowed": ["open", "accepted", "rejected", "flagged"]})
        if body.cut_precision not in ("keyframe", "exact"):
            raise HTTPException(status_code=422, detail={
                "error": "bad_cut_precision", "allowed": ["keyframe", "exact"]})
        top_n = int(body.top_n or cfg.export_top_n)
        # §9: LONGWATCH_BLUR_FACES is the deployment's default for this option;
        # an explicit request value wins.  (The config knob was dead before —
        # the request model's False always shadowed it.)
        blur_faces = bool(body.blur_faces) if body.blur_faces is not None else bool(cfg.blur_faces)
        with db.transaction() as cur:
            cur.execute(
                """
                INSERT INTO exports (reviewer, top_n, states, blur_faces, cut_precision)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
                """,
                (cfg.reviewer_name, top_n, jobs_mod.jsonb(list(states)),
                 blur_faces, body.cut_precision),
            )
            export_id = str(cur.fetchone()["id"])
        job_id = _enqueue(db, "export", detail=f"export_id={export_id}")
        return {"export_id": export_id, "job_id": job_id}

    @router.get("/exports")
    def list_exports(request: Request):
        db = request.app.state.db
        rows = db.fetch_all(
            """
            SELECT e.id, e.reviewer, e.top_n, e.states, e.blur_faces, e.cut_precision,
                   e.bundle_path, e.manifest_sha256, e.created_at, e.signed_at,
                   j.id AS job_id, j.state AS job_state, j.stage, j.pct, j.error
              FROM exports e
              LEFT JOIN jobs j ON j.detail LIKE 'export_id=' || e.id || '%'
             ORDER BY e.created_at DESC LIMIT 50
            """)
        for row in rows:
            row["id"] = str(row["id"])
            if row.get("job_id"):
                row["job_id"] = str(row["job_id"])
            if isinstance(row.get("states"), str):
                row["states"] = json.loads(row["states"])
            row["download_ready"] = bool(row["bundle_path"] and os.path.isdir(row["bundle_path"]))
        return {"exports": rows}

    @router.get("/exports/{export_id}")
    def export_detail(request: Request, export_id: str):
        db = request.app.state.db
        row = db.fetch_one("SELECT * FROM exports WHERE id = %s", (export_id,))
        if row is None:
            raise HTTPException(status_code=404, detail="export not found")
        row["id"] = str(row["id"])
        if isinstance(row.get("states"), str):
            row["states"] = json.loads(row["states"])
        job = db.fetch_one("SELECT id, state, stage, pct, error FROM jobs "
                           "WHERE detail LIKE %s ORDER BY created_at DESC LIMIT 1",
                           (f"export_id={export_id}%",))
        if job:
            job["id"] = str(job["id"])
        row["job"] = job
        return row

    @router.get("/exports/{export_id}/download")
    def export_download(request: Request, export_id: str):
        db = request.app.state.db
        row = db.fetch_one("SELECT bundle_path FROM exports WHERE id = %s", (export_id,))
        if row is None or not row["bundle_path"]:
            raise HTTPException(status_code=404, detail="export bundle is not ready")
        bundle = Path(row["bundle_path"])
        zip_path = bundle.with_suffix(".zip")
        if not zip_path.is_file():
            from .export_bundle import _zip_bundle
            _zip_bundle(bundle, zip_path)
        return FileResponse(str(zip_path), media_type="application/zip",
                            filename=zip_path.name)

    @router.get("/jobs/{job_id}")
    def job_status(request: Request, job_id: str):
        db = request.app.state.db
        row = jobs_mod.get_job(db, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="job not found")
        return {
            "job_id": str(row["id"]),
            "kind": row["kind"],
            "state": row["state"],
            "stage": row["stage"],
            "pct": int(row["pct"]),
            "attempts": int(row["attempts"]),
            "detail": row["detail"],
            "error": row["error"],
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
        }

    # ------------------------------------------------------- retention/ledger
    @router.post("/retention/purge")
    def retention_purge(request: Request):
        db = request.app.state.db
        job_id = _enqueue(db, "purge", detail="retention purge")
        return JSONResponse({"job_id": job_id}, status_code=202)

    @router.get("/ledger/verify")
    def ledger_verify(request: Request):
        db = request.app.state.db
        with db.connect() as conn:
            result = ledger.verify(conn)
        return result

    # ---------------------------------------------------------------- system
    @router.get("/system/status")
    def system_status(request: Request):
        app = request.app
        cfg = app.state.cfg
        db = app.state.db
        status: dict[str, Any] = {"service": "longwatch", "database": "unknown",
                                  "model": "unknown", "class_map": "unknown",
                                  "workers": {}, "offline_guard": {}, "ml_failed_count": 0,
                                  "key": {}, "inbox": {"dir": cfg.inbox_dir, "enabled": bool(cfg.inbox_dir)}}
        try:
            db.ping()
            status["database"] = "ok"
        except Exception as exc:  # noqa: BLE001
            status["database"] = f"error: {exc}"
        try:
            detector = app.state.detector_factory()
            contract = detector.contract()
            status["model"] = {"ok": True, **contract,
                               "provenance": model_provenance(cfg)}
            status["class_map"] = {"ok": True, "classes": contract["classes"]}
        except Exception as exc:  # noqa: BLE001
            status["model"] = {"ok": False, "error": str(exc),
                               "provenance": model_provenance(cfg)}
            status["class_map"] = {"ok": False, "error": str(exc)}
        pool = getattr(app.state, "workers", None)
        status["workers"] = pool.status() if pool else {"media": {}, "utility": {}}
        from . import offline_guard
        status["offline_guard"] = offline_guard.status()
        try:
            row = db.fetch_one("SELECT count(*) AS n FROM windows WHERE ml_state = 'failed' "
                               "AND ml_acked_at IS NULL")
            status["ml_failed_count"] = int(row["n"])
        except Exception:  # noqa: BLE001
            status["ml_failed_count"] = -1
        try:
            from . import signing
            identity = signing.key_identity(cfg.key_path)
            status["key"] = {"fingerprint": identity["fingerprint"],
                             "public_pem": identity["public_pem"]}
        except Exception as exc:  # noqa: BLE001
            status["key"] = {"error": str(exc)}
        status["disclosures"] = {
            "integrity_statement": INTEGRITY_STATEMENT,
            "time_basis": TIME_BASIS,
            "review_medium": review_medium(cfg.proxy_long_edge),
            "model_limits": MODEL_LIMITS + provenance_note(model_provenance(cfg)),
            "person_absent": (
                "non-detection is treated as absence for rule purposes; a detector miss "
                "reads as absence, which is why the detector-limits disclosure is "
                "permanent rather than conditional"
            ),
            "trust_boundary": (
                "with LONGWATCH_AUTH_TOKEN unset, anyone who can reach the port is the "
                "operator; deploy on loopback or behind an operator-controlled proxy if a "
                "boundary is needed"
            ),
        }
        status["auth"] = {"token_required": bool(cfg.auth_token),
                          "trust_boundary": (
                              "single-operator offline appliance; the token is an "
                              "accident guard, not access control")}
        # The deployment's export defaults (§12): the workbench's export form
        # initializes from these so LONGWATCH_BLUR_FACES / _EXPORT_TOP_N /
        # _EXPORT_STATES are the defaults everywhere, not only for API callers
        # that omit the fields.
        status["export_defaults"] = {"blur_faces": bool(cfg.blur_faces),
                                     "top_n": int(cfg.export_top_n),
                                     "states": list(cfg.export_states)}
        return status

    return router


MEDIA_PATH_RE = re.compile(
    r"^/api/v1/(clips/[^/]+/proxy|sources/[^/]+/original)$")


def install_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def referrer_and_token(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and not MEDIA_PATH_RE.match(path):
            # The two byte-range media routes check the token themselves (they
            # also accept ?token=, because <video> cannot send headers).
            try:
                _require_token(request)
            except HTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
                response.headers["Referrer-Policy"] = "no-referrer"
                return response
        response = await call_next(request)
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


def install_routes(app: FastAPI) -> None:
    app.include_router(build_router())

    @app.get("/healthz")
    def healthz(request: Request):
        db = request.app.state.db
        checks: dict[str, Any] = {}
        ok = True
        try:
            db.ping()
            checks["database"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["database"] = f"error: {exc}"
            ok = False
        try:
            detector = request.app.state.detector_factory()
            checks["model"] = detector.self_test()
        except Exception as exc:  # noqa: BLE001
            checks["model"] = {"ok": False, "error": str(exc)}
            ok = False
        status = 200 if ok else 503
        return JSONResponse({"ok": ok, "checks": checks}, status_code=status)

    @app.get("/")
    def spa_root(request: Request):
        return _spa_response("index.html")

    @app.get("/{path:path}")
    def spa_assets(request: Request, path: str):
        if path.startswith(("api/", "healthz", "openapi.json", "docs", "redoc")):
            raise HTTPException(status_code=404, detail="not found")
        return _spa_response(path)
