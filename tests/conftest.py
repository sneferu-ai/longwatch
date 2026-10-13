"""Shared test harness.

The harness owns its schema in a dedicated test database: every test function
gets a fresh Postgres schema inside ``TEST_DATABASE_URL`` (or ``DATABASE_URL``),
so the CHAIN scenario's deliberate corruption uses table-owner privilege —
``ALTER TABLE … DISABLE TRIGGER ALL`` — and no superuser bypass, and no test can
observe another's rows.

Scenarios that need a real tool (ffmpeg, a browser, the ONNX fixture) skip with
a named reason when the tool is absent; a skip is never reported as a pass.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from longwatch import ledger, migrations, patterns as patterns_mod  # noqa: E402
from longwatch.config import Config  # noqa: E402
from longwatch.db import Database  # noqa: E402

MODEL_FIXTURE = REPO / "tests" / "fixtures" / "model_fixture.onnx"
CLASS_MAP = REPO / "models" / "coco80.json"


def _database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or None


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def openssl_available() -> bool:
    return bool(shutil.which("openssl"))


def node_available() -> bool:
    return bool(shutil.which("node"))


def fixture_digest(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="session")
def dsn() -> str:
    url = _database_url()
    if not url:
        pytest.skip("no TEST_DATABASE_URL/DATABASE_URL configured — database scenarios skipped")
    return url


@pytest.fixture()
def schema(dsn, request) -> str:
    import psycopg

    module = re.sub(r"[^a-z0-9]+", "_", request.node.module.__name__.lower())[:32]
    name = f"lw_{module}_{os.urandom(3).hex()}"
    admin = psycopg.connect(dsn, autocommit=True)
    try:
        with admin.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
            cur.execute(f'CREATE SCHEMA "{name}"')
    finally:
        admin.close()
    yield name
    admin = psycopg.connect(dsn, autocommit=True)
    try:
        with admin.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
    finally:
        admin.close()


@pytest.fixture()
def db(dsn, schema) -> Database:
    database = Database(dsn, size=3, schema=schema)
    conn = database.dedicated()
    try:
        migrations.apply_migrations(conn)
        patterns_mod.seed_builtins(conn)
        ledger.ensure_genesis(conn)
    finally:
        conn.close()
    yield database
    database.close()


@pytest.fixture()
def cfg(tmp_path, dsn, schema) -> Config:
    config = Config(
        database_url=dsn,
        data_dir=str(tmp_path / "data"),
        offline=False,
        reviewer_name="operator",
        model_path=str(MODEL_FIXTURE),
        model_sha256=fixture_digest(MODEL_FIXTURE),
        class_map_path=str(CLASS_MAP),
        coverage_s=300.0,
        window_min_s=10.0,
        window_max_s=180.0,
        proxy_long_edge=640,
    )
    config.ensure_dirs()
    return config


@pytest.fixture()
def require_ffmpeg():
    if not ffmpeg_available():
        pytest.skip("ffmpeg/ffprobe are not available in this environment")


@pytest.fixture()
def require_model():
    if not MODEL_FIXTURE.is_file():
        pytest.skip("tests/fixtures/model_fixture.onnx is absent")


def make_video(path: Path, seconds: float = 6.0, size: str = "160x90") -> Path:
    """A tiny real video for ffmpeg-dependent scenarios."""
    path.parent.mkdir(parents=True, exist_ok=True)
    argv = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi",
            "-i", f"testsrc2=s={size}:d={seconds}", "-r", "12", "-an",
            "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(argv, check=True, capture_output=True)
    return path


def run_cli(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "longwatch", *argv],
                          cwd=str(REPO), capture_output=True, text=True, **kwargs)
