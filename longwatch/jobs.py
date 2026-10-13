"""The job pool (§5) — Postgres is the queue.

``SELECT … FOR UPDATE SKIP LOCKED`` gives durable, crash-resumable,
exactly-once-claimed dispatch with no broker and no second failure domain.
Each worker thread filters the claim query to its own kind set:

* media worker   — ``kind = 'ingest'`` (decode- and inference-bound);
* utility worker — ``kind IN ('export','purge','rescore')`` (I/O- and rule-bound).

Ledger appends and the state transitions they describe share one short
transaction; a crash leaves both or neither.

Every helper accepts either a live psycopg connection (the worker path, where
the connection is the caller's) or a :class:`~longwatch.db.Database` (the
request path, where the helper opens its own short transaction).
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass

from .ledger import psycopg_json

MEDIA_KINDS = ("ingest",)
UTILITY_KINDS = ("export", "purge", "rescore")

STAGE_FOR_KIND = {
    "ingest": ("probe", "windows", "proxies", "signals", "detect", "score"),
    "export": ("bundle",),
    "purge": ("purge",),
    "rescore": ("rescore",),
}

MAX_ATTEMPTS = 3
HEARTBEAT_S = 15


@contextlib.contextmanager
def _connection(target):
    """Accept either a live connection or a Database; open one when needed."""
    if hasattr(target, "cursor"):
        yield target
        return
    with target.connect() as conn:
        yield conn


@dataclass
class Job:
    id: str
    kind: str
    source_id: str | None
    stage: str | None
    pct: int
    attempts: int
    detail: str | None

    @classmethod
    def from_row(cls, row: dict) -> "Job":
        return cls(id=str(row["id"]), kind=row["kind"],
                   source_id=str(row["source_id"]) if row["source_id"] else None,
                   stage=row["stage"], pct=int(row["pct"]),
                   attempts=int(row["attempts"]), detail=row["detail"])


def enqueue(target, kind: str, source_id: str | None = None,
            detail: str | None = None) -> str:
    """Queue one job; returns its id."""
    if kind not in STAGE_FOR_KIND:
        raise ValueError(f"unknown job kind {kind!r}")
    with _connection(target) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO jobs (kind, source_id, detail) VALUES (%s, %s, %s) RETURNING id",
                (kind, source_id, detail),
            )
            job_id = str(cur.fetchone()["id"])
        conn.commit()
    return job_id


def reclaim_stale(target, lease_s: int) -> dict:
    """A job whose lease went stale is reclaimable by a worker of its kind set.

    After ``MAX_ATTEMPTS`` the job is marked failed with the last error
    preserved verbatim; it is never silently retried forever.
    """
    with _connection(target) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                   SET state = 'queued',
                       detail = COALESCE(detail, '') || ' [lease reclaimed]'
                 WHERE state = 'running'
                   AND beat_at IS NOT NULL
                   AND beat_at < now() - make_interval(secs => %s)
                   AND attempts < %s
                RETURNING id, kind
                """,
                (float(lease_s), MAX_ATTEMPTS),
            )
            reclaimed = [dict(row) for row in cur.fetchall()]
            cur.execute(
                """
                UPDATE jobs
                   SET state = 'failed',
                       error = COALESCE(error,
                                        'lease expired after ' || attempts || ' attempts'),
                       finished_at = now()
                 WHERE state = 'running'
                   AND beat_at IS NOT NULL
                   AND beat_at < now() - make_interval(secs => %s)
                   AND attempts >= %s
                RETURNING id
                """,
                (float(lease_s), MAX_ATTEMPTS),
            )
            failed = [str(row["id"]) for row in cur.fetchall()]
        conn.commit()
    return {"reclaimed": reclaimed, "failed": failed}


def claim(conn, kinds: tuple[str, ...]) -> Job | None:
    """Claim one queued job of the given kinds, exactly once."""
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH candidate AS (
                SELECT id FROM jobs
                 WHERE state = 'queued' AND kind = ANY(%s)
                 ORDER BY created_at
                 FOR UPDATE SKIP LOCKED
                 LIMIT 1
            )
            UPDATE jobs
               SET state = 'running', claimed_at = now(), beat_at = now(),
                   attempts = attempts + 1
             WHERE id IN (SELECT id FROM candidate)
            RETURNING id, kind, source_id, stage, pct, attempts, detail
            """,
            (list(kinds),),
        )
        row = cur.fetchone()
    conn.commit()
    return Job.from_row(row) if row else None


def heartbeat(target, job_id: str) -> None:
    with _connection(target) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET beat_at = now() WHERE id = %s", (job_id,))
        conn.commit()


def progress(target, job_id: str, stage: str | None = None, pct: int | None = None,
             detail: str | None = None) -> None:
    with _connection(target) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                   SET stage = COALESCE(%s, stage),
                       pct = COALESCE(%s, pct),
                       detail = COALESCE(%s, detail),
                       beat_at = now()
                 WHERE id = %s
                """,
                (stage, pct, detail, job_id),
            )
        conn.commit()


def finish(target, job_id: str, *, error: str | None = None,
           detail: str | None = None) -> None:
    with _connection(target) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                   SET state = %s, error = %s, detail = COALESCE(%s, detail),
                       pct = CASE WHEN %s THEN 100 ELSE pct END,
                       finished_at = now()
                 WHERE id = %s
                """,
                ("failed" if error else "succeeded", error, detail,
                 error is None, job_id),
            )
        conn.commit()


def get_job(target, job_id: str) -> dict | None:
    with _connection(target) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
            return cur.fetchone()


def pending_kinds(target, kinds: tuple[str, ...]) -> int:
    with _connection(target) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM jobs WHERE state IN ('queued','running')"
                " AND kind = ANY(%s)",
                (list(kinds),),
            )
            return int(cur.fetchone()["n"])


def set_ml_failed(target, source_id: str, reason: str) -> int:
    """The pre-stat rule's consequence: not-yet-evaluated windows fail loudly."""
    with _connection(target) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE windows
                   SET ml_state = 'failed', ml_failure_reason = %s
                 WHERE source_id = %s AND (ml_state IS NULL OR ml_state = 'ok')
                """,
                (reason, source_id),
            )
            count = cur.rowcount
        conn.commit()
    return int(count)


def jsonb(document):
    return psycopg_json(document)
