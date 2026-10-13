"""The two in-process worker threads (§5).

A separate worker container would starve the pipeline (the proof environment's
worker profile is smaller than what ffmpeg plus ONNX Runtime need), so the
application process hosts two plain threads over one shared CPU budget:

* **media worker**   — claims ``kind='ingest'`` (decode- and inference-bound);
* **utility worker** — claims ``kind IN ('export','purge','rescore')``
  (I/O- and rule-bound).

An operator who starts a corpus ingest can still cut an export, run a purge, or
re-score a pattern the same hour — slower under contention, never unavailable.
"""
from __future__ import annotations

import logging
import threading
import time

from . import jobs as jobs_mod, pipeline

log = logging.getLogger("longwatch.workers")

IDLE_SLEEP_S = 0.5


class Worker(threading.Thread):
    """One worker thread over the Postgres job queue."""

    def __init__(self, db, cfg, kinds: tuple[str, ...], name: str,
                 detector_factory=None, idle_sleep_s: float = IDLE_SLEEP_S):
        super().__init__(name=name, daemon=True)
        self.db = db
        self.cfg = cfg
        self.kinds = tuple(kinds)
        self.detector_factory = detector_factory
        self.idle_sleep_s = idle_sleep_s
        self._stop_event = threading.Event()
        self.jobs_processed = 0
        self.last_error: str | None = None
        self.last_job_id: str | None = None
        self.heartbeat_s = jobs_mod.HEARTBEAT_S

    # ------------------------------------------------------------------ run
    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:  # pragma: no cover - thread body, exercised via run_once
        conn = None
        try:
            conn = self.db.dedicated()
            while not self._stop_event.is_set():
                try:
                    processed = self.run_once(conn)
                except Exception as exc:  # noqa: BLE001 - a worker never dies silently
                    self.last_error = str(exc)
                    log.exception("worker %s failed a job: %s", self.name, exc)
                    time.sleep(self.idle_sleep_s)
                    continue
                if not processed:
                    self._stop_event.wait(self.idle_sleep_s)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

    def run_once(self, conn) -> bool:
        """Claim and run at most one job.  Returns True when a job ran."""
        jobs_mod.reclaim_stale(conn, self.cfg.job_lease_s)
        job = jobs_mod.claim(conn, self.kinds)
        if job is None:
            return False
        self.last_job_id = job.id
        stop_beat = threading.Event()
        beater = threading.Thread(target=self._beat, args=(job.id, stop_beat),
                                  name=f"{self.name}-heartbeat", daemon=True)
        beater.start()
        try:
            detail = pipeline.run_job(self.db, job, self.cfg,
                                      detector_factory=self.detector_factory)
            jobs_mod.finish(conn, job.id, detail=_detail_text(detail))
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            log.error("job %s (%s) failed: %s", job.id, job.kind, exc)
            try:
                jobs_mod.finish(conn, job.id, error=str(exc)[:4000])
            except Exception:  # noqa: BLE001 - the connection may be gone
                log.exception("could not record the failure of job %s", job.id)
        finally:
            stop_beat.set()
            beater.join(timeout=5.0)
        self.jobs_processed += 1
        return True

    def _beat(self, job_id: str, stop: threading.Event) -> None:
        """Heartbeat the lease every 15 s on the worker's own connection."""
        conn = None
        try:
            conn = self.db.dedicated()
            while not stop.wait(self.heartbeat_s):
                jobs_mod.heartbeat(conn, job_id)
        except Exception:  # noqa: BLE001 - a heartbeat failure is not a job failure
            log.warning("heartbeat failed for job %s", job_id)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass


def _detail_text(detail) -> str:
    if detail is None:
        return ""
    if isinstance(detail, dict):
        import json
        return json.dumps(detail, sort_keys=True, default=str)[:4000]
    return str(detail)[:4000]


class WorkerPool:
    """Both worker threads, started and stopped with the application lifespan."""

    def __init__(self, db, cfg, detector_factory=None):
        self.media = Worker(db, cfg, jobs_mod.MEDIA_KINDS, "longwatch-media",
                            detector_factory=detector_factory)
        self.utility = Worker(db, cfg, jobs_mod.UTILITY_KINDS, "longwatch-utility",
                              detector_factory=detector_factory)

    def start(self) -> None:
        self.media.start()
        self.utility.start()

    def stop(self, timeout: float = 10.0) -> None:
        self.media.stop()
        self.utility.stop()
        self.media.join(timeout=timeout)
        self.utility.join(timeout=timeout)

    def status(self) -> dict:
        return {
            "media": {"alive": self.media.is_alive(), "kinds": list(self.media.kinds),
                      "jobs_processed": self.media.jobs_processed,
                      "last_job_id": self.media.last_job_id,
                      "last_error": self.media.last_error},
            "utility": {"alive": self.utility.is_alive(), "kinds": list(self.utility.kinds),
                        "jobs_processed": self.utility.jobs_processed,
                        "last_job_id": self.utility.last_job_id,
                        "last_error": self.utility.last_error},
        }


def drain_once(db, cfg, kinds: tuple[str, ...], detector_factory=None) -> bool:
    """Run one queued job synchronously — the tests' and the CLI's door."""
    conn = db.dedicated()
    try:
        jobs_mod.reclaim_stale(conn, cfg.job_lease_s)
        job = jobs_mod.claim(conn, kinds)
        if job is None:
            return False
        try:
            detail = pipeline.run_job(db, job, cfg, detector_factory=detector_factory)
            jobs_mod.finish(conn, job.id, detail=_detail_text(detail))
        except Exception as exc:  # noqa: BLE001
            jobs_mod.finish(conn, job.id, error=str(exc)[:4000])
            raise
        return True
    finally:
        conn.close()
