"""The five-stage pipeline: Ingest → Signals → Detect → Score → Review.

The pipeline is driven by the job pool.  Media work happens outside any DB
transaction; then a short, SQL-only transaction writes the state transition and
the ledger append together.  Every derivation is idempotent on its natural key,
so a crash between media work and its recording transaction simply re-does the
work — it never duplicates it.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from pathlib import Path

from . import detect as detect_mod
from . import jobs as jobs_mod
from . import ledger, media, rules as rules_mod, scoring
from . import signals as signals_mod, vocab
from . import windowing

log = logging.getLogger("longwatch.pipeline")

INGEST_STAGES = ("probe", "windows", "proxies", "signals", "detect", "score")


class PipelineError(RuntimeError):
    """A stage failed; the message is preserved verbatim on the job."""


def _stage_index(stage: str | None) -> int:
    if stage in INGEST_STAGES:
        return INGEST_STAGES.index(stage)
    return -1


# ------------------------------------------------------------------- sources
def find_source_by_digest(db, sha256: str) -> dict | None:
    return db.fetch_one("SELECT * FROM sources WHERE sha256 = %s", (sha256,))


def register_source(db, *, storage_path: str, origin: str, original_name: str,
                    sha256: str, size_bytes: int, actor: str) -> tuple[dict, bool]:
    """Register (or recognise) a source row.  Identity is the digest, not the path.

    The duplicate policy is race-safe (§8.1: "no raw constraint violation ever
    surfaces to a client"): two concurrent first uploads of the same bytes both
    pass the pre-check, the loser's INSERT meets the unique digest constraint,
    and the loser is folded into the winner's row with a SOURCE_DUPLICATE
    sighting — never a 500.
    """
    import psycopg.errors

    existing = find_source_by_digest(db, sha256)
    if existing is not None:
        with db.transaction() as cur:
            ledger.append(cur, "SOURCE_DUPLICATE", actor, {
                "source_id": str(existing["id"]),
                "sha256": sha256,
                "new_path": storage_path,
                "origin": origin,
            })
        return existing, False
    try:
        with db.transaction() as cur:
            cur.execute(
                """
                INSERT INTO sources (origin, original_name, storage_path, sha256,
                                     size_bytes, probe, state)
                VALUES (%s, %s, %s, %s, %s, '{}'::jsonb, 'registered')
                RETURNING *
                """,
                (origin, original_name, storage_path, sha256, int(size_bytes)),
            )
            row = cur.fetchone()
            ledger.append(cur, "SOURCE_REGISTERED", actor, {
                "source_id": str(row["id"]),
                "origin": origin,
                "original_name": original_name,
                "sha256": sha256,
                "size_bytes": int(size_bytes),
            })
        return row, True
    except psycopg.errors.UniqueViolation:
        winner = find_source_by_digest(db, sha256)
        if winner is None:
            raise  # the collision was not the digest (e.g. a reused path)
        with db.transaction() as cur:
            ledger.append(cur, "SOURCE_DUPLICATE", actor, {
                "source_id": str(winner["id"]),
                "sha256": sha256,
                "new_path": storage_path,
                "origin": origin,
            })
        return winner, False


def scan_inbox(db, cfg, actor: str) -> list[dict]:
    """One scan of the configured inbox directory.

    Shared by ``POST /api/v1/ingest/scan`` and the optional
    ``LONGWATCH_INBOX_POLL_S`` background cadence: files are registered in
    place (a 50 GB drive's contents are never copied), duplicates by digest
    record a ``SOURCE_DUPLICATE`` sighting, and each new file queues an ingest
    job.
    """
    inbox = Path(cfg.inbox_dir) if cfg.inbox_dir else None
    if inbox is None:
        return []
    if not inbox.is_dir():
        # a configured-but-vanished inbox is a loud condition, not an empty one —
        # the poller logs it, the route names it in a 400
        raise FileNotFoundError(f"inbox directory {inbox} does not exist")
    results = []
    for path in sorted(p for p in inbox.iterdir() if p.is_file()):
        digest = media.sha256_file(path)
        existing = find_source_by_digest(db, digest)
        if existing is not None:
            with db.transaction() as cur:
                ledger.append(cur, "SOURCE_DUPLICATE", actor, {
                    "source_id": str(existing["id"]), "sha256": digest,
                    "new_path": str(path), "origin": "inbox",
                })
            results.append({"source_id": str(existing["id"]), "duplicate": True})
            continue
        source, _ = register_source(
            db, storage_path=str(path), origin="inbox", original_name=path.name,
            sha256=digest, size_bytes=path.stat().st_size, actor=actor)
        job_id = jobs_mod.enqueue(db, "ingest", source_id=str(source["id"]),
                                  detail="inbox scan")
        results.append({"source_id": str(source["id"]), "job_id": job_id})
    return results


def _source_file_missing(db, source: dict, stage: str, actor: str) -> bool:
    """The pre-stat rule: no ffmpeg invocation, no timeout wait, no hang."""
    if os.path.exists(source["storage_path"]):
        return False
    with db.transaction() as cur:
        cur.execute("UPDATE sources SET state = 'missing', failure_reason = %s WHERE id = %s",
                    (f"source file absent at stage {stage}", source["id"]))
        cur.execute(
            """
            UPDATE windows SET ml_state = 'failed', ml_failure_reason = 'source missing'
             WHERE source_id = %s AND (ml_state IS NULL OR ml_state = 'ok')
            """,
            (source["id"],),
        )
        ledger.append(cur, "SOURCE_MISSING", actor, {
            "source_id": str(source["id"]),
            "storage_path": source["storage_path"],
            "stage": stage,
        })
    return True


def load_series(db, source_id: str) -> dict[str, list[int]]:
    rows = db.fetch_all(
        "SELECT series FROM signal_blocks WHERE source_id = %s ORDER BY idx", (source_id,))
    series: dict[str, list[int]] = {}
    for row in rows:
        block = row["series"]
        if isinstance(block, str):
            block = json.loads(block)
        for name, values in block.items():
            series.setdefault(name, []).extend(int(v) for v in values)
    return series


def load_events(db, source_id: str) -> list[dict]:
    rows = db.fetch_all(
        "SELECT kind, t_start, t_end, value_milli, meta FROM signal_events "
        "WHERE source_id = %s ORDER BY t_start NULLS FIRST", (source_id,))
    for row in rows:
        if isinstance(row.get("meta"), str):
            row["meta"] = json.loads(row["meta"])
    return rows


def load_intervals(db, source_id: str) -> list[dict]:
    """Presence intervals with the per-window tolerance derived from the summary."""
    rows = db.fetch_all(
        """
        SELECT p.class_name, p.t_start, p.t_end, p.samples, p.conf_max_milli, p.synthetic,
               p.window_id, w.detections_summary, w.idx AS window_idx
          FROM presence_intervals p
          JOIN windows w ON w.id = p.window_id
         WHERE w.source_id = %s
         ORDER BY p.t_start
        """,
        (source_id,),
    )
    intervals = []
    for row in rows:
        summary = row["detections_summary"]
        if isinstance(summary, str):
            summary = json.loads(summary)
        interval_ms = 0
        if summary:
            entry = summary.get(row["class_name"]) or {}
            interval_ms = int(entry.get("sample_interval_ms", 0))
        intervals.append({
            "class_name": row["class_name"],
            "t_start": float(row["t_start"]),
            "t_end": float(row["t_end"]),
            "samples": int(row["samples"]),
            "conf_max_milli": int(row["conf_max_milli"]),
            "synthetic": bool(row["synthetic"]),
            "window_id": str(row["window_id"]),
            "tolerance_s": (2.0 * interval_ms / 1000.0) if interval_ms else 2.0,
        })
    return detect_mod.merge_intervals(intervals, 2.0)


# -------------------------------------------------------------------- stages
def _stage_probe(db, job, source, cfg, actor) -> None:
    if source.get("probe") and source.get("duration_s"):
        return
    try:
        document = media.probe(source["storage_path"], cfg.ffmpeg_hang_base_s,
                               cfg.ffmpeg_hang_factor)
    except media.MediaError as exc:
        with db.transaction() as cur:
            cur.execute(
                "UPDATE sources SET state = 'unreadable', failure_reason = %s WHERE id = %s",
                (exc.stderr or str(exc), source["id"]),
            )
            ledger.append(cur, "SOURCE_UNREADABLE", actor, {
                "source_id": str(source["id"]),
                "original_name": source["original_name"],
                "probe_stderr": (exc.stderr or str(exc))[:4000],
            })
        raise PipelineError(f"source is unreadable: {exc}") from exc
    facts = media.probe_facts(document)
    with db.transaction() as cur:
        cur.execute(
            """
            UPDATE sources SET duration_s = %s, fps = %s, width = %s, height = %s,
                   video_codec = %s, audio_codec = %s, has_audio = %s, probe = %s
             WHERE id = %s
            """,
            (facts["duration_s"], facts["fps"], facts["width"], facts["height"],
             facts["video_codec"], facts["audio_codec"], facts["has_audio"],
             jobs_mod.jsonb(document), source["id"]),
        )


def _stage_windows(db, job, source, cfg, actor) -> int:
    duration = float(source["duration_s"] or 0.0)
    if duration <= 0:
        raise PipelineError("source duration is unknown; cannot plan windows")
    cuts = media.scene_cut_times(source["storage_path"], duration, cfg.scene_t0,
                                 cfg.ffmpeg_hang_base_s, cfg.ffmpeg_hang_factor)
    plan = windowing.plan_windows(duration, cuts, cfg.coverage_s, cfg.window_min_s,
                                  cfg.window_max_s)
    with db.transaction() as cur:
        cur.execute("DELETE FROM windows WHERE source_id = %s", (source["id"],))
        for window in plan:
            cur.execute(
                """
                INSERT INTO windows (source_id, idx, start_s, end_s, boundary_kind, scene_score)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (source["id"], window.idx, window.start_s, window.end_s,
                 window.boundary_kind, window.scene_score),
            )
            window_id = str(cur.fetchone()["id"])
            ledger.append(cur, "WINDOW_PLANNED", actor, {
                "source_id": str(source["id"]),
                "window_id": window_id,
                "idx": int(window.idx),
                "start_ms": int(round(window.start_s * 1000.0)),
                "end_ms": int(round(window.end_s * 1000.0)),
                "boundary_kind": window.boundary_kind,
                "window_count": len(plan),
            })
        cur.execute("UPDATE sources SET state = 'windowed' WHERE id = %s", (source["id"],))
    return len(plan)


def _stage_proxies(db, job, source, cfg, actor) -> dict:
    started = time.monotonic()
    windows = db.fetch_all(
        "SELECT id, idx, start_s, end_s FROM windows WHERE source_id = %s ORDER BY idx",
        (source["id"],))
    target_dir = Path(cfg.data_dir) / "proxies" / str(source["id"])
    target_dir.mkdir(parents=True, exist_ok=True)
    derived = 0
    for window in windows:
        existing = db.fetch_one(
            "SELECT id, path FROM clips WHERE window_id = %s AND kind = 'review_proxy'",
            (window["id"],))
        if existing is not None and os.path.exists(existing["path"]):
            continue
        out = target_dir / f"window-{int(window['idx']):05d}.mp4"
        tmp = out.with_suffix(".tmp.mp4")
        argv = media.proxy_command(source["storage_path"], tmp, float(window["start_s"]),
                                   float(window["end_s"]) - float(window["start_s"]),
                                   cfg.proxy_long_edge, bool(source["has_audio"]))
        try:
            media.run_ffmpeg(argv, media.hang_timeout(
                cfg.ffmpeg_hang_base_s, cfg.ffmpeg_hang_factor,
                float(window["end_s"]) - float(window["start_s"])))
        except media.MediaError as exc:
            if _source_file_missing(db, source, "proxies", actor):
                raise PipelineError("source file absent during proxy derivation") from exc
            raise
        os.replace(tmp, out)
        digest = media.sha256_file(out)
        size = out.stat().st_size
        with db.transaction() as cur:
            # a clip row whose file vanished (operator action, partial purge)
            # is stale: replace it in the same transaction as the new row so
            # (window_id, 'review_proxy') never resolves to a missing file
            cur.execute(
                "DELETE FROM clips WHERE window_id = %s AND kind = 'review_proxy'",
                (window["id"],))
            cur.execute(
                """
                INSERT INTO clips (window_id, kind, requested_start_ms, actual_start_ms,
                                   end_ms, path, sha256, size_bytes, cut_mode, ffmpeg_cmd)
                VALUES (%s, 'review_proxy', %s, %s, %s, %s, %s, %s, NULL, %s)
                RETURNING id
                """,
                (window["id"],
                 media.seconds_to_ms(float(window["start_s"])),
                 media.seconds_to_ms(float(window["start_s"])),
                 media.seconds_to_ms(float(window["end_s"])),
                 str(out), digest, int(size), " ".join(argv)),
            )
            clip_id = str(cur.fetchone()["id"])
            ledger.append(cur, "CLIP_DERIVED", actor, {
                "source_id": str(source["id"]),
                "window_id": str(window["id"]),
                "clip_id": clip_id,
                "kind": "review_proxy",
                "sha256": digest,
                "requested_start_ms": media.seconds_to_ms(float(window["start_s"])),
                "actual_start_ms": media.seconds_to_ms(float(window["start_s"])),
                "end_ms": media.seconds_to_ms(float(window["end_s"])),
                "cut_mode": None,
            })
        derived += 1
    elapsed = time.monotonic() - started
    with db.transaction() as cur:
        cur.execute("UPDATE sources SET state = 'proxied' WHERE id = %s", (source["id"],))
    return {"derived": derived, "seconds": round(elapsed, 3)}


def _stage_signals(db, job, source, cfg, actor) -> dict:
    result = signals_mod.run_signals(source["storage_path"], float(source["duration_s"]), cfg)
    with db.transaction() as cur:
        cur.execute("DELETE FROM signal_blocks WHERE source_id = %s", (source["id"],))
        cur.execute("DELETE FROM signal_events WHERE source_id = %s", (source["id"],))
        for block in result.blocks:
            cur.execute(
                "INSERT INTO signal_blocks (source_id, idx, start_s, series) VALUES (%s, %s, %s, %s)",
                (source["id"], block["idx"], block["start_s"], jobs_mod.jsonb(block["series"])),
            )
        for event in result.events:
            cur.execute(
                """
                INSERT INTO signal_events (source_id, kind, t_start, t_end, value_milli, meta)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (source["id"], event["kind"], event.get("t_start"), event.get("t_end"),
                 event.get("value_milli"), jobs_mod.jsonb(event.get("meta") or {})),
            )
        cur.execute("UPDATE sources SET signal_summary = %s, state = 'analyzed' WHERE id = %s",
                    (jobs_mod.jsonb(result.summary), source["id"]))
        ledger.append(cur, "SIGNALS_COMPLETED", actor, {
            "source_id": str(source["id"]),
            "blocks": len(result.blocks),
            "events": len(result.events),
            "has_audio": bool(result.has_audio),
        })
    return {"blocks": len(result.blocks), "events": len(result.events),
            "summary": result.summary, "series": result.series}


def _window_motion(series: dict, start_s: float, end_s: float) -> int:
    values = series.get("motion") or []
    first = max(0, int(start_s))
    last = min(len(values), int(end_s) + 1)
    chunk = values[first:last]
    if not chunk:
        return 0
    return int(round(sum(chunk) / float(len(chunk))))


def _stage_detect(db, job, source, cfg, actor, detector) -> dict:
    windows = db.fetch_all(
        "SELECT id, idx, start_s, end_s, ml_state FROM windows "
        "WHERE source_id = %s ORDER BY idx",
        (source["id"],))
    series = load_series(db, source["id"])
    total_intervals = 0
    skipped_ok = 0
    for window in windows:
        # resume is per-window: a window whose detection committed stays
        # committed, so a crash at window 4,000 of 4,800 never re-decodes the
        # first 4,000; only never-evaluated or failed windows are processed
        if window["ml_state"] == "ok":
            skipped_ok += 1
            continue
        if _source_file_missing(db, source, "detect", actor):
            raise PipelineError("source file absent during detection")
        motion = _window_motion(series, float(window["start_s"]), float(window["end_s"]))
        try:
            result = detector.run_window(source["storage_path"], window, motion,
                                         int(source["width"] or 640),
                                         int(source["height"] or 360))
        except detect_mod.MissingSource as exc:
            _source_file_missing(db, source, "detect", actor)
            raise PipelineError(str(exc)) from exc
        except detect_mod.ModelContractError:
            raise
        except media.MediaError as exc:
            with db.transaction() as cur:
                cur.execute(
                    "UPDATE windows SET ml_state = 'failed', ml_failure_reason = %s WHERE id = %s",
                    (str(exc)[:2000], window["id"]),
                )
                ledger.append(cur, "DETECTION_COMPLETED", actor, {
                    "source_id": str(source["id"]),
                    "window_id": str(window["id"]),
                    "ml_state": "failed",
                    "sample_interval_ms": 0,
                    "positive_frames": 0,
                })
            continue
        tolerance = 2.0 * (result.sample_interval_ms / 1000.0)
        merged = detect_mod.merge_intervals(
            [{**interval, "tolerance_s": tolerance, "window_ids": [str(window["id"])]}
             for interval in result.intervals], tolerance)
        with db.transaction() as cur:
            cur.execute("DELETE FROM presence_intervals WHERE window_id = %s", (window["id"],))
            for interval in merged:
                cur.execute(
                    """
                    INSERT INTO presence_intervals (window_id, class_name, t_start, t_end,
                                                    samples, conf_max_milli, synthetic)
                    VALUES (%s, %s, %s, %s, %s, %s, false)
                    """,
                    (window["id"], interval["class_name"], interval["t_start"],
                     interval["t_end"], int(interval.get("samples", 0)),
                     int(interval.get("conf_max_milli", 0))),
                )
            cur.execute(
                """
                UPDATE windows SET ml_state = 'ok', ml_failure_reason = NULL,
                       detections_summary = %s, detections = %s
                 WHERE id = %s
                """,
                (jobs_mod.jsonb(result.summary),
                 jobs_mod.jsonb(result.detections_blob) if result.detections_blob else None,
                 window["id"]),
            )
            positives = sum(int(entry.get("positive_frames", 0))
                            for entry in result.summary.values())
            ledger.append(cur, "DETECTION_COMPLETED", actor, {
                "source_id": str(source["id"]),
                "window_id": str(window["id"]),
                "ml_state": "ok",
                "sample_interval_ms": int(result.sample_interval_ms),
                "positive_frames": positives,
            })
        total_intervals += len(merged)
    return {"windows": len(windows), "intervals": total_intervals,
            "resumed_windows_skipped": skipped_ok}


def score_source(db, source: dict, actor: str, job=None, slug: str | None = None) -> dict:
    """Evaluate the current patterns over every window of one source.

    Pure rule evaluation over stored signals and presence intervals — no media
    I/O.  Returns counts for the ledger and the caller's job detail.
    """
    intervals = load_intervals(db, source["id"])
    series = load_series(db, source["id"])
    events = load_events(db, source["id"])
    summary = source.get("signal_summary") or {}
    if isinstance(summary, str):
        summary = json.loads(summary)
    windows = db.fetch_all(
        "SELECT id, idx, start_s, end_s FROM windows WHERE source_id = %s ORDER BY idx",
        (source["id"],))
    with db.connect() as conn:
        with conn.cursor() as cur:
            if slug:
                cur.execute(
                    "SELECT slug, version, title, sentence_template, rule, weight FROM patterns "
                    "WHERE slug = %s AND is_current AND enabled", (slug,))
            else:
                cur.execute(
                    "SELECT slug, version, title, sentence_template, rule, weight FROM patterns "
                    "WHERE is_current AND enabled ORDER BY slug")
            current = list(cur.fetchall())
    created = 0
    skipped = 0
    for pattern in current:
        rule = pattern["rule"]
        if isinstance(rule, str):
            rule = json.loads(rule)
        pattern_doc = {**pattern, "rule": rule}
        for window in windows:
            ctx = rules_mod.EvalContext(window=window, intervals=intervals, series=series,
                                        summary=summary, events=events)
            try:
                candidates = scoring.evaluate_window(pattern_doc, ctx)
            except scoring.RenderError as exc:
                log.error("rendered-sentence invariant violation for %s: %s",
                          pattern["slug"], exc)
                skipped += 1
                continue
            except vocab.VocabBreach as exc:
                # Impossible by construction (templates are guarded at write time,
                # placeholder values are integers or fixed class names); enforced
                # anyway: the candidate is not persisted.
                log.error("VOCAB invariant violation: rendered sentence for %s carries %r",
                          pattern["slug"], exc.term)
                skipped += 1
                continue
            except Exception as exc:  # a malformed rule must not kill the stage
                log.error("rule evaluation failed for %s: %s", pattern["slug"], exc)
                skipped += 1
                continue
            for candidate in candidates:
                with db.transaction() as cur:
                    cur.execute(
                        """
                        INSERT INTO candidates (window_id, pattern_slug, pattern_version,
                                                score, sentence, evidence)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (window_id, pattern_slug, pattern_version) DO NOTHING
                        RETURNING id
                        """,
                        (window["id"], pattern["slug"], int(pattern["version"]),
                         int(candidate.score), candidate.sentence,
                         jobs_mod.jsonb(candidate.evidence)),
                    )
                    row = cur.fetchone()
                    if row is None:
                        continue
                    created += 1
                    ledger.append(cur, "CANDIDATE_SCORED", actor, {
                        "candidate_id": str(row["id"]),
                        "source_id": str(source["id"]),
                        "window_id": str(window["id"]),
                        "pattern_slug": pattern["slug"],
                        "pattern_version": int(pattern["version"]),
                        "score": int(candidate.score),
                        "sentence": candidate.sentence,
                        "evidence": candidate.evidence,
                    })
    return {"candidates_created": created, "renders_skipped": skipped,
            "patterns": len(current), "windows": len(windows)}


# ------------------------------------------------------------------- ingest
def _resume_index(job: jobs_mod.Job) -> int:
    """Where an interrupted ingest resumes.

    ``job.stage`` names the stage that was *in flight* when the job stopped,
    and ``job.detail`` carries an explicit completion marker written only after
    a stage's work committed.  An interrupted stage is re-run (every stage is
    idempotent on its natural keys, so the re-run never duplicates an
    artifact); a stage the marker says completed is not repeated.  The naive
    alternative — resume *after* the recorded stage — skips the interrupted
    stage entirely, leaving windows without proxies or detection forever.
    """
    index = _stage_index(job.stage)
    if index < 0:
        return 0
    marker = f"stage {job.stage} complete"
    completed = (job.detail or "").startswith(marker)
    return index + 1 if completed else index


def run_ingest(db, job: jobs_mod.Job, cfg, detector_factory=None, actor: str | None = None) -> dict:
    """Run (or resume) an ingest job to completion."""
    actor = actor or cfg.reviewer_name
    source = db.fetch_one("SELECT * FROM sources WHERE id = %s", (job.source_id,))
    if source is None:
        raise PipelineError("ingest job has no source")
    if source["state"] == "unreadable":
        raise PipelineError("source is unreadable; no pipeline stages apply")
    start = _resume_index(job)
    result: dict = {}
    for stage in INGEST_STAGES[start:]:
        if _source_file_missing(db, source, stage, actor):
            raise PipelineError(f"source file absent before stage {stage}")
        jobs_mod.progress(db, job.id, stage=stage,
                          pct=int(round(100.0 * _stage_index(stage) / len(INGEST_STAGES))),
                          detail=f"stage {stage}")
        if stage == "probe":
            _stage_probe(db, job, source, cfg, actor)
            source = db.fetch_one("SELECT * FROM sources WHERE id = %s", (source["id"],))
        elif stage == "windows":
            result["windows"] = _stage_windows(db, job, source, cfg, actor)
        elif stage == "proxies":
            result["proxies"] = _stage_proxies(db, job, source, cfg, actor)
        elif stage == "signals":
            result["signals"] = _stage_signals(db, job, source, cfg, actor)
            source = db.fetch_one("SELECT * FROM sources WHERE id = %s", (source["id"],))
        elif stage == "detect":
            detector = detector_factory() if detector_factory else detect_mod.Detector(cfg)
            result["detect"] = _stage_detect(db, job, source, cfg, actor, detector)
            if source["origin"] == "demo":
                from . import demo as demo_mod
                result["synthetic_presence"] = demo_mod.inject_synthetic_presence(db, source, actor)
        elif stage == "score":
            result["score"] = score_source(db, source, actor, job=job)
        with db.transaction() as cur:
            # the completion marker is the resume contract: a stage is only
            # ever skipped when this write committed after its work did
            cur.execute("UPDATE jobs SET stage = %s, detail = %s WHERE id = %s",
                        (stage, f"stage {stage} complete", job.id))
    with db.transaction() as cur:
        cur.execute("UPDATE sources SET state = 'ready', failure_reason = NULL WHERE id = %s",
                    (source["id"],))
    return result


def _job_detail_value(detail: str | None, key: str, pattern: str) -> str | None:
    """Extract ``key=value`` from a job's detail text.

    The lease reclaimer appends `` [lease reclaimed]`` to the detail, so a
    naive ``split("key=")[-1].strip()`` silently breaks on any job that was
    ever reclaimed — the parsed value then names nothing and the job no-ops.
    """
    match = re.search(rf"{re.escape(key)}=({pattern})", detail or "")
    return match.group(1) if match else None


def run_rescore(db, job: jobs_mod.Job, cfg, actor: str | None = None) -> dict:
    """Pure rule re-evaluation over stored signals and presence intervals."""
    actor = actor or cfg.reviewer_name
    slug = _job_detail_value(job.detail, "slug", r"[a-z0-9_]{3,64}")
    sources = db.fetch_all("SELECT * FROM sources WHERE state IN ('ready','scored','analyzed')")
    totals = {"sources": len(sources), "windows": 0, "candidates_created": 0}
    carried = {"accepted": 0, "flagged": 0, "rejected": 0}
    for source in sources:
        outcome = score_source(db, source, actor, job=job, slug=slug)
        totals["windows"] += outcome["windows"]
        totals["candidates_created"] += outcome["candidates_created"]
    if slug:
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version FROM patterns WHERE slug = %s AND is_current", (slug,))
                row = cur.fetchone()
                version = int(row["version"]) if row else 0
                cur.execute(
                    """
                    SELECT state, count(*) AS n FROM candidates
                     WHERE pattern_slug = %s AND pattern_version < %s
                       AND state IN ('accepted','flagged','rejected')
                     GROUP BY state
                    """,
                    (slug, version),
                )
                for entry in cur.fetchall():
                    carried[entry["state"]] = int(entry["n"])
    else:
        version = 0
    with db.transaction() as cur:
        ledger.append(cur, "RESCORE_COMPLETED", actor, {
            "slug": slug or "",
            "version": int(version),
            "sources": int(totals["sources"]),
            "windows": int(totals["windows"]),
            "candidates_created": int(totals["candidates_created"]),
            "decided_carried": carried,
        })
    return {**totals, "decided_carried": carried, "slug": slug, "version": version}


def run_purge(db, job: jobs_mod.Job, cfg, actor: str | None = None) -> dict:
    """Delete LongWatch's own derived artifacts; never an original, never a source row."""
    actor = actor or cfg.reviewer_name
    destroyed = 0
    clips = db.fetch_all("SELECT id, path, sha256, kind FROM clips")
    for clip in clips:
        path = Path(clip["path"])
        digest = clip["sha256"]
        if path.exists():
            try:
                path.unlink()
            except OSError as exc:  # pragma: no cover - filesystem race
                log.warning("purge could not remove %s: %s", path, exc)
                continue
        with db.transaction() as cur:
            cur.execute("DELETE FROM clips WHERE id = %s", (clip["id"],))
            ledger.append(cur, "DERIVED_DESTROYED", actor, {
                "kind": clip["kind"], "path": str(path), "sha256": digest,
            })
        destroyed += 1
    exports = db.fetch_all("SELECT id, bundle_path, manifest_sha256 FROM exports")
    for export in exports:
        # the bundle is a directory tree plus its .zip sibling; the ledger event
        # may only claim a destruction that actually happened, so a stubborn
        # remnant skips the event and stays visible on the next purge
        bundle = export["bundle_path"]
        removed = True
        if bundle:
            bundle_dir = Path(bundle)
            zip_path = bundle_dir.with_suffix(".zip")
            try:
                if bundle_dir.is_dir():
                    shutil.rmtree(bundle_dir)
                elif bundle_dir.exists():
                    bundle_dir.unlink()
                if zip_path.exists():
                    zip_path.unlink()
            except OSError as exc:
                removed = False
                log.warning("purge could not remove export bundle %s: %s", bundle, exc)
        if not removed:
            continue
        with db.transaction() as cur:
            cur.execute("DELETE FROM exports WHERE id = %s", (export["id"],))
            ledger.append(cur, "DERIVED_DESTROYED", actor, {
                "kind": "export_bundle", "path": str(bundle or ""),
                "sha256": export["manifest_sha256"] or "0" * 64,
            })
        destroyed += 1
    return {"artifacts_destroyed": destroyed, "clips": len(clips), "exports": len(exports)}


def run_job(db, job: jobs_mod.Job, cfg, detector_factory=None) -> dict:
    if job.kind == "ingest":
        return run_ingest(db, job, cfg, detector_factory=detector_factory)
    if job.kind == "rescore":
        return run_rescore(db, job, cfg)
    if job.kind == "purge":
        return run_purge(db, job, cfg)
    if job.kind == "export":
        from . import export_bundle
        return export_bundle.run_export(db, job, cfg)
    raise PipelineError(f"unknown job kind {job.kind!r}")
