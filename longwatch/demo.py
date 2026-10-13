"""The demo seed (§16) — explicit, labeled, measured.

**There is no automatic demo seeding.**  Demo data appears only when the
operator clicks the Ingest page's demo button or calls
``POST /api/v1/demo/seed`` — never from an environment default, never on a
fresh production database by surprise.  The action is idempotent: if an intact
demo source exists, it returns the existing one.

The fixture is generated locally via lavfi (no network, no bundled binary
media): 100 s of bright static content followed by 60 s of darkened moving
content, 160 s total, no audio stream (which also exercises ``audio_absent``).

The flagship presence pattern fires through **labeled synthetic injection**:
the demo ingest job inserts ``presence_intervals`` rows with ``synthetic=true``,
so ingest → windows → signals → detection → injection → scoring → triage →
export is exercisable in a fresh environment with nothing but a port and a DSN,
honestly labeled as what it is.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from . import jobs as jobs_mod, ledger, media, pipeline

log = logging.getLogger("longwatch.demo")

FIXTURE_NAME = "demo.mp4"
DOG_START_S, DOG_END_S = 0.0, 100.0
PERSON_START_S, PERSON_END_S = 10.0, 80.0
EXPECTED_PATTERNS = ["sustained_motion_low_light", "stationary_animal_with_person"]


class DemoError(RuntimeError):
    """The demo seed could not be produced."""


def fixture_path(cfg) -> Path:
    return Path(cfg.data_dir) / "fixtures" / FIXTURE_NAME


def generate_fixture(cfg) -> Path:
    """Generate the 160 s lavfi fixture (idempotent — regenerated when absent)."""
    target = fixture_path(cfg)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size > 0:
        return target
    tmp = target.with_suffix(".tmp.mp4")
    argv = media.lavfi_demo_command(tmp)
    media.run_ffmpeg(argv, media.hang_timeout(cfg.ffmpeg_hang_base_s,
                                              cfg.ffmpeg_hang_factor, 160.0))
    os.replace(tmp, target)
    return target


def find_intact_demo_source(db) -> dict | None:
    row = db.fetch_one(
        "SELECT * FROM sources WHERE origin = 'demo' ORDER BY created_at DESC LIMIT 1")
    if row is None:
        return None
    if row["state"] in ("failed", "unreadable", "missing"):
        return None
    if not os.path.exists(row["storage_path"]):
        return None
    return row


def create_demo_source(db, cfg, actor: str | None = None) -> dict:
    """The operator's explicit action: generate, register, and queue the demo."""
    actor = actor or cfg.reviewer_name
    existing = find_intact_demo_source(db)
    if existing is not None:
        return {"source": existing, "job_id": None, "existing": True}
    path = generate_fixture(cfg)
    digest = media.sha256_file(path)
    size = path.stat().st_size
    source, created = pipeline.register_source(
        db, storage_path=str(path), origin="demo", original_name=FIXTURE_NAME,
        sha256=digest, size_bytes=size, actor=actor)
    if not created:
        return {"source": source, "job_id": None, "existing": True}
    with db.transaction() as cur:
        ledger.append(cur, "DEMO_SEEDED", actor, {
            "fixture": {"sha256": digest, "duration_ms": 160_000, "width": 480, "height": 270},
            "synthetic_presence": True,
            "patterns_expected": EXPECTED_PATTERNS,
        })
    job_id = jobs_mod.enqueue(db, "ingest", source_id=str(source["id"]),
                              detail="demo seed")
    return {"source": source, "job_id": job_id, "existing": False}


def _window_for(db, source_id: str, at_s: float) -> dict | None:
    return db.fetch_one(
        """
        SELECT id, idx, start_s, end_s FROM windows
         WHERE source_id = %s AND start_s <= %s AND end_s >= %s
         ORDER BY idx LIMIT 1
        """,
        (source_id, at_s, at_s),
    )


def inject_synthetic_presence(db, source: dict, actor: str | None = None) -> dict:
    """Labeled synthetic presence over the static first half of the fixture.

    Idempotent: an interrupted demo ingest re-runs the detect stage and this
    injection with it, so prior synthetic rows for this source are replaced,
    never duplicated.
    """
    actor = actor or "demo"
    if source.get("origin") != "demo":
        return {"injected": 0}
    with db.transaction() as cur:
        cur.execute(
            """
            DELETE FROM presence_intervals
             WHERE synthetic
               AND window_id IN (SELECT id FROM windows WHERE source_id = %s)
            """,
            (source["id"],),
        )
    plans = [
        ("dog", DOG_START_S, DOG_END_S, 1400),
        ("person", PERSON_START_S, PERSON_END_S, 1600),
    ]
    injected = 0
    for class_name, start_s, end_s, conf in plans:
        midpoint = (start_s + end_s) / 2.0
        window = _window_for(db, str(source["id"]), midpoint)
        if window is None:
            log.warning("demo injection found no window containing %.1fs", midpoint)
            continue
        with db.transaction() as cur:
            cur.execute(
                """
                INSERT INTO presence_intervals (window_id, class_name, t_start, t_end,
                                                samples, conf_max_milli, synthetic)
                VALUES (%s, %s, %s, %s, %s, %s, true)
                """,
                (window["id"], class_name, float(start_s), float(end_s),
                 int(end_s - start_s), int(conf)),
            )
        injected += 1
    return {"injected": injected, "synthetic": True,
            "dog_s": [DOG_START_S, DOG_END_S], "person_s": [PERSON_START_S, PERSON_END_S]}
