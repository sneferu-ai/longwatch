"""QUEUE — pagination, ml_failed entries first, acknowledgement (§17 scenario 9).

``GET /api/v1/candidates`` returns ``{total, entries}`` with limit/offset
honored (default 50, max 200); ``ml_failed`` entries are returned first and
disappear after ``ml_ack`` writes ``ML_FAILURE_ACKED``.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from longwatch import app as app_mod
from longwatch.ledger import psycopg_json


def _client(db, cfg) -> TestClient:
    return TestClient(app_mod.create_app(cfg, db, start_workers=False, install_guard=False))


def _seed(db, candidates: int = 60, failed_windows: int = 2) -> dict:
    with db.transaction() as cur:
        cur.execute(
            """
            INSERT INTO sources (origin, original_name, storage_path, sha256, size_bytes,
                                 duration_s, probe, state)
            VALUES ('upload','queue.mp4','/tmp/queue.mp4',%s,1024,600,'{}'::jsonb,'ready')
            RETURNING id
            """, ("c" * 64,))
        source_id = cur.fetchone()["id"]
        # one window per candidate: (window_id, pattern_slug, pattern_version) is unique
        for index in range(candidates):
            cur.execute(
                "INSERT INTO windows (source_id, idx, start_s, end_s, boundary_kind, ml_state)"
                " VALUES (%s, %s, %s, %s, 'grid', 'ok') RETURNING id",
                (source_id, index, index * 10, index * 10 + 10))
            window_id = cur.fetchone()["id"]
            if index == 0:
                first_window_id = window_id
            cur.execute(
                """
                INSERT INTO candidates (window_id, pattern_slug, pattern_version, score,
                                        sentence, evidence)
                VALUES (%s, 'stationary_animal_with_person', 1, %s, %s, %s)
                """,
                (window_id, 1 + (index % 100),
                 f"dog presence held {100 + index}s at motion energy <= 0.000 (tolerance 2s) "
                 f"while a person was present 70s of the same span",
                 psycopg_json({"duration_ms": 100_000, "synthetic": False})),
            )
        for index in range(failed_windows):
            cur.execute(
                """
                INSERT INTO windows (source_id, idx, start_s, end_s, boundary_kind,
                                     ml_state, ml_failure_reason)
                VALUES (%s, %s, %s, %s, 'grid', 'failed', 'decoder exited 1')
                RETURNING id
                """,
                (source_id, 100 + index, 100 + index * 10, 110 + index * 10),
            )
    return {"source_id": str(source_id), "window_id": str(first_window_id)}


def test_pagination_shape_and_limits(db, cfg):
    _seed(db, candidates=60)
    client = _client(db, cfg)
    first = client.get("/api/v1/candidates").json()
    assert first["limit"] == 50
    assert len(first["entries"]) == 50 + 2  # the two ml_failed entries are pinned first
    assert first["entries"][0]["kind"] == "ml_failed"
    assert first["entries"][1]["kind"] == "ml_failed"
    assert first["entries"][2]["kind"] == "candidate"
    assert first["total"] == 62

    second = client.get("/api/v1/candidates?limit=200&offset=50").json()
    assert second["limit"] == 200
    assert len(second["entries"]) == 12

    capped = client.get("/api/v1/candidates?limit=9999").json()
    assert capped["limit"] == 200


def test_ml_failed_entries_are_pinned_and_acknowledgeable(db, cfg):
    ids = _seed(db, candidates=1, failed_windows=1)
    client = _client(db, cfg)
    entries = client.get("/api/v1/candidates").json()["entries"]
    failed = entries[0]
    assert failed["kind"] == "ml_failed"
    assert failed["label"] == "detection incomplete — manual review required"
    assert failed["reason"] == "decoder exited 1"

    response = client.post(f"/api/v1/windows/{failed['window_id']}/ml_ack")
    assert response.status_code == 200, response.text
    after = client.get("/api/v1/candidates").json()["entries"]
    assert all(entry["kind"] != "ml_failed" for entry in after)
    events = db.fetch_all("SELECT payload FROM ledger_events WHERE event_type = 'ML_FAILURE_ACKED'")
    assert len(events) == 1
    payload = events[0]["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["reviewer"] == "operator"
    assert payload["window_id"] == failed["window_id"]

    # a second acknowledgement is a conflict, not a silent no-op
    assert client.post(f"/api/v1/windows/{failed['window_id']}/ml_ack").status_code == 409


def test_candidate_entries_carry_clip_and_source_ids(db, cfg):
    ids = _seed(db, candidates=3, failed_windows=0)
    with db.transaction() as cur:
        cur.execute(
            """
            INSERT INTO clips (window_id, kind, requested_start_ms, actual_start_ms, end_ms,
                               path, sha256, size_bytes, cut_mode, ffmpeg_cmd)
            VALUES (%s, 'review_proxy', 0, 0, 100000, '/tmp/proxy.mp4', %s, 10, NULL, 'ffmpeg')
            RETURNING id
            """, (ids["window_id"], "d" * 64))
        clip_id = str(cur.fetchone()["id"])
    client = _client(db, cfg)
    entries = [entry for entry in client.get("/api/v1/candidates").json()["entries"]
               if entry["kind"] == "candidate"]
    assert entries
    with_clip = [entry for entry in entries if entry["clip_id"] == clip_id]
    assert with_clip, "the clip-bearing candidate must carry its clip_id"
    assert with_clip[0]["source_id"] == ids["source_id"]

    detail = client.get(f"/api/v1/candidates/{with_clip[0]['candidate_id']}").json()
    for key in ("clip_id", "source_id", "start_s", "end_s", "pattern_slug",
                "pattern_version", "state", "evidence", "sentence"):
        assert key in detail, key
    assert detail["clip_id"] == clip_id


def test_pages_stitch_into_the_total_order(db, cfg):
    """SQL-side LIMIT/OFFSET must page the ranked candidates exactly — the 2 s
    poll is bounded at corpus scale, and no row is skipped or doubled across
    page boundaries."""
    # the shared _seed parks failed windows at idx 100+, so stay under 100
    _seed(db, candidates=95, failed_windows=2)
    client = _client(db, cfg)
    first = client.get("/api/v1/candidates?limit=50").json()
    assert first["total"] == 97
    seen: list[str] = []
    for offset in (0, 50):  # contiguous pages stitch; 95 = 50 + 45
        page = client.get(f"/api/v1/candidates?limit=50&offset={offset}").json()
        candidates = [e for e in page["entries"] if e["kind"] == "candidate"]
        seen.extend(e["candidate_id"] for e in candidates)
        # the two ml_failed entries pin above every page's candidates
        assert [e["kind"] for e in page["entries"][:2]] == ["ml_failed", "ml_failed"]
    assert len(seen) == 95
    assert len(set(seen)) == 95, "no candidate is skipped or doubled across pages"
    # the boundary row is exactly addressable
    edge = client.get("/api/v1/candidates?limit=50&offset=94").json()
    assert [e["candidate_id"] for e in edge["entries"] if e["kind"] == "candidate"] == \
        [seen[-1]]
    scores = [e["score"] for e in client.get("/api/v1/candidates?limit=200").json()["entries"]
              if e["kind"] == "candidate"]
    assert scores == sorted(scores, reverse=True)


def test_superseded_versions_leave_the_queue(db, cfg):
    ids = _seed(db, candidates=2, failed_windows=0)
    client = _client(db, cfg)
    assert len([e for e in client.get("/api/v1/candidates").json()["entries"]
                if e["kind"] == "candidate"]) == 2
    with db.transaction() as cur:
        cur.execute("UPDATE patterns SET is_current = false WHERE slug = "
                    "'stationary_animal_with_person'")
    remaining = [e for e in client.get("/api/v1/candidates").json()["entries"]
                 if e["kind"] == "candidate"]
    assert remaining == []
