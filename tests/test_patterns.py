"""PATTERNS — the three built-ins on constructed fixtures (§17 scenario 7).

The injection layer is stated: presence fixtures are inserted directly into
``presence_intervals`` — the aggregation output the rule engine consumes —
exercising merge, the boundary max-of-adjacent tolerance, cross-window 90 s
spans, gap-tolerance merging, the flagship's absolute semantics, the
motion/low-light pattern's percentile semantics against a stored
``signal_summary`` (including the ≥ 60-sample rule → ``insufficient_samples``),
the person-absent definition, integer scores, non-empty sentences quoting
fixture values, and the deterministic four-key ranking.
"""
from __future__ import annotations

import json
import uuid

from longwatch import jobs as jobs_mod, pipeline, scoring
from longwatch.ledger import psycopg_json


# ------------------------------------------------------------------ fixtures
def seed_source(db, duration_s: float = 200.0, windows=((0.0, 100.0), (100.0, 200.0)),
                origin: str = "upload") -> dict:
    with db.transaction() as cur:
        cur.execute(
            """
            INSERT INTO sources (origin, original_name, storage_path, sha256, size_bytes,
                                 duration_s, probe, state, signal_summary)
            VALUES (%s, %s, %s, %s, %s, %s, '{}'::jsonb, 'ready', '{}'::jsonb)
            RETURNING *
            """,
            (origin, "fixture.mp4", f"/tmp/{origin}-{uuid.uuid4().hex}.mp4",
             uuid.uuid4().hex.ljust(64, "0")[:64], 1024, float(duration_s)),
        )
        source = cur.fetchone()
        for index, (start, end) in enumerate(windows):
            cur.execute(
                "INSERT INTO windows (source_id, idx, start_s, end_s, boundary_kind, ml_state)"
                " VALUES (%s, %s, %s, %s, 'grid', 'ok') RETURNING id",
                (source["id"], index, start, end),
            )
            window_id = cur.fetchone()["id"]
            cur.execute("UPDATE windows SET detections_summary = %s WHERE id = %s",
                        (psycopg_json({"dog": {"sample_interval_ms": 1000},
                                       "person": {"sample_interval_ms": 1000}}), window_id))
    return db.fetch_one("SELECT * FROM sources WHERE id = %s", (source["id"],))


def add_interval(db, source_id: str, class_name: str, start_s: float, end_s: float,
                 sample_interval_ms: int = 1000, synthetic: bool = False) -> None:
    window = db.fetch_one(
        "SELECT id FROM windows WHERE source_id = %s AND start_s <= %s AND end_s >= %s"
        " ORDER BY idx LIMIT 1", (source_id, (start_s + end_s) / 2.0, (start_s + end_s) / 2.0))
    assert window is not None, "no window contains the interval"
    with db.transaction() as cur:
        cur.execute(
            """
            INSERT INTO presence_intervals (window_id, class_name, t_start, t_end,
                                            samples, conf_max_milli, synthetic)
            VALUES (%s, %s, %s, %s, %s, 900, %s)
            """,
            (window["id"], class_name, float(start_s), float(end_s),
             int(end_s - start_s), bool(synthetic)),
        )
        cur.execute("UPDATE windows SET detections_summary = %s WHERE id = %s",
                    (psycopg_json({class_name: {"sample_interval_ms": sample_interval_ms}}),
                     window["id"]))


def add_series(db, source_id: str, motion: list[int], luma: list[int]) -> None:
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO signal_blocks (source_id, idx, start_s, series) VALUES (%s, 0, 0, %s)",
            (source_id, psycopg_json({"motion": motion, "luma": luma,
                                      "persistence": [0] * len(motion)})),
        )


def set_summary(db, source_id: str, summary: dict) -> None:
    with db.transaction() as cur:
        cur.execute("UPDATE sources SET signal_summary = %s WHERE id = %s",
                    (psycopg_json(summary), source_id))


def add_event(db, source_id: str, kind: str, start_s, end_s, value_milli=None) -> None:
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO signal_events (source_id, kind, t_start, t_end, value_milli, meta)"
            " VALUES (%s, %s, %s, %s, %s, '{}'::jsonb)",
            (source_id, kind, start_s, end_s, value_milli),
        )


def clear_candidates(db, source_id: str) -> None:
    with db.transaction() as cur:
        cur.execute(
            "DELETE FROM candidates WHERE window_id IN "
            "(SELECT id FROM windows WHERE source_id = %s)", (source_id,))


def score(db, source_id: str) -> list[dict]:
    source = db.fetch_one("SELECT * FROM sources WHERE id = %s", (source_id,))
    pipeline.score_source(db, source, "operator")
    rows = db.fetch_all(
        """
        SELECT c.id, c.score, c.sentence, c.evidence, c.state, c.pattern_slug,
               c.pattern_version, w.start_s, w.source_id
          FROM candidates c JOIN windows w ON w.id = c.window_id
         WHERE w.source_id = %s
         ORDER BY c.score DESC, w.source_id, w.start_s, c.pattern_slug
        """, (source_id,))
    for row in rows:
        if isinstance(row["evidence"], str):
            row["evidence"] = json.loads(row["evidence"])
    return rows


# ------------------------------------------------------------------- flagship
def test_flagship_absolute_semantics_fire(db):
    source = seed_source(db)
    add_series(db, str(source["id"]), motion=[0] * 200, luma=[500] * 200)
    add_interval(db, str(source["id"]), "dog", 0.0, 100.0, synthetic=True)
    add_interval(db, str(source["id"]), "person", 10.0, 80.0)
    rows = score(db, str(source["id"]))
    flagship = [row for row in rows if row["pattern_slug"] == "stationary_animal_with_person"]
    assert len(flagship) == 1
    row = flagship[0]
    assert row["pattern_version"] == 1
    assert isinstance(row["score"], int) and 1 <= row["score"] <= 100
    assert row["sentence"] == (
        "dog presence held 100s at motion energy <= 0.000 (tolerance 2s) while a person "
        "was present 70s of the same span")
    assert row["evidence"]["synthetic"] is True
    assert row["evidence"]["motion_mean_milli"] == 0
    assert row["evidence"]["overlap_ms"] == 70_000
    assert row["evidence"]["gap_tolerance_ms"] == 2_000
    assert float(row["start_s"]) == 0.0  # attributed to the window holding the midpoint


def test_flagship_does_not_fire_on_motion_or_short_presence(db):
    source = seed_source(db)
    add_series(db, str(source["id"]), motion=[400] * 200, luma=[500] * 200)
    add_interval(db, str(source["id"]), "dog", 0.0, 100.0)
    add_interval(db, str(source["id"]), "person", 10.0, 80.0)
    assert [row for row in score(db, str(source["id"]))
            if row["pattern_slug"] == "stationary_animal_with_person"] == []

    source2 = seed_source(db)
    add_series(db, str(source2["id"]), motion=[0] * 200, luma=[500] * 200)
    add_interval(db, str(source2["id"]), "dog", 0.0, 60.0)  # 60 s < 90 s
    add_interval(db, str(source2["id"]), "person", 10.0, 50.0)
    assert [row for row in score(db, str(source2["id"]))
            if row["pattern_slug"] == "stationary_animal_with_person"] == []


def test_cross_window_presence_merges_with_max_of_adjacent_tolerance(db):
    """A 90 s presence spanning a scene cut is not blinded by short windows."""
    source = seed_source(db, windows=((0.0, 60.0), (60.0, 150.0)))
    add_series(db, str(source["id"]), motion=[3] * 200, luma=[500] * 200)
    add_interval(db, str(source["id"]), "dog", 0.0, 58.0, sample_interval_ms=1000)
    add_interval(db, str(source["id"]), "dog", 62.0, 150.0, sample_interval_ms=2000)
    add_interval(db, str(source["id"]), "person", 20.0, 60.0)
    rows = [row for row in score(db, str(source["id"]))
            if row["pattern_slug"] == "stationary_animal_with_person"]
    assert len(rows) == 1
    assert rows[0]["evidence"]["gap_tolerance_ms"] == 4_000  # max of 2 s and 4 s
    assert rows[0]["evidence"]["duration_ms"] == 150_000


# ------------------------------------------------------- motion / low light
def test_percentile_semantics_use_the_stored_summary(db):
    source = seed_source(db, windows=((0.0, 200.0),))
    motion = [3] * 120 + [500] * 80
    luma = [1000] * 120 + [50] * 80
    add_series(db, str(source["id"]), motion=motion, luma=luma)
    set_summary(db, str(source["id"]), {
        "motion": {"count": 200, "p85": 500},
        "luma": {"count": 200, "p10": 50},
    })
    rows = [row for row in score(db, str(source["id"]))
            if row["pattern_slug"] == "sustained_motion_low_light"]
    assert len(rows) == 1
    assert rows[0]["sentence"] == (
        "motion above this recording's p85 for 80s while luma below its p10")
    assert rows[0]["evidence"]["luma_percentile"] == 10   # the luma leg's percentile
    assert rows[0]["evidence"]["motion_percentile"] == 85  # and the motion leg's
    assert rows[0]["evidence"]["luma_summary_samples"] == 200


def test_percentile_needs_sixty_samples(db):
    source = seed_source(db, windows=((0.0, 200.0),))
    add_series(db, str(source["id"]), motion=[3] * 120 + [500] * 80,
               luma=[1000] * 120 + [50] * 80)
    set_summary(db, str(source["id"]), {
        "motion": {"count": 30, "p85": 500},
        "luma": {"count": 30, "p10": 50},
    })
    assert [row for row in score(db, str(source["id"]))
            if row["pattern_slug"] == "sustained_motion_low_light"] == []


# ------------------------------------------------------------- person absent
def test_person_absent_definition(db):
    source = seed_source(db)
    add_series(db, str(source["id"]), motion=[3] * 200, luma=[500] * 200)
    add_interval(db, str(source["id"]), "cow", 0.0, 100.0)
    add_event(db, str(source["id"]), "silence_gap", 0.0, 10.0, 10_000)
    rows = [row for row in score(db, str(source["id"]))
            if row["pattern_slug"] == "long_silence_animals_present"]
    assert len(rows) == 1
    assert rows[0]["sentence"] == (
        "silence gap of 10s with cow present 100s and no person detected")
    assert rows[0]["evidence"]["gap_ms"] == 10_000
    assert rows[0]["evidence"]["duration_ms"] == 100_000
    assert rows[0]["evidence"]["non_detection_note"] == (
        "non-detection is treated as absence for rule purposes")

    # a person anywhere inside the span defeats the pattern
    add_interval(db, str(source["id"]), "person", 50.0, 60.0)
    clear_candidates(db, str(source["id"]))
    assert [row for row in score(db, str(source["id"]))
            if row["pattern_slug"] == "long_silence_animals_present"] == []


def test_short_silence_gap_does_not_qualify(db):
    source = seed_source(db)
    add_series(db, str(source["id"]), motion=[3] * 200, luma=[500] * 200)
    add_interval(db, str(source["id"]), "cow", 0.0, 100.0)
    add_event(db, str(source["id"]), "silence_gap", 0.0, 4.0, 4_000)
    assert [row for row in score(db, str(source["id"]))
            if row["pattern_slug"] == "long_silence_animals_present"] == []


# ------------------------------------------------------------- queue ranking
def test_ranking_is_the_deterministic_four_key_order(db):
    source = seed_source(db)
    add_series(db, str(source["id"]), motion=[3] * 200, luma=[500] * 200)
    add_interval(db, str(source["id"]), "dog", 0.0, 100.0)
    add_interval(db, str(source["id"]), "person", 10.0, 80.0)
    add_event(db, str(source["id"]), "silence_gap", 0.0, 60.0, 60_000)
    rows = score(db, str(source["id"]))
    keys = [(-row["score"], str(row["source_id"]), float(row["start_s"]), row["pattern_slug"])
            for row in rows]
    assert keys == sorted(keys)


# --------------------------------------------------------------- pattern edits
def test_pattern_edit_creates_a_version_and_rescore_job(db, cfg):
    from fastapi.testclient import TestClient

    from longwatch import app as app_mod

    app = app_mod.create_app(cfg, db, start_workers=False, install_guard=False)
    client = TestClient(app)
    before = client.get("/api/v1/patterns").json()["patterns"]
    current = [row for row in before if row["slug"] == "sustained_motion_low_light"
               and row["is_current"]][0]
    body = {
        "title": current["title"],
        "sentence_template": current["sentence_template"],
        "rule": {"type": "signal_percentile", "signal": "motion", "op": "gte",
                 "percentile": 90, "min_duration_s": 20},
        "weight": 70,
        "enabled": True,
    }
    response = client.put("/api/v1/patterns/sustained_motion_low_light", json=body)
    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["version"] == 2
    assert payload["rescore_job_id"]

    after = client.get("/api/v1/patterns").json()["patterns"]
    versions = [row for row in after if row["slug"] == "sustained_motion_low_light"]
    assert {row["version"] for row in versions} == {1, 2}
    assert [row["version"] for row in versions if row["is_current"]] == [2]

    events = db.fetch_all(
        "SELECT event_type, payload FROM ledger_events WHERE event_type = 'PATTERN_UPDATED'")
    assert len(events) == 1
    payload_json = events[0]["payload"]
    if isinstance(payload_json, str):
        payload_json = json.loads(payload_json)
    assert payload_json["decided_carried"] == {"accepted": 0, "flagged": 0, "rejected": 0}
    assert payload_json["version"] == 2
    jobs = db.fetch_all("SELECT kind, detail FROM jobs WHERE kind = 'rescore'")
    assert jobs and jobs[0]["detail"] == "slug=sustained_motion_low_light"


def test_rescore_survives_a_lease_reclaim_suffix(db, cfg):
    """The reclaimer appends ' [lease reclaimed]' to a job's detail; a rescore
    whose slug no longer parses would silently score nothing."""
    source = seed_source(db)
    add_interval(db, str(source["id"]), "dog", 0.0, 100.0)
    add_interval(db, str(source["id"]), "person", 10.0, 80.0)
    job_id = jobs_mod.enqueue(db, "rescore", detail="slug=stationary_animal_with_person")
    db.execute("UPDATE jobs SET detail = detail || ' [lease reclaimed]' WHERE id = %s",
               (job_id,))
    job = jobs_mod.Job.from_row(jobs_mod.get_job(db, job_id))
    outcome = pipeline.run_rescore(db, job, cfg)
    assert outcome["slug"] == "stationary_animal_with_person"
    assert outcome["candidates_created"] >= 1, "the flagship really re-scored"
    events = db.fetch_all(
        "SELECT payload FROM ledger_events WHERE event_type = 'RESCORE_COMPLETED'")
    payload = events[0]["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["slug"] == "stationary_animal_with_person"


def test_rule_validation_refuses_free_text_class_naming(db, cfg):
    from fastapi.testclient import TestClient

    from longwatch import app as app_mod

    app = app_mod.create_app(cfg, db, start_workers=False, install_guard=False)
    client = TestClient(app)
    body = {
        "title": "Free text class",
        "sentence_template": "{class} held {dur}s",
        "rule": {"type": "presence", "class": "livestock", "min_duration_s": 10},
        "weight": 30,
    }
    response = client.put("/api/v1/patterns/stationary_animal_with_person", json=body)
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "rule_error"

    bad_template = {
        "title": "Bad template",
        "sentence_template": "{whatever} held {dur}s",
        "rule": {"type": "presence", "class": "dog", "min_duration_s": 10},
        "weight": 30,
    }
    response = client.put("/api/v1/patterns/stationary_animal_with_person", json=bad_template)
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "template_error"


def test_scoring_clamps_to_one_and_one_hundred():
    assert scoring.score_span(100, {"duration_ms": 1_000_000,
                                    "required_duration_ms": 1_000}) == 100
    assert scoring.score_span(1, {"duration_ms": 0, "required_duration_ms": 1000}) == 1
