"""DEMO — the explicit seed action, measured, labeled (§17 scenario 8).

The demo seed on an empty DB: the fixture's measured ``signal_summary`` values
are recorded; ``sustained_motion_low_light`` fires with evidence naming the
measured percentiles; ``stationary_animal_with_person`` fires with
``synthetic: true`` evidence; the ingest wall clock is measured and < 300 s;
all presence rows carry ``synthetic = true``; re-seeding is idempotent.
"""
from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from longwatch import app as app_mod, demo, detect, workers


def _run_ingest(db, cfg) -> None:
    assert workers.drain_once(db, cfg, ("ingest",),
                              detector_factory=lambda: detect.Detector(cfg)) is True


def test_demo_seed_is_measured_labeled_and_idempotent(db, cfg, require_ffmpeg, require_model):
    started = time.monotonic()
    outcome = demo.create_demo_source(db, cfg)
    assert outcome["existing"] is False
    assert outcome["job_id"]
    _run_ingest(db, cfg)
    elapsed = time.monotonic() - started
    assert elapsed < 300, f"demo ingest took {elapsed:.1f}s"

    source = db.fetch_one("SELECT * FROM sources WHERE id = %s", (outcome["source"]["id"],))
    assert source["state"] == "ready", source["failure_reason"]
    summary = source["signal_summary"]
    if isinstance(summary, str):
        summary = json.loads(summary)
    assert summary["motion"]["count"] >= 150          # 160 s at 1 fps
    assert summary["motion"]["p85"] > summary["motion"]["p10"]
    assert summary["luma"]["p10"] < summary["luma"]["p50"]

    events = db.fetch_all("SELECT kind FROM signal_events WHERE source_id = %s",
                          (source["id"],))
    assert [row["kind"] for row in events] == ["audio_absent"]  # no audio stream

    candidates = db.fetch_all(
        """
        SELECT c.pattern_slug, c.score, c.sentence, c.evidence
          FROM candidates c JOIN windows w ON w.id = c.window_id
         WHERE w.source_id = %s ORDER BY c.pattern_slug
        """, (source["id"],))
    by_slug = {row["pattern_slug"]: row for row in candidates}
    assert "sustained_motion_low_light" in by_slug, [row["pattern_slug"] for row in candidates]
    low_light = by_slug["sustained_motion_low_light"]
    evidence = low_light["evidence"]
    if isinstance(evidence, str):
        evidence = json.loads(evidence)
    assert "this recording's p85" in low_light["sentence"]
    assert evidence["motion_percentile"] == 85
    assert evidence["luma_percentile"] == 10
    assert evidence["luma_threshold_milli"] == summary["luma"]["p10"]
    assert evidence["motion_threshold_milli"] == summary["motion"]["p85"]
    assert evidence["luma_summary_samples"] == summary["luma"]["count"]

    flagship = by_slug.get("stationary_animal_with_person")
    assert flagship is not None, "the flagship pattern must fire via labeled synthetic injection"
    flagship_evidence = flagship["evidence"]
    if isinstance(flagship_evidence, str):
        flagship_evidence = json.loads(flagship_evidence)
    assert flagship_evidence["synthetic"] is True
    assert flagship_evidence["class"] == "dog"
    assert flagship_evidence["duration_ms"] == 100_000
    assert flagship_evidence["overlap_ms"] == 70_000

    presence = db.fetch_all(
        """
        SELECT p.synthetic, p.class_name FROM presence_intervals p
          JOIN windows w ON w.id = p.window_id WHERE w.source_id = %s
        """, (source["id"],))
    assert presence, "the demo must inject presence rows"
    assert all(row["synthetic"] for row in presence)
    assert {row["class_name"] for row in presence} == {"dog", "person"}

    seeded = db.fetch_all("SELECT payload FROM ledger_events WHERE event_type = 'DEMO_SEEDED'")
    assert len(seeded) == 1
    payload = seeded[0]["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["synthetic_presence"] is True
    assert payload["fixture"]["sha256"] == source["sha256"]

    # idempotent: the second action returns the existing source, launches nothing
    again = demo.create_demo_source(db, cfg)
    assert again["existing"] is True
    assert str(again["source"]["id"]) == str(source["id"])
    assert db.fetch_one("SELECT count(*) AS n FROM sources")["n"] == 1


def test_demo_seed_endpoint_reports_202_then_200(db, cfg, require_ffmpeg, require_model):
    client = TestClient(app_mod.create_app(cfg, db, start_workers=False, install_guard=False))
    first = client.post("/api/v1/demo/seed")
    assert first.status_code == 202, first.text
    assert first.json()["job_id"]
    _run_ingest(db, cfg)
    second = client.post("/api/v1/demo/seed")
    assert second.status_code == 200, second.text
    assert second.json()["source_id"] == first.json()["source_id"]

    sources = client.get("/api/v1/sources").json()["sources"]
    assert len(sources) == 1
    assert sources[0]["origin"] == "demo"
    assert sources[0]["state"] == "ready"

    entries = client.get("/api/v1/candidates").json()["entries"]
    assert entries
    assert all(entry.get("demo") for entry in entries)
    synthetic = [entry for entry in entries if entry.get("synthetic")]
    assert synthetic, "the flagship candidate must be marked synthetic"


def test_no_demo_data_exists_without_the_explicit_action(db, cfg):
    """There is deliberately no demo-seed environment variable (§12)."""
    import os

    assert not any("DEMO" in key for key in os.environ if key.startswith("LONGWATCH_"))
    assert db.fetch_one("SELECT count(*) AS n FROM sources")["n"] == 0
    assert db.fetch_one("SELECT count(*) AS n FROM presence_intervals")["n"] == 0
