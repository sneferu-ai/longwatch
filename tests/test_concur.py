"""CONCUR — parallel appends from both worker paths, one clean chain (§17.5).

The media-worker path and the utility-worker path append 100 ledger events each,
in parallel, through the real append path; the final chain has strictly
increasing seq, no forked ``prev_hash``, verifies clean, and no transaction held
a lock across media work.
"""
from __future__ import annotations

import threading
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from longwatch import ledger

WORKER_PATHS = ("longwatch-media", "longwatch-utility")
PER_PATH = 100


def _append_batch(dsn: str, schema: str, actor: str, errors: list) -> None:
    conn = psycopg.connect(dsn, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}"')
        for index in range(PER_PATH):
            try:
                with conn.cursor() as cur:
                    ledger.append(cur, "DETECTION_COMPLETED", actor, {
                        "source_id": "src-1",
                        "window_id": f"{actor}-w-{index}",
                        "ml_state": "ok",
                        "sample_interval_ms": 500,
                        "positive_frames": index,
                    })
                conn.commit()
            except Exception as exc:  # noqa: BLE001 - recorded and asserted below
                conn.rollback()
                errors.append(exc)
    finally:
        conn.close()


def test_parallel_appends_from_both_worker_paths(db, dsn, schema):
    errors: list = []
    threads = [
        threading.Thread(target=_append_batch, args=(dsn, schema, actor, errors))
        for actor in WORKER_PATHS
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert errors == [], errors

    rows = db.fetch_all("SELECT seq, prev_hash, row_hash FROM ledger_events ORDER BY seq")
    assert len(rows) == 1 + PER_PATH * 2
    assert [row["seq"] for row in rows] == list(range(1, len(rows) + 1))
    hashes = [row["row_hash"] for row in rows]
    assert len(set(hashes)) == len(hashes), "duplicate row_hash"
    # exactly one row per parent: no two rows can share a prev_hash
    assert len({row["prev_hash"] for row in rows}) == len(rows), "forked prev_hash"
    previous = ledger.GENESIS_PREV
    for row in rows:
        assert row["prev_hash"] == previous
        previous = row["row_hash"]
    with db.connect() as conn:
        result = ledger.verify(conn)
    assert result["ok"] is True
    assert result["length"] == len(rows)


def test_append_never_holds_the_lock_across_media_work():
    """Structural: the ledger module touches no media, file or archive code."""
    source = (Path(__file__).resolve().parent.parent / "longwatch" / "ledger.py").read_text()
    for forbidden in ("import media", "from . import media", "ffmpeg", "zipfile",
                      "subprocess", "open("):
        assert forbidden not in source, f"ledger.py must not reference {forbidden!r}"


def test_reclaim_is_lease_based(db):
    from longwatch import jobs as jobs_mod

    with db.connect() as conn:
        job_id = jobs_mod.enqueue(conn, "ingest", source_id=None, detail="lease probe")
    conn = db.dedicated()
    try:
        claimed = jobs_mod.claim(conn, ("ingest",))
        assert claimed is not None and claimed.id == job_id
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET beat_at = now() - interval '600 seconds' WHERE id = %s",
                        (job_id,))
        conn.commit()
        outcome = jobs_mod.reclaim_stale(conn, 120)
        assert [str(entry["id"]) for entry in outcome["reclaimed"]] == [job_id]
        again = jobs_mod.claim(conn, ("ingest",))
        assert again is not None and again.attempts == 2
    finally:
        conn.close()
