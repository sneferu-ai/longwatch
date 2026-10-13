"""CLI — the documented entrypoint sequence (§18).

``entrypoint.sh`` runs ``python -m longwatch migrate`` and then execs
``serve``, which migrates again.  The second migrate — every container
restart, every documented start — must be a clean no-op, and the product's own
setup commands are the only door a clean machine gets.  These tests drive the
real CLI as a subprocess against a throwaway schema, the way the container
entrypoint does.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest


def _schema_dsn(dsn: str, schema: str) -> str:
    sep = "&" if "?" in dsn else "?"
    return f"{dsn}{sep}options=-csearch_path%3D{schema}"


@pytest.fixture()
def cli_dsn(dsn):
    """A fresh schema behind a DSN, exactly as a first boot sees it."""
    import psycopg

    schema = f"lw_cli_{uuid.uuid4().hex[:8]}"
    admin = psycopg.connect(dsn, autocommit=True)
    try:
        with admin.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        admin.close()
    yield _schema_dsn(dsn, schema)
    admin = psycopg.connect(dsn, autocommit=True)
    try:
        with admin.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    finally:
        admin.close()


def _run_cli(argv, dsn):
    from tests.conftest import run_cli

    env = dict(os.environ, DATABASE_URL=dsn)
    return run_cli(argv, env=env, timeout=120)


def test_migrate_twice_is_a_clean_noop(cli_dsn):
    """The entrypoint's migrate → serve (which migrates again) must boot."""
    first = _run_cli(["migrate"], cli_dsn)
    assert first.returncode == 0, first.stderr[-2000:]
    applied_first = json.loads(first.stdout)
    assert applied_first["migrations_applied"], "a fresh schema migrates"

    second = _run_cli(["migrate"], cli_dsn)
    assert second.returncode == 0, (
        "the second migrate — every restart of the documented entrypoint — "
        f"must not crash:\n{second.stderr[-2000:]}")
    applied_second = json.loads(second.stdout)
    assert applied_second["migrations_applied"] == []
    assert applied_second["patterns_seeded"] == []


def test_patterns_export_round_trips_the_seeded_builtins(cli_dsn):
    assert _run_cli(["migrate"], cli_dsn).returncode == 0
    result = _run_cli(["patterns", "export"], cli_dsn)
    assert result.returncode == 0, result.stderr[-2000:]
    slugs = {row["slug"] for row in json.loads(result.stdout)["patterns"]}
    assert slugs == {"stationary_animal_with_person", "sustained_motion_low_light",
                     "long_silence_animals_present"}


def test_verify_bundle_cli_refuses_a_missing_directory_cleanly(cli_dsn):
    result = _run_cli(["verify-bundle", "/no/such/bundle"], cli_dsn)
    assert result.returncode == 2
    assert "not a directory" in result.stderr
