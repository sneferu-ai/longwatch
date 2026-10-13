"""Database access — psycopg v3, raw cursors, synchronous pool, no ORM (§5).

A fixed pool (size 4) serves request handlers, which are plain ``def``
functions executed on FastAPI's AnyIO threadpool; each worker thread takes one
dedicated connection of its own.  Blocking DB calls therefore never run on the
event loop.
"""
from __future__ import annotations

import contextlib
import queue
import threading
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

DEFAULT_POOL_SIZE = 4


class Database:
    """A small synchronous connection pool over one DSN."""

    def __init__(self, dsn: str, size: int = DEFAULT_POOL_SIZE, schema: str | None = None):
        if not dsn:
            raise ValueError("DATABASE_URL is required and is never defaulted")
        self.dsn = dsn
        self.size = max(1, int(size))
        self.schema = schema
        self._pool: queue.LifoQueue = queue.LifoQueue(maxsize=self.size)
        self._created = 0
        self._lock = threading.Lock()
        self._closed = False

    # ------------------------------------------------------------- internals
    def _new_connection(self) -> psycopg.Connection:
        conn = psycopg.connect(self.dsn, row_factory=dict_row, autocommit=False)
        if self.schema:
            with conn.cursor() as cur:
                cur.execute(f'SET search_path TO "{self.schema}"')
            conn.commit()
        return conn

    def _acquire(self) -> psycopg.Connection:
        if self._closed:
            raise RuntimeError("database pool is closed")
        while True:
            try:
                conn = self._pool.get_nowait()
            except queue.Empty:
                with self._lock:
                    if self._created < self.size:
                        self._created += 1
                        make_new = True
                    else:
                        make_new = False
                if make_new:
                    try:
                        return self._new_connection()
                    except Exception:
                        with self._lock:
                            self._created -= 1
                        raise
                conn = self._pool.get(timeout=30)
            if conn.closed or conn.broken:
                with self._lock:
                    self._created -= 1
                with contextlib.suppress(Exception):
                    conn.close()
                continue
            return conn

    def _release(self, conn: psycopg.Connection) -> None:
        if self._closed:
            with contextlib.suppress(Exception):
                conn.close()
            return
        with contextlib.suppress(Exception):
            if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                conn.rollback()
        try:
            self._pool.put_nowait(conn)
        except queue.Full:  # pragma: no cover - defensive
            with contextlib.suppress(Exception):
                conn.close()

    # ----------------------------------------------------------------- public
    @contextlib.contextmanager
    def connect(self) -> Iterator[psycopg.Connection]:
        """Check out a pooled connection; uncommitted work rolls back."""
        conn = self._acquire()
        try:
            yield conn
        except Exception:
            with contextlib.suppress(Exception):
                conn.rollback()
            raise
        finally:
            self._release(conn)

    @contextlib.contextmanager
    def transaction(self) -> Iterator[psycopg.Cursor]:
        """A short, SQL-only transaction (never contains media or file work)."""
        with self.connect() as conn:
            with conn.cursor() as cur:
                yield cur
            conn.commit()

    def dedicated(self) -> psycopg.Connection:
        """A private connection for a worker thread; the caller closes it."""
        return self._new_connection()

    def fetch_all(self, sql: str, params: Any = None) -> list[dict]:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())

    def fetch_one(self, sql: str, params: Any = None) -> dict | None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()

    def execute(self, sql: str, params: Any = None) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()

    def ping(self) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()

    def close(self) -> None:
        self._closed = True
        while True:
            try:
                conn = self._pool.get_nowait()
            except queue.Empty:
                break
            with contextlib.suppress(Exception):
                conn.close()
