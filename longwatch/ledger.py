"""The custody ledger (§7) — append-only, hash-chained, serialized.

Every append routes through :func:`append`, which takes
``pg_advisory_xact_lock(hashtext('longwatch:ledger'))`` as its first statement
inside the caller's transaction, reads the tail row, computes the byte-exact
hash and inserts.  No transaction that contains an append contains media or
file work, so the advisory lock is held for milliseconds of SQL.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from . import canonical

GENESIS_PREV = "0" * 64
LEDGER_VERSION = "longwatch-ledger-v1"
ADVISORY_LOCK_KEY = "longwatch:ledger"

#: Every event type this build emits (§7.5).
EVENT_TYPES = (
    "LEDGER_GENESIS",
    "SOURCE_REGISTERED",
    "SOURCE_DUPLICATE",
    "SOURCE_UNREADABLE",
    "SOURCE_MISSING",
    "SOURCE_DIGEST_MISMATCH",
    "WINDOW_PLANNED",
    "CLIP_DERIVED",
    "SIGNALS_COMPLETED",
    "DETECTION_COMPLETED",
    "ML_FAILURE_ACKED",
    "CANDIDATE_SCORED",
    "DECISION_RECORDED",
    "PATTERN_UPDATED",
    "RESCORE_COMPLETED",
    "EXPORT_PRODUCED",
    "TRANSFORM_APPLIED",
    "DERIVED_DESTROYED",
    "DEMO_SEEDED",
)


class LedgerError(RuntimeError):
    """A chain-level failure (collision, missing genesis, divergence)."""


def now_ms() -> int:
    """The single wall-clock reading that enters a hash."""
    return time.time_ns() // 1_000_000


def preimage(seq: int, prev_hash: str, event_type: str, actor: str,
             recorded_at_ms: int, payload: Any) -> bytes:
    """The byte-exact hash preimage of §7.2."""
    body = canonical.canonical_json(payload)
    text = (
        f"{LEDGER_VERSION}|{int(seq)}|{prev_hash}|{event_type}|{actor}|"
        f"{int(recorded_at_ms)}|{body}"
    )
    return text.encode("utf-8")


def compute_row_hash(seq: int, prev_hash: str, event_type: str, actor: str,
                     recorded_at_ms: int, payload: Any) -> str:
    return hashlib.sha256(
        preimage(seq, prev_hash, event_type, actor, recorded_at_ms, payload)
    ).hexdigest()


def tail(cur) -> tuple[int, str]:
    """(seq, row_hash) of the last row, or (0, GENESIS_PREV) when empty."""
    cur.execute("SELECT seq, row_hash FROM ledger_events ORDER BY seq DESC LIMIT 1")
    row = cur.fetchone()
    if row is None:
        return 0, GENESIS_PREV
    return int(row["seq"]), row["row_hash"]


def append(cur, event_type: str, actor: str, payload: Any = None,
           recorded_at_ms: int | None = None) -> dict:
    """Append one event inside the caller's short transaction.

    Returns ``{"seq", "row_hash", "prev_hash", "recorded_at_ms"}``.
    """
    if event_type not in EVENT_TYPES:
        raise LedgerError(f"unknown event type {event_type!r}")
    document = dict(payload or {})
    canonical.validate(document)  # FloatInPayload raises here, before any SQL

    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (ADVISORY_LOCK_KEY,))
    last_seq, prev_hash = tail(cur)
    seq = last_seq + 1
    stamp = int(recorded_at_ms) if recorded_at_ms is not None else now_ms()
    row_hash = compute_row_hash(seq, prev_hash, event_type, actor, stamp, document)
    try:
        cur.execute(
            """
            INSERT INTO ledger_events
                (seq, prev_hash, row_hash, event_type, actor, payload,
                 recorded_at, recorded_at_ms)
            VALUES (%s, %s, %s, %s, %s, %s, to_timestamp(%s / 1000.0), %s)
            """,
            (seq, prev_hash, row_hash, event_type, actor,
             psycopg_json(document), stamp, stamp),
        )
    except Exception as exc:  # row_hash UNIQUE collision is a hard failure
        raise LedgerError(f"ledger append failed at seq {seq}: {exc}") from exc
    return {"seq": seq, "row_hash": row_hash, "prev_hash": prev_hash,
            "recorded_at_ms": stamp}


def ensure_genesis(conn, actor: str = "system") -> dict | None:
    """Write the genesis row when the ledger is empty.  Idempotent."""
    with conn.cursor() as cur:
        cur.execute("SELECT seq FROM ledger_events ORDER BY seq LIMIT 1")
        if cur.fetchone() is not None:
            return None
        result = append(cur, "LEDGER_GENESIS", actor, {"ledger": "longwatch", "v": 1})
    conn.commit()
    return result


def head(conn) -> tuple[int, str]:
    with conn.cursor() as cur:
        return tail(cur)


def verify(conn, from_seq: int = 0, expected_prev: str | None = None) -> dict:
    """Walk the chain from ``from_seq`` (exclusive) and report the first divergence."""
    if expected_prev is None:
        expected_prev = GENESIS_PREV if from_seq == 0 else None
    prev = expected_prev
    length = int(from_seq)
    first_divergence: int | None = None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT seq, prev_hash, row_hash, event_type, actor, payload, recorded_at_ms
              FROM ledger_events
             WHERE seq > %s
             ORDER BY seq
            """,
            (int(from_seq),),
        )
        for row in cur:
            seq = int(row["seq"])
            length = seq
            if first_divergence is not None:
                continue
            payload = row["payload"]
            if isinstance(payload, str):
                payload = canonical.parse(payload)
            recomputed = compute_row_hash(seq, row["prev_hash"], row["event_type"],
                                          row["actor"], int(row["recorded_at_ms"]), payload)
            if prev is not None and row["prev_hash"] != prev:
                first_divergence = seq
            elif recomputed != row["row_hash"]:
                first_divergence = seq
            prev = row["row_hash"]
    return {
        "ok": first_divergence is None,
        "length": length,
        "head_hash": prev if prev is not None else GENESIS_PREV,
        "first_divergence": first_divergence,
    }


def transcript_rows(conn, upto_seq: int | None = None) -> list[dict]:
    """Rows 1..N as the frozen ``ledger.jsonl`` transcript."""
    sql = ("SELECT seq, prev_hash, row_hash, event_type, actor, payload, "
           "recorded_at_ms, to_char(recorded_at, 'YYYY-MM-DD\"T\"HH24:MI:SS.USOF') AS recorded_at_iso "
           "FROM ledger_events")
    params: tuple = ()
    if upto_seq is not None:
        sql += " WHERE seq <= %s"
        params = (int(upto_seq),)
    sql += " ORDER BY seq"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = []
        for row in cur:
            payload = row["payload"]
            if isinstance(payload, str):
                payload = canonical.parse(payload)
            rows.append({
                "seq": int(row["seq"]),
                "prev_hash": row["prev_hash"],
                "row_hash": row["row_hash"],
                "event_type": row["event_type"],
                "actor": row["actor"],
                "payload": payload,
                "recorded_at_ms": int(row["recorded_at_ms"]),
                "recorded_at_iso": row["recorded_at_iso"],
            })
    return rows


def psycopg_json(document) -> Any:
    """Hand a validated document to psycopg's jsonb adapter."""
    from psycopg.types.json import Jsonb
    return Jsonb(document)
