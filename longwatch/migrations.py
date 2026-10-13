"""Plain-SQL migration runner (§5).

Files live in ``migrations/NNN_*.sql`` and are applied in filename order, each
wrapped in a single transaction and recorded in ``schema_migrations``, guarded
by a Postgres advisory lock.  The runner executes ``CREATE TABLE IF NOT EXISTS
schema_migrations`` itself **before** applying any migration file — the first
migration does not create its own bookkeeping table.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import psycopg

MIGRATIONS_DIRNAME = "migrations"
ADVISORY_LOCK_KEY = 0x10_6E_67_77_74  # 'longwatch' in a stable int

SCHEMA_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now())
"""


def migrations_dir(root: Path | None = None) -> Path:
    if root is not None:
        return Path(root) / MIGRATIONS_DIRNAME
    return Path(__file__).resolve().parent.parent / MIGRATIONS_DIRNAME


def available_migrations(root: Path | None = None) -> list[Path]:
    directory = migrations_dir(root)
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.sql") if p.is_file())


def migration_version(path: Path) -> str:
    return path.name


def migration_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def applied_versions(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations")
        return {row["version"] for row in cur.fetchall()}


def apply_migrations(conn, root: Path | None = None, *, schema: str | None = None) -> list[str]:
    """Apply every unapplied migration.  Idempotent; a re-run is a no-op."""
    applied: list[str] = []
    with conn.cursor() as cur:
        if schema:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            cur.execute(f'SET search_path TO "{schema}"')
        cur.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
    conn.commit()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_MIGRATIONS_SQL)
        conn.commit()
        done = applied_versions(conn)
        for path in available_migrations(root):
            version = migration_version(path)
            if version in done:
                continue
            sql = path.read_text(encoding="utf-8")
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO schema_migrations (version) VALUES (%s)", (version,)
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            applied.append(version)
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
        conn.commit()
    return applied


def migrate_dsn(dsn: str, root: Path | None = None, schema: str | None = None) -> list[str]:
    from psycopg.rows import dict_row

    # the pool and every reader in this module use dict-shaped rows; the
    # standalone DSN path must match or the second migrate crashes
    conn = psycopg.connect(dsn, autocommit=False, row_factory=dict_row)
    try:
        if schema:
            with conn.cursor() as cur:
                cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            conn.commit()
        return apply_migrations(conn, root, schema=schema)
    finally:
        conn.close()
