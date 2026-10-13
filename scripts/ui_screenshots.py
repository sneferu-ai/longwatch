#!/usr/bin/env python3
"""Screenshot + DOM-contract pass over the built SPA, against a mocked API.

The runtime image needs Postgres, ffmpeg and a real corpus to boot the server;
this harness serves the *built* ``longwatch/static`` bundle from a local HTTP
server and answers ``/api/v1/*`` from fixtures shaped exactly like the real
responses (see BACKEND_MANIFEST.md and longwatch/api.py).  It is a development
tool, not a test: it produces the screenshots and the DOM assertions the round
summary quotes, and it fails loudly on a console error or a missing hook.

    python3 scripts/ui_screenshots.py --out /tmp/longwatch-shots
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "longwatch" / "static"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from longwatch import rules as _rules  # noqa: E402  (the real published schema)

PATTERN_SCHEMA = _rules.pattern_schema()

#: Whether the fixture register already holds sources.  The real product starts
#: empty and the demo fixture is an explicit operator action; set this to False
#: to exercise that path (the demo-seed button renders only on an empty
#: register, exactly as SPEC §11 requires).
SEEDED = True

SOURCE_DEMO = "11111111-1111-4111-8111-111111111111"
SOURCE_UPLOAD = "22222222-2222-4222-8222-222222222222"
WINDOW_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

SENTENCE_FLAGSHIP = (
    "dog presence held 41s at motion energy <= 0.014 (tolerance 12s) while a person was present 38s of the same span"
)
SENTENCE_P85 = "motion above this recording's p85 for 6s while luma below its p10"
SENTENCE_SILENCE = "silence gap of 11.5s with sheep present 22s and no person detected"

DISCLOSURES = {
    "integrity_statement": (
        "The SHA-256 digests in this bundle show that each listed excerpt file is byte-identical to material cut "
        "from a source file whose digest LongWatch recorded when it read that file, and that the accompanying custody "
        "record has not changed since each entry was written. They show nothing about what happened to the footage "
        "before LongWatch received it, nothing about when it was recorded, and nothing about whether depicted conduct "
        "was lawful, humane, or otherwise. LongWatch makes exactly one claim: the listed time ranges deserve "
        "examination by a qualified person, and the measured reasons are attached."
    ),
    "time_basis": "server-local clock at processing time; not synchronized to any external time authority",
    "review_medium": (
        "lossy working copy (long edge <= 640px, CRF 28); decisions were rendered on this working copy"
    ),
    "model_limits": (
        "The bundled detector is a general COCO-trained model: detections are coarse ranking inputs, not findings. "
        "Windows where it could not run are queued as detection-incomplete."
    ),
    "person_absent": (
        "A window with no person detected is not evidence that no person was present; it is a window the detector "
        "did not flag."
    ),
    "trust_boundary": (
        "Single-operator offline appliance: one reviewer identity from configuration, stamped on every human action. "
        "Attribution, not access control."
    ),
}

STATUS = {
    "database": "ok",
    "model": {"ok": True, "classes": 80, "provenance": "fetched+pinned (yolov8n.onnx sha256 0f1e…)"},
    "class_map": {"ok": True, "classes": 80},
    "workers": {
        "media": {"alive": True, "jobs_processed": 14, "last_error": None},
        "utility": {"alive": True, "jobs_processed": 6, "last_error": None},
    },
    "offline_guard": {
        "installed": True,
        "database_host": "db.internal",
        "sanctioned_egress": "DNS resolution of db.internal only",
    },
    "ml_failed_count": 1,
    "key": {"fingerprint": "9c1f4d2ab7e05c8836f0a1d4e7b2935c40a8f6d1e2b3c4d5a6f708192a3b4c5d"},
    "auth": {"token_required": False, "trust_boundary": DISCLOSURES["trust_boundary"]},
    "disclosures": DISCLOSURES,
    "export_defaults": {"blur_faces": False, "top_n": 200, "states": ["accepted", "flagged"]},
}


def candidate(cid: str, slug: str, version: int, score: int, sentence: str, start: float, end: float,
              state: str = "open", demo: bool = True, synthetic: bool = False) -> dict:
    return {
        "kind": "candidate",
        "candidate_id": cid,
        "window_id": WINDOW_A,
        "source_id": SOURCE_DEMO,
        "source_name": "barn-audit-2026-03-14.mp4",
        "clip_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        "start_s": start,
        "end_s": end,
        "pattern_slug": slug,
        "pattern_version": version,
        "score": score,
        "sentence": sentence,
        "state": state,
        "synthetic": synthetic,
        "demo": demo,
    }


CANDIDATES = [
    candidate("c1", "stationary_animal_with_person", 1, 91, SENTENCE_FLAGSHIP, 12.0, 53.0, synthetic=True),
    candidate("c2", "sustained_motion_low_light", 1, 84, SENTENCE_P85, 54.0, 60.0),
    candidate("c3", "long_silence_animal_no_person", 1, 77, SENTENCE_SILENCE, 30.0, 52.0, state="accepted"),
    candidate("c4", "sustained_motion_low_light", 1, 63, "motion above this recording's p85 for 9s while luma below its p10", 60.0, 72.0),
    candidate("c5", "stationary_animal_with_person", 1, 58, "sheep presence held 33s at motion energy <= 0.011 (tolerance 12s) while a person was present 30s of the same span", 72.0, 96.0),
    candidate("c6", "long_silence_animal_no_person", 1, 44, "silence gap of 18.0s with cow present 18s and no person detected", 96.0, 118.0, state="flagged"),
    candidate("c7", "sustained_motion_low_light", 1, 31, "motion above this recording's p85 for 4s while luma below its p10", 118.0, 126.0),
]

ML_FAILED = {
    "kind": "ml_failed",
    "window_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    "source_id": SOURCE_UPLOAD,
    "source_name": "yard-camera-04.mp4",
    "start_s": 240.0,
    "end_s": 300.0,
    "reason": "decode failed: moov atom not found after 240.0s",
    "label": "detection incomplete — manual review required",
}

DETAIL = {
    "candidate_id": "c1",
    "window_id": WINDOW_A,
    "source_id": SOURCE_DEMO,
    "source_name": "barn-audit-2026-03-14.mp4",
    "clip_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    "start_s": 12.0,
    "end_s": 53.0,
    "pattern_slug": "stationary_animal_with_person",
    "pattern_version": 1,
    "score": 91,
    "sentence": SENTENCE_FLAGSHIP,
    "state": "open",
    "evidence": {
        "duration_ms": 41000,
        "gap_ms": 12000,
        "gap_tolerance_ms": 12000,
        "motion_mean_milli": 14,
        "class": "dog",
        "person_present_ms": 38000,
        "samples": 41,
        "synthetic": True,
    },
    "synthetic": True,
    "demo": True,
    "ml_state": "ok",
    "detections_summary": {
        "dog": {"positive_frames": 33, "conf_max_milli": 812, "conf_p50_milli": 640, "samples": 41, "sample_interval_ms": 1000},
        "person": {"positive_frames": 38, "conf_max_milli": 903, "conf_p50_milli": 771, "samples": 41, "sample_interval_ms": 1000},
    },
    "decisions": [
        {
            "id": "d1",
            "action": "accept",
            "note": "motion held steady; dog and person both visible",
            "reviewer": "operator",
            "src_viewed": True,
            "created_at": "2026-03-14T09:12:44Z",
        }
    ],
}

SOURCES = {
    "sources": [
        {
            "id": SOURCE_DEMO,
            "origin": "demo",
            "original_name": "barn-audit-2026-03-14.mp4",
            "state": "ready",
            "sha256": "5f2b9c4d1e7a8306b5c2d9e8f1a4b7c0d3e6f9a2b5c8d1e4f7a0b3c6d9e2f5a8",
            "size_bytes": 41_238_912,
            "duration_s": 96.4,
            "has_audio": True,
            "created_at": "2026-03-14T08:40:02Z",
            "failure_reason": None,
            "signal_summary": {
                "motion": {"p10": 3, "p25": 6, "p50": 11, "p75": 19, "p85": 27, "p90": 34, "p95": 48},
                "luma": {"p10": 210, "p25": 288, "p50": 402, "p75": 517, "p85": 604, "p90": 668, "p95": 742},
            },
            "window_count": 6,
            "pending_jobs": 0,
        },
        {
            "id": SOURCE_UPLOAD,
            "origin": "upload",
            "original_name": "yard-camera-04.mp4",
            "state": "analyzed",
            "sha256": "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90",
            "size_bytes": 188_743_680,
            "duration_s": 1204.0,
            "has_audio": False,
            "created_at": "2026-03-14T09:02:11Z",
            "failure_reason": None,
            "signal_summary": {"motion": {"p10": 1, "p50": 4, "p85": 12, "p95": 22}},
            "window_count": 44,
            "pending_jobs": 2,
        },
    ]
}

TIMELINE = {
    "candidates": [
        {"id": "c1", "pattern_slug": "stationary_animal_with_person", "pattern_version": 1, "score": 91,
         "sentence": SENTENCE_FLAGSHIP, "state": "open", "start_s": 12.0, "end_s": 53.0},
        {"id": "c2", "pattern_slug": "sustained_motion_low_light", "pattern_version": 1, "score": 84,
         "sentence": SENTENCE_P85, "state": "open", "start_s": 54.0, "end_s": 60.0},
        {"id": "c4", "pattern_slug": "sustained_motion_low_light", "pattern_version": 1, "score": 63,
         "sentence": "motion above p85", "state": "open", "start_s": 60.0, "end_s": 72.0},
        {"id": "c5", "pattern_slug": "stationary_animal_with_person", "pattern_version": 1, "score": 58,
         "sentence": "sheep presence", "state": "open", "start_s": 72.0, "end_s": 96.0},
    ],
    "events": [
        {"kind": "silence_gap", "t_start": 30.0, "t_end": 41.5, "value_milli": 11500},
        {"kind": "loudness_spike", "t_start": 62.0, "t_end": None, "value_milli": 8100},
    ],
    "windows": [
        {"id": f"w{i}", "idx": i, "start_s": i * 16.0, "end_s": (i + 1) * 16.0,
         "boundary_kind": "grid", "ml_state": "ok"}
        for i in range(6)
    ],
}


def series() -> dict:
    motion = []
    luma = []
    for index in range(96):
        inside = 12 <= index <= 53
        motion.append(6 + (index % 5) + (14 if inside else 0))
        luma.append(380 + (index * 7) % 160 - (40 if inside else 0))
    return {"motion": motion, "luma": luma, "persistence": [0] * 96}


PATTERNS = {
    "patterns": [
        {"slug": "stationary_animal_with_person", "version": 2, "title": "Animal stationary while a person is present",
         "sentence_template": "{animal} presence held {dur}s at motion energy <= {motion} (tolerance {tol}s) while a person was present {pdur}s of the same span",
         "rule": {"all": [{"class": "animal"}, {"max_motion_milli": 25}, {"min_duration_s": 20}]}, "weight": 60,
         "enabled": True, "builtin": True, "is_current": True, "updated_at": "2026-03-12 10:04:00+00",
         "updated_by": "operator"},
        {"slug": "stationary_animal_with_person", "version": 1, "title": "Animal stationary while a person is present",
         "sentence_template": "{animal} presence held {dur}s at motion energy <= {motion}", "rule": {"all": []},
         "weight": 60, "enabled": True, "builtin": True, "is_current": False},
        {"slug": "sustained_motion_low_light", "version": 1, "title": "Sustained motion in low light",
         "sentence_template": "motion above this recording's p85 for {dur}s while luma below its p10",
         "rule": {"all": [{"motion_percentile": 85}, {"luma_percentile": 10}]}, "weight": 40, "enabled": True,
         "builtin": True, "is_current": True},
        {"slug": "long_silence_animal_no_person", "version": 1, "title": "Long silence with animals present and no person detected",
         "sentence_template": "silence gap of {gap}s with {animal} present {dur}s and no person detected",
         "rule": {"all": [{"silence_gap_s": 10}]}, "weight": 30, "enabled": True, "builtin": True, "is_current": True},
    ]
}

STATS = {
    "stats": [
        {"slug": "stationary_animal_with_person", "version": 2, "fired": 6, "accepted": 4, "rejected": 1,
         "flagged": 1, "precision_estimate": 0.667},
        {"slug": "sustained_motion_low_light", "version": 1, "fired": 9, "accepted": 3, "rejected": 5,
         "flagged": 1, "precision_estimate": 0.333},
        {"slug": "long_silence_animal_no_person", "version": 1, "fired": 3, "accepted": 2, "rejected": 0,
         "flagged": 1, "precision_estimate": 1.0},
    ]
}

EXPORTS = {
    "exports": [
        {"id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", "reviewer": "operator", "top_n": 200,
         "states": ["accepted", "flagged"], "cut_precision": "keyframe", "blur_faces": False,
         "job_state": "succeeded", "stage": "bundle", "pct": 100, "error": None, "download_ready": True,
         "created_at": "2026-03-14T09:30:00Z"},
        {"id": "ffffffff-ffff-4fff-8fff-ffffffffffff", "reviewer": "operator", "top_n": 50,
         "states": ["accepted"], "cut_precision": "exact", "blur_faces": True,
         "job_state": "running", "stage": "bundle", "pct": 62, "error": None, "download_ready": False,
         "created_at": "2026-03-14T09:41:00Z"},
    ]
}

SOURCE_DETAIL = {
    **SOURCES["sources"][0],
    "storage_path": "/data/sources/5f2b9c4d/barn-audit-2026-03-14.mp4",
    "video_codec": "h264",
    "audio_codec": "aac",
    "fps": 25.0,
    "width": 1920,
    "height": 1080,
    "ml_failed_count": 0,
    "job": {"id": "job-1", "kind": "ingest", "state": "succeeded", "stage": "score", "pct": 100,
            "detail": "5 stages complete", "error": None},
}


def route(path: str) -> object | None:
    parsed = urlparse(path)
    route_path = parsed.path
    if route_path == "/api/v1/system/status":
        return STATUS
    if route_path == "/api/v1/sources":
        return SOURCES if SEEDED else {"sources": []}
    if route_path == f"/api/v1/sources/{SOURCE_DEMO}":
        return SOURCE_DETAIL
    if route_path == f"/api/v1/sources/{SOURCE_UPLOAD}":
        return {**SOURCES["sources"][1], "storage_path": "/data/sources/a1b2c3d4/yard-camera-04.mp4",
                "video_codec": "hevc", "audio_codec": None, "fps": 30.0, "width": 2560, "height": 1440,
                "ml_failed_count": 1,
                "job": {"id": "job-9", "kind": "ingest", "state": "running", "stage": "detect", "pct": 61,
                        "detail": "window 27 of 44", "error": None}}
    if route_path in (f"/api/v1/sources/{SOURCE_DEMO}/timeline", f"/api/v1/sources/{SOURCE_UPLOAD}/timeline"):
        return TIMELINE
    if route_path in (f"/api/v1/sources/{SOURCE_DEMO}/signals", f"/api/v1/sources/{SOURCE_UPLOAD}/signals"):
        return {"from": 0, "to": None, "series": series()}
    if route_path == "/api/v1/candidates":
        state = None
        for part in parsed.query.split("&"):
            if part.startswith("state="):
                state = part.split("=", 1)[1]
        entries = [ML_FAILED, *CANDIDATES]
        if state:
            entries = [entry for entry in entries if entry.get("state") == state or entry["kind"] == "ml_failed"]
        return {"total": len(entries), "entries": entries, "limit": 100, "offset": 0}
    if route_path.startswith("/api/v1/candidates/") and route_path.endswith("/decisions"):
        return {"decision_id": "d-new", "state": "accepted", "ledger_seq": 42, "reviewer": "operator",
                "src_viewed": True}
    if route_path.startswith("/api/v1/candidates/"):
        return DETAIL
    if route_path == "/api/v1/patterns":
        return PATTERNS
    if route_path == "/api/v1/patterns/stats":
        return STATS
    if route_path == "/api/v1/patterns/schema":
        return PATTERN_SCHEMA
    if route_path.startswith("/api/v1/jobs/"):
        return {"id": "job-2", "state": "running", "pct": 62, "stage": "rescore", "error": None}
    if route_path == "/api/v1/exports":
        return EXPORTS
    if route_path == "/api/v1/ledger/verify":
        return {"ok": True, "length": 148, "head_hash": "3f9a" + "0" * 60, "first_divergence": None}
    if route_path.startswith("/api/v1/"):
        return {}
    return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # noqa: D102 - quiet
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        payload = route(self.path)
        if payload is not None:
            self._send(200, json.dumps(payload).encode(), "application/json")
            return
        path = urlparse(self.path).path
        target = STATIC / path.lstrip("/")
        if path == "/" or not target.is_file():
            target = STATIC / "index.html"
        if not target.is_file():
            self._send(404, b"not found", "text/plain")
            return
        types = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
                 ".woff2": "font/woff2", ".woff": "font/woff", ".svg": "image/svg+xml"}
        self._send(200, target.read_bytes(), types.get(target.suffix, "application/octet-stream"))

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def do_POST(self) -> None:  # noqa: N802
        global SEEDED
        raw = self._read_body()
        if self.path.startswith("/api/v1/demo/seed"):
            SEEDED = True
            self._send(202, json.dumps({"source_id": SOURCE_DEMO, "job_id": "job-demo"}).encode(),
                       "application/json")
            return
        payload = route(self.path)
        if payload is None:
            self._send(404, b'{"detail":"not found"}', "application/json")
            return
        if self.path.endswith("/decisions"):
            try:
                action = json.loads(raw or b"{}").get("action", "accept")
            except Exception:  # noqa: BLE001
                action = "accept"
            state = {"accept": "accepted", "reject": "rejected", "flag": "flagged"}.get(action, "open")
            self._send(200, json.dumps({"decision_id": "d-new", "state": state, "ledger_seq": 42,
                                        "reviewer": "operator", "src_viewed": True}).encode(),
                       "application/json")
            return
        if self.path.startswith("/api/v1/exports"):
            self._send(202, json.dumps({"export_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
                                        "job_id": "job-3"}).encode(), "application/json")
            return
        if self.path.startswith("/api/v1/retention/purge"):
            self._send(202, json.dumps({"job_id": "job-4"}).encode(), "application/json")
            return
        self._send(202, json.dumps(payload).encode(), "application/json")

    def do_PUT(self) -> None:  # noqa: N802
        self._read_body()
        if self.path.startswith("/api/v1/patterns/"):
            self._send(202, json.dumps({"version": 3, "rescore_job_id": "job-2"}).encode(), "application/json")
            return
        self._send(404, b'{"detail":"not found"}', "application/json")


def format_console_problems(label: str, theme: str,
                            console: list[tuple[str, str]]) -> list[str]:
    """One problem line per console message, attributed to the page that
    produced it.  The page name is captured at emit time — a message fired on
    ``patterns`` is never filed under whatever page the loop visited last.
    """
    return [f"{label}/{theme}/{page_name}: console {error}" for page_name, error in console]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/longwatch-shots")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if not (STATIC / "index.html").is_file():
        print("the SPA is not built; run scripts/build_spa.sh first")
        return 2

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    from playwright.sync_api import sync_playwright

    base = f"http://127.0.0.1:{args.port}"
    problems: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for theme in ("dark", "light"):
            for width, height, label in ((1440, 1000, "desktop"), (375, 812, "mobile")):
                page = browser.new_page(viewport={"width": width, "height": height})
                # Console problems are attributed to the page that produced
                # them: the handler reads the current page name at emit time,
                # so a message fired on 'patterns' is never filed under the
                # last page the loop happened to visit.
                current_page = {"name": "ingest"}
                console: list[tuple[str, str]] = []
                page.on("console", lambda message: console.append(
                    (current_page["name"], f"{message.type}: {message.text}"))
                    if message.type in ("error", "warning") else None)
                page.add_init_script(f"window.localStorage.setItem('longwatch.theme', '{theme}');")
                for name in ("ingest", "triage", "patterns", "exports", "settings", "about"):
                    current_page["name"] = name
                    page.goto(f"{base}/#/{name}", wait_until="networkidle")
                    page.wait_for_timeout(400)
                    page.screenshot(path=str(out / f"{label}-{theme}-{name}.png"), full_page=True)
                # DOM contract checks on the workbench
                current_page["name"] = "triage"
                page.goto(f"{base}/#/triage", wait_until="networkidle")
                page.wait_for_selector('[data-testid="queue-table"]')
                page.wait_for_timeout(300)
                body = page.inner_text("body")
                for needle in ("this recording's p85", "stationary_animal_with_person", "detection incomplete"):
                    if needle not in body:
                        problems.append(f"{label}/{theme}: triage body is missing {needle!r}")
                if not page.locator('[data-testid="candidate-explanation"]').count():
                    problems.append(f"{label}/{theme}: no candidate-explanation hook")
                problems.extend(format_console_problems(label, theme, console))
                page.close()
        browser.close()
    server.shutdown()

    if problems:
        print("PROBLEMS")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"OK — screenshots written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
