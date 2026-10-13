"""Application assembly: lifespan, workers, offline guard, ledger verification.

Order of operations per boot (§5): the entrypoint waits for the DB, migrates
idempotently, seeds the builtin patterns idempotently on ``(slug, version)``,
then execs ``python -m longwatch serve``, whose lifespan installs the offline
guard **first**, re-checks migrations (no-op), opens the DB pool, starts both
worker threads and the background ledger-verification task.

Demo data is **not** seeded here — it exists only behind the explicit operator
action of §16.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import FastAPI

from . import __version__, detect as detect_mod, ledger, migrations, patterns as patterns_mod
from . import signing
from .api import install_middleware, install_routes
from .config import Config, get_config
from .db import Database
from .workers import WorkerPool

log = logging.getLogger("longwatch.app")


class DetectorCache:
    """One ONNX session per (model, class map) pair — allocated once."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._key: tuple | None = None
        self._detector: detect_mod.Detector | None = None

    def __call__(self) -> detect_mod.Detector:
        key = (self.cfg.model_path, self.cfg.class_map_path, self.cfg.model_sha256,
               self.cfg.ort_threads)
        if self._detector is None or self._key != key:
            self._detector = detect_mod.Detector(self.cfg)
            self._key = key
        return self._detector


def bootstrap_database(cfg: Config, db: Database) -> dict:
    """Migrate, seed patterns, ensure the genesis row.  Idempotent."""
    with db.connect() as conn:
        applied = migrations.apply_migrations(conn)
    with db.connect() as conn:
        seeded = patterns_mod.seed_builtins(conn, actor="migration")
        genesis = ledger.ensure_genesis(conn, actor="system")
    return {"migrations_applied": applied, "patterns_seeded": seeded,
            "genesis": genesis}


def verify_ledger_incrementally(db: Database) -> dict:
    """Re-verify newer rows against the persisted checkpoint."""
    with db.connect() as conn:
        checkpoint = db.fetch_one(
            "SELECT last_seq_verified, head_hash FROM ledger_verify_state WHERE id = 1")
        from_seq = int(checkpoint["last_seq_verified"]) if checkpoint else 0
        expected_prev = checkpoint["head_hash"] if checkpoint else None
        result = ledger.verify(conn, from_seq=from_seq, expected_prev=expected_prev)
        if result["ok"]:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ledger_verify_state (id, last_seq_verified, head_hash, verified_at)
                    VALUES (1, %s, %s, now())
                    ON CONFLICT (id) DO UPDATE
                       SET last_seq_verified = EXCLUDED.last_seq_verified,
                           head_hash = EXCLUDED.head_hash,
                           verified_at = EXCLUDED.verified_at
                    """,
                    (int(result["length"]), result["head_hash"]),
                )
            conn.commit()
    return result


async def _ledger_verification_loop(app: FastAPI, interval_s: int) -> None:
    while True:
        await asyncio.sleep(max(5, int(interval_s)))
        try:
            result = await asyncio.to_thread(verify_ledger_incrementally, app.state.db)
            app.state.ledger_verify = result
            if not result["ok"]:
                log.error("LEDGER CORRUPTION detected at seq %s", result["first_divergence"])
        except Exception as exc:  # noqa: BLE001 - never kill the loop
            log.warning("incremental ledger verification failed: %s", exc)


async def _inbox_poll_loop(app: FastAPI, interval_s: float) -> None:
    """The optional inbox auto-scan cadence (LONGWATCH_INBOX_POLL_S)."""
    from . import pipeline
    while True:
        await asyncio.sleep(max(5.0, float(interval_s)))
        try:
            await asyncio.to_thread(
                pipeline.scan_inbox, app.state.db, app.state.cfg,
                app.state.cfg.reviewer_name)
        except Exception as exc:  # noqa: BLE001 - a scan failure is not a crash
            log.warning("inbox poll failed: %s", exc)


def create_app(cfg: Config | None = None, db: Database | None = None,
               *, start_workers: bool = True, install_guard: bool | None = None) -> FastAPI:
    cfg = cfg or get_config()
    app = FastAPI(title="LongWatch", version=__version__,
                  description="Offline footage triage and evidence-integrity ledger")
    app.state.cfg = cfg
    app.state.db = db or Database(cfg.database_url)
    app.state.detector_factory = DetectorCache(cfg)
    app.state.start_workers = start_workers
    app.state.install_guard = cfg.offline if install_guard is None else install_guard
    app.state.ledger_verify = None
    app.state.workers = None
    # LONGWATCH_UPLOAD_CONCURRENCY — the upload handler's stream semaphore
    app.state.upload_semaphore = asyncio.Semaphore(int(cfg.upload_concurrency))

    install_middleware(app)
    install_routes(app)

    @app.on_event("startup")
    async def _startup() -> None:  # pragma: no cover - exercised by the server
        if app.state.install_guard:
            from . import offline_guard
            info = offline_guard.bootstrap_and_install(cfg.database_url)
            app.state.offline_guard = info
            log.info("offline guard installed: %s", info)
        cfg.ensure_dirs()
        bootstrap_database(cfg, app.state.db)
        try:
            identity = signing.key_identity(cfg.key_path)
            app.state.key_identity = identity
            log.info("LongWatch signing public key fingerprint: %s", identity["fingerprint"])
        except Exception as exc:  # noqa: BLE001
            log.error("signing key unavailable: %s", exc)
        app.state.ledger_verify = await asyncio.to_thread(verify_ledger_incrementally,
                                                          app.state.db)
        if app.state.start_workers:
            pool = WorkerPool(app.state.db, cfg, detector_factory=app.state.detector_factory)
            pool.start()
            app.state.workers = pool
        app.state.verify_task = asyncio.create_task(
            _ledger_verification_loop(app, cfg.ledger_verify_s))
        app.state.inbox_task = None
        if cfg.inbox_dir and cfg.inbox_poll_s > 0:
            app.state.inbox_task = asyncio.create_task(
                _inbox_poll_loop(app, cfg.inbox_poll_s))
            log.info("inbox polling every %.1fs on %s", cfg.inbox_poll_s, cfg.inbox_dir)

    @app.on_event("shutdown")
    async def _shutdown() -> None:  # pragma: no cover
        for name in ("verify_task", "inbox_task"):
            task = getattr(app.state, name, None)
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if app.state.workers is not None:
            await asyncio.to_thread(app.state.workers.stop)

    return app


def serve(cfg: Config | None = None) -> None:  # pragma: no cover - process entry
    import uvicorn

    cfg = cfg or get_config()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if not cfg.database_url:
        raise SystemExit("DATABASE_URL is required and is never defaulted")
    app = create_app(cfg)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


def migrate_only(cfg: Config | None = None) -> dict:
    cfg = cfg or get_config()
    db = Database(cfg.database_url)
    try:
        return bootstrap_database(cfg, db)
    finally:
        db.close()
