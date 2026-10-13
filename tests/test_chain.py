"""CHAIN — append-only, byte-exact, and corruption-detectable (§17 scenario 4).

The corruption mechanism is stated: the harness owns its schema in a dedicated
test database and corrupts a row via ``ALTER TABLE … DISABLE TRIGGER ALL;
UPDATE …; ALTER TABLE … ENABLE TRIGGER ALL`` — table-owner privilege, no
superuser bypass.  The byte-exact preimage recomputes every row hash from
``ledger.jsonl`` fields alone.
"""
from __future__ import annotations

import time

import psycopg
import pytest

from longwatch import ledger

RAISE_EXCEPTION = "ledger_immutable"


def _seed(db, count: int = 3) -> None:
    with db.transaction() as cur:
        for index in range(count):
            ledger.append(cur, "SOURCE_REGISTERED", "operator", {
                "source_id": f"src-{index}", "origin": "upload",
                "original_name": f"clip-{index}.mp4", "sha256": "a" * 64,
                "size_bytes": 1024 + index,
            })


def test_genesis_is_written_once(db):
    with db.connect() as conn:
        seq, head = ledger.head(conn)
    assert seq == 1
    with db.connect() as conn:
        assert ledger.ensure_genesis(conn) is None
        assert ledger.head(conn)[0] == 1


def test_append_is_hash_chained_and_verifies(db):
    _seed(db, 5)
    with db.connect() as conn:
        result = ledger.verify(conn)
    assert result["ok"] is True
    assert result["length"] == 6
    assert result["first_divergence"] is None


def test_update_delete_and_truncate_all_raise(db):
    _seed(db, 2)
    with db.connect() as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.cursor() as cur:
                cur.execute("UPDATE ledger_events SET actor = 'someone else' WHERE seq = 2")
        conn.rollback()
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ledger_events WHERE seq = 2")
        conn.rollback()
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.cursor() as cur:
                cur.execute("TRUNCATE ledger_events")
        conn.rollback()
    with db.connect() as conn:
        assert ledger.verify(conn)["ok"] is True


def test_verify_reports_the_first_divergent_seq(db):
    _seed(db, 4)
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE ledger_events DISABLE TRIGGER ALL")
            cur.execute(
                "UPDATE ledger_events SET payload = payload || '{\"tampered\":1}'::jsonb "
                "WHERE seq = 3")
            cur.execute("ALTER TABLE ledger_events ENABLE TRIGGER ALL")
        conn.commit()
        result = ledger.verify(conn)
    assert result["ok"] is False
    assert result["first_divergence"] == 3
    # the chain itself (linkage) is intact — the payload no longer matches its hash
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT prev_hash, row_hash FROM ledger_events WHERE seq = 4")
            row = cur.fetchone()
            cur.execute("SELECT row_hash FROM ledger_events WHERE seq = 3")
            assert row["prev_hash"] == cur.fetchone()["row_hash"]


def test_preimage_recomputes_every_row_from_the_transcript_alone(db):
    _seed(db, 4)
    with db.connect() as conn:
        rows = ledger.transcript_rows(conn)
    previous = ledger.GENESIS_PREV
    for row in rows:
        recomputed = ledger.compute_row_hash(row["seq"], row["prev_hash"], row["event_type"],
                                             row["actor"], row["recorded_at_ms"], row["payload"])
        assert recomputed == row["row_hash"]
        assert row["prev_hash"] == previous
        previous = row["row_hash"]


def test_float_in_payload_is_refused_before_any_sql(db):
    with db.connect() as conn:
        with conn.cursor() as cur:
            with pytest.raises(Exception) as excinfo:
                ledger.append(cur, "SIGNALS_COMPLETED", "operator", {"motion": 0.15})
    assert "FloatInPayload" in type(excinfo.value).__name__
    with db.connect() as conn:
        assert ledger.head(conn)[0] == 1  # nothing was appended


def test_ten_thousand_rows_verify_under_two_seconds(db):
    with db.transaction() as cur:
        for index in range(10_000):
            ledger.append(cur, "WINDOW_PLANNED", "operator", {
                "source_id": "src", "window_id": f"w-{index}", "idx": index,
                "start_ms": index * 1000, "end_ms": index * 1000 + 1000,
                "boundary_kind": "grid", "window_count": 10_000,
            })
    with db.connect() as conn:
        started = time.monotonic()
        result = ledger.verify(conn)
        elapsed = time.monotonic() - started
    assert result["ok"] is True
    assert result["length"] == 10_001
    assert elapsed < 2.0, f"verification took {elapsed:.2f}s"


def test_incremental_verification_uses_a_checkpoint(db):
    _seed(db, 3)
    with db.connect() as conn:
        first = ledger.verify(conn)
    _seed(db, 2)
    with db.connect() as conn:
        second = ledger.verify(conn, from_seq=first["length"], expected_prev=first["head_hash"])
    assert second["ok"] is True
    assert second["length"] == 6
    assert second["head_hash"] != first["head_hash"]
