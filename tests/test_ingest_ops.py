"""Ingest-path operations — uploads, scan, resume, purge (§8.1, §13).

The upload path is the product's front door and the duplicate policy is a
stated contract (``200`` with the existing ``source_id`` and its state, never
a raw constraint violation); the resume contract is "an interrupted job
restarts from the interrupted stage without duplicating artifacts"; the purge
deletes only derived artifacts and its ledger events may only claim
destructions that actually happened.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from longwatch import app as app_mod, detect, jobs as jobs_mod, ledger, media, pipeline, workers
from longwatch.ledger import psycopg_json
from tests.conftest import make_video


def _client(db, cfg) -> TestClient:
    return TestClient(app_mod.create_app(cfg, db, start_workers=False, install_guard=False))


def _register_ready_source(db, cfg, tmp_path, seconds: float = 4.0) -> str:
    """A source row whose ingest has run to completion against a real video."""
    video = make_video(tmp_path / "ops.mp4", seconds=seconds)
    digest = media.sha256_file(video)
    source, created = pipeline.register_source(
        db, storage_path=str(video), origin="upload", original_name="ops.mp4",
        sha256=digest, size_bytes=video.stat().st_size, actor="operator")
    assert created
    jobs_mod.enqueue(db, "ingest", source_id=str(source["id"]), detail="test")
    assert workers.drain_once(db, cfg, ("ingest",),
                              detector_factory=lambda: detect.Detector(cfg)) is True
    state = db.fetch_one("SELECT state FROM sources WHERE id = %s", (source["id"],))
    assert state["state"] == "ready"
    return str(source["id"])


# ------------------------------------------------------------------- uploads
def test_upload_registers_and_a_second_upload_is_a_200_duplicate(db, cfg):
    client = _client(db, cfg)
    payload = b"\x00\x01longwatch-upload-bytes" * 4096
    first = client.post("/api/v1/uploads", files={"file": ("clip.mp4", payload, "video/mp4")})
    assert first.status_code == 202, first.text
    body = first.json()
    assert body["source_id"] and body["job_id"]
    source = db.fetch_one("SELECT * FROM sources WHERE id = %s", (body["source_id"],))
    import hashlib
    assert source["sha256"] == hashlib.sha256(payload).hexdigest()
    assert source["origin"] == "upload"
    registered = db.fetch_all(
        "SELECT 1 FROM ledger_events WHERE event_type = 'SOURCE_REGISTERED'")
    assert len(registered) == 1

    second = client.post("/api/v1/uploads", files={"file": ("clip-copy.mp4", payload, "video/mp4")})
    assert second.status_code == 200, second.text
    duplicate = second.json()
    assert duplicate["duplicate"] is True
    assert duplicate["source_id"] == body["source_id"]
    assert duplicate["state"] == source["state"]  # §8.1: the current state rides along
    assert db.fetch_one("SELECT count(*) AS n FROM sources")["n"] == 1
    sightings = db.fetch_all(
        "SELECT 1 FROM ledger_events WHERE event_type = 'SOURCE_DUPLICATE'")
    assert len(sightings) == 1
    # the duplicate's temp bytes are gone, not parked
    assert not list((Path(cfg.data_dir) / "tmp").glob("*.part"))


def test_upload_beyond_the_limit_is_a_mid_stream_413(db, cfg):
    cfg.upload_limit_mb = 1
    client = _client(db, cfg)
    payload = os.urandom(1024 * 1024 + 8)
    response = client.post("/api/v1/uploads", files={"file": ("big.bin", payload, "video/mp4")})
    assert response.status_code == 413, response.text
    assert response.json()["detail"]["error"] == "upload_too_large"
    assert db.fetch_one("SELECT count(*) AS n FROM sources")["n"] == 0
    assert not list((Path(cfg.data_dir) / "tmp").glob("*.part"))


def test_the_duplicate_policy_survives_the_concurrent_first_upload_race(db, cfg):
    """Two first uploads of the same bytes both pass the digest pre-check; the
    loser's INSERT meets the unique constraint and must fold into the winner's
    row — §8.1: no raw constraint violation ever surfaces to a client."""
    digest = "ab" * 32
    first, created = pipeline.register_source(
        db, storage_path="/data/originals/winner.mp4", origin="upload",
        original_name="winner.mp4", sha256=digest, size_bytes=100, actor="operator")
    assert created is True

    # the loser's view of the world: the winner committed between the pre-check
    # and the INSERT, so the pre-check (patched to the pre-commit view) misses
    original_lookup = pipeline.find_source_by_digest
    calls = {"n": 0}

    def pre_commit_view(db_, sha256_):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # the pre-check ran before the winner committed
        return original_lookup(db_, sha256_)

    import unittest.mock as mock

    with mock.patch.object(pipeline, "find_source_by_digest", pre_commit_view):
        loser, created = pipeline.register_source(
            db, storage_path="/data/originals/loser.mp4", origin="upload",
            original_name="loser.mp4", sha256=digest, size_bytes=100, actor="operator")
    assert created is False
    assert str(loser["id"]) == str(first["id"])
    assert db.fetch_one("SELECT count(*) AS n FROM sources")["n"] == 1
    sightings = db.fetch_all(
        "SELECT payload FROM ledger_events WHERE event_type = 'SOURCE_DUPLICATE'")
    assert len(sightings) == 1
    payload = sightings[0]["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["new_path"] == "/data/originals/loser.mp4"


def test_upload_streaming_is_semaphore_bounded(db, cfg):
    import asyncio

    app = app_mod.create_app(cfg, db, start_workers=False, install_guard=False)
    assert isinstance(app.state.upload_semaphore, asyncio.Semaphore)
    assert app.state.upload_semaphore._value == int(cfg.upload_concurrency)


# --------------------------------------------------------------------- scan
def test_scan_registers_and_marks_duplicates(db, cfg, tmp_path, require_ffmpeg):
    client = _client(db, cfg)
    assert client.post("/api/v1/ingest/scan").status_code == 400  # inbox disabled

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    # different durations → different bytes; identical files would collide by
    # digest, which is the duplicate policy doing exactly its job
    first_video = make_video(inbox / "a.mp4", seconds=2.0)
    make_video(inbox / "b.mp4", seconds=3.0)
    cfg.inbox_dir = str(inbox)

    response = client.post("/api/v1/ingest/scan")
    assert response.status_code == 200 or response.status_code == 202, response.text
    results = response.json()
    assert len(results) == 2
    assert all("source_id" in row and "job_id" in row for row in results)

    # the same file scanned again is a sighting, not a second source
    again = client.post("/api/v1/ingest/scan").json()
    assert len(again) == 2
    assert all(row.get("duplicate") is True for row in again)
    assert db.fetch_one("SELECT count(*) AS n FROM sources")["n"] == 2
    assert db.fetch_one("SELECT count(*) AS n FROM ledger_events "
                        "WHERE event_type = 'SOURCE_DUPLICATE'")["n"] == 2
    # registered in place, never copied (§8.1)
    paths = {row["storage_path"] for row in db.fetch_all("SELECT storage_path FROM sources")}
    assert str(first_video) in paths


def test_scan_of_a_missing_inbox_is_a_named_400(db, cfg, tmp_path):
    cfg.inbox_dir = str(tmp_path / "not-there")
    client = _client(db, cfg)
    response = client.post("/api/v1/ingest/scan")
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "inbox_missing"


# ------------------------------------------------------------------- resume
def test_an_interrupted_stage_is_re_run_not_skipped(db, cfg, tmp_path, require_ffmpeg,
                                                    require_model):
    """A job that died mid-'proxies' resumes AT proxies — never past it."""
    video = make_video(tmp_path / "resume.mp4", seconds=4.0)
    digest = media.sha256_file(video)
    source, _ = pipeline.register_source(
        db, storage_path=str(video), origin="upload", original_name="resume.mp4",
        sha256=digest, size_bytes=video.stat().st_size, actor="operator")
    source_id = str(source["id"])
    # the recorded state of a job that finished probe+windows and died in proxies
    pipeline._stage_probe(db, None, db.fetch_one("SELECT * FROM sources WHERE id = %s",
                                                 (source_id,)), cfg, "operator")
    pipeline._stage_windows(db, None, db.fetch_one("SELECT * FROM sources WHERE id = %s",
                                                   (source_id,)), cfg, "operator")
    job_id = jobs_mod.enqueue(db, "ingest", source_id=source_id, detail="test")
    db.execute("UPDATE jobs SET stage = 'proxies', detail = 'stage proxies', "
               "state = 'queued' WHERE id = %s", (job_id,))
    job = jobs_mod.get_job(db, job_id)
    pipeline.run_ingest(db, jobs_mod.Job.from_row(job), cfg,
                        detector_factory=lambda: detect.Detector(cfg), actor="operator")

    windows = db.fetch_all("SELECT id FROM windows WHERE source_id = %s", (source_id,))
    assert windows
    for window in windows:
        rows = db.fetch_all(
            "SELECT id FROM clips WHERE window_id = %s AND kind = 'review_proxy'",
            (window["id"],))
        assert len(rows) == 1, "exactly one proxy per window, no duplicates, none missing"
        assert Path(db.fetch_one("SELECT path FROM clips WHERE id = %s",
                                 (rows[0]["id"],))["path"]).is_file()
    assert db.fetch_one("SELECT state FROM sources WHERE id = %s",
                        (source_id,))["state"] == "ready"


def test_a_completed_stage_is_not_repeated_on_resume(db, cfg, tmp_path, require_ffmpeg,
                                                     require_model):
    """The completion marker is load-bearing: a stage whose marker committed
    is skipped; only the interrupted stage re-runs."""
    source_id = _register_ready_source(db, cfg, tmp_path / "done", seconds=4.0)
    before = db.fetch_one(
        "SELECT count(*) AS n FROM ledger_events WHERE event_type = 'WINDOW_PLANNED'")["n"]
    clips_before = db.fetch_one("SELECT count(*) AS n FROM clips")["n"]
    # a job re-driven with every stage marked complete does nothing again
    job_id = jobs_mod.enqueue(db, "ingest", source_id=source_id, detail="test")
    db.execute("UPDATE jobs SET stage = 'score', detail = 'stage score complete', "
               "state = 'queued' WHERE id = %s", (job_id,))
    job = jobs_mod.get_job(db, job_id)
    pipeline.run_ingest(db, jobs_mod.Job.from_row(job), cfg,
                        detector_factory=lambda: detect.Detector(cfg), actor="operator")
    assert db.fetch_one("SELECT count(*) AS n FROM ledger_events "
                        "WHERE event_type = 'WINDOW_PLANNED'")["n"] == before
    assert db.fetch_one("SELECT count(*) AS n FROM clips")["n"] == clips_before


def test_detect_resume_processes_only_unfinished_windows(db, cfg, tmp_path, require_ffmpeg,
                                                         require_model):
    source_id = _register_ready_source(db, cfg, tmp_path / "detect", seconds=4.0)
    window = db.fetch_one("SELECT id FROM windows WHERE source_id = %s ORDER BY idx LIMIT 1",
                          (source_id,))
    with db.transaction() as cur:
        cur.execute("UPDATE windows SET ml_state = NULL, detections_summary = NULL "
                    "WHERE id = %s", (window["id"],))
        cur.execute("DELETE FROM presence_intervals WHERE window_id = %s", (window["id"],))
    events_before = db.fetch_one(
        "SELECT count(*) AS n FROM ledger_events WHERE event_type = 'DETECTION_COMPLETED'")["n"]

    job_id = jobs_mod.enqueue(db, "ingest", source_id=source_id, detail="test")
    db.execute("UPDATE jobs SET stage = 'signals', detail = 'stage signals complete', "
               "state = 'queued' WHERE id = %s", (job_id,))
    job = jobs_mod.get_job(db, job_id)
    pipeline.run_ingest(db, jobs_mod.Job.from_row(job), cfg,
                        detector_factory=lambda: detect.Detector(cfg), actor="operator")

    events_after = db.fetch_one(
        "SELECT count(*) AS n FROM ledger_events WHERE event_type = 'DETECTION_COMPLETED'")["n"]
    assert events_after - events_before == 1, "only the unfinished window was re-detected"
    assert db.fetch_one("SELECT ml_state FROM windows WHERE id = %s",
                        (window["id"],))["ml_state"] == "ok"


# ------------------------------------------------------------------- purge
def test_purge_destroys_only_derived_artifacts_and_the_ledger_stays_honest(
        db, cfg, tmp_path, require_ffmpeg, require_model):
    source_id = _register_ready_source(db, cfg, tmp_path / "purge", seconds=4.0)
    source = db.fetch_one("SELECT * FROM sources WHERE id = %s", (source_id,))
    original_bytes = Path(source["storage_path"]).read_bytes()

    # a real export row with a bundle directory AND its zip on disk
    bundle_dir = Path(cfg.data_dir) / "exports" / "export_test"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "manifest.json").write_text("{}", encoding="utf-8")
    zip_path = bundle_dir.with_suffix(".zip")
    zip_path.write_bytes(b"pk-zip-bytes")
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO exports (reviewer, top_n, states, bundle_path, manifest_sha256)"
            " VALUES ('operator', 10, %s, %s, %s) RETURNING id",
            (psycopg_json(["accepted"]), str(bundle_dir), "a" * 64))

    job_id = jobs_mod.enqueue(db, "purge", detail="retention purge")
    assert workers.drain_once(db, cfg, ("purge",)) is True
    job = jobs_mod.get_job(db, job_id)
    assert job["state"] == "succeeded", job["error"]

    # every derived file is actually gone — the ledger claimed nothing false
    assert not bundle_dir.exists() and not zip_path.exists()
    assert db.fetch_one("SELECT count(*) AS n FROM clips")["n"] == 0
    assert db.fetch_one("SELECT count(*) AS n FROM exports")["n"] == 0
    destroyed = db.fetch_all(
        "SELECT payload FROM ledger_events WHERE event_type = 'DERIVED_DESTROYED'")
    kinds = set()
    for row in destroyed:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        kinds.add(payload["kind"])
    assert {"review_proxy", "export_bundle"} <= kinds

    # refusal #2: the original and its source row are untouched
    assert Path(source["storage_path"]).read_bytes() == original_bytes
    assert db.fetch_one("SELECT state FROM sources WHERE id = %s",
                        (source_id,))["state"] == "ready"
    with db.connect() as conn:
        assert ledger.verify(conn)["ok"] is True


def test_purge_of_an_export_without_a_bundle_row_is_still_ledgered(db, cfg):
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO exports (reviewer, top_n, states) VALUES ('operator', 10, %s) "
            "RETURNING id", (psycopg_json(["accepted"]),))
    jobs_mod.enqueue(db, "purge", detail="retention purge")
    assert workers.drain_once(db, cfg, ("purge",)) is True
    assert db.fetch_one("SELECT count(*) AS n FROM exports")["n"] == 0
    assert db.fetch_one("SELECT count(*) AS n FROM ledger_events "
                        "WHERE event_type = 'DERIVED_DESTROYED'")["n"] == 1
