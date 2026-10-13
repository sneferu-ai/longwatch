"""EXPORT — the signed bundle, verified from the bundle alone (§17 scenario 10).

A freshly built bundle passes ``verify_bundle.py`` from the bundle alone: every
file digest matches the signed manifest's ``files`` map (including both verifier
scripts); ``manifest.json`` re-canonicalizes byte-equal to
``manifest.canonical``; the signature verifies; the chain walks clean; the CSV
row count equals the exported candidates with the
``decision_state``/``reviewer``/``src_viewed``/``synthetic`` columns; the
default scope is accepted+flagged; the documented openssl command exits 0; both
``cut_precision`` modes produce correctly labeled ``cut_mode`` and bounds.
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from longwatch import export_bundle, jobs as jobs_mod, media
from longwatch.ledger import psycopg_json

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import verify_bundle  # noqa: E402


def _seed_exportable(db, cfg, tmp_path, seconds: float = 6.0,
                     states: tuple[str, ...] = ("accepted", "flagged")) -> str:
    """A source + window + decided candidate pointing at a real small video."""
    from tests.conftest import make_video

    video = make_video(tmp_path / "evidence.mp4", seconds=seconds)
    document = media.probe(str(video))
    facts = media.probe_facts(document)
    digest = media.sha256_file(video)
    with db.transaction() as cur:
        cur.execute(
            """
            INSERT INTO sources (origin, original_name, storage_path, sha256, size_bytes,
                                 duration_s, fps, width, height, video_codec, has_audio,
                                 probe, state)
            VALUES ('upload','evidence.mp4',%s,%s,%s,%s,%s,%s,%s,%s,false,%s,'ready')
            RETURNING id
            """,
            (str(video), digest, video.stat().st_size, facts["duration_s"], facts["fps"],
             facts["width"], facts["height"], facts["video_codec"],
             psycopg_json(document)),
        )
        source_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO windows (source_id, idx, start_s, end_s, boundary_kind, ml_state)"
            " VALUES (%s, 0, 0, %s, 'grid', 'ok') RETURNING id",
            (source_id, min(5.0, seconds)))
        window_id = cur.fetchone()["id"]
        cur.execute(
            """
            INSERT INTO candidates (window_id, pattern_slug, pattern_version, score, sentence,
                                    evidence)
            VALUES (%s, 'stationary_animal_with_person', 1, 80, %s, %s) RETURNING id
            """,
            (window_id,
             "dog presence held 5s at motion energy <= 0.003 (tolerance 2s) while a person "
             "was present 4s of the same span",
             psycopg_json({"duration_ms": 5000, "motion_mean_milli": 3, "synthetic": True,
                           "class": "dog", "overlap_ms": 4000,
                           "required_duration_ms": 90_000, "required_overlap_ms": 30_000})),
        )
        candidate_id = cur.fetchone()["id"]
        cur.execute(
            """
            INSERT INTO decisions (candidate_id, action, note, reviewer, src_viewed)
            VALUES (%s, 'accept', 'observed a dog and a person', 'operator', true)
            """, (candidate_id,))
        cur.execute("UPDATE candidates SET state = 'accepted' WHERE id = %s", (candidate_id,))
        cur.execute(
            """
            INSERT INTO exports (reviewer, top_n, states, blur_faces, cut_precision)
            VALUES ('operator', 200, %s, false, 'keyframe') RETURNING id
            """, (psycopg_json(list(states)),))
        export_id = cur.fetchone()["id"]
    return str(export_id)


def _run_export(db, cfg, export_id: str) -> dict:
    job_id = jobs_mod.enqueue(db, "export", detail=f"export_id={export_id}")
    conn = db.dedicated()
    try:
        job = jobs_mod.claim(conn, ("export",))
        assert job is not None and job.id == job_id
        outcome = export_bundle.run_export(db, job, cfg)
        jobs_mod.finish(conn, job.id)
    finally:
        conn.close()
    return outcome


def test_export_bundle_verifies_from_the_bundle_alone(db, cfg, tmp_path, require_ffmpeg):
    export_id = _seed_exportable(db, cfg, tmp_path)
    outcome = _run_export(db, cfg, export_id)
    bundle = Path(outcome["bundle_path"])
    assert bundle.is_dir()

    expected = {"manifest.json", "manifest.canonical", "manifest.sig", "public.pem",
                "candidates.csv", "ledger.jsonl", "checksums.sha256", "verify_bundle.py",
                "ed25519_verifier.py", "VERIFY.md", "README.txt"}
    present = {path.name for path in bundle.iterdir()}
    assert expected <= present, expected - present
    assert (bundle / "clips").is_dir()

    report = verify_bundle.verify_bundle(str(bundle))
    report.print()
    assert report.exit_code == 0, [check for check in report.checks if check[1] == "FAIL"]

    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["ledger_head_seq"] == manifest["ledger_rows"]
    assert manifest["head_hash"]
    assert manifest["public_key_sha256"]
    for script in ("verify_bundle.py", "ed25519_verifier.py"):
        assert manifest["files"][script] == hashlib.sha256(
            (bundle / script).read_bytes()).hexdigest()
    assert manifest["integrity_statement"].startswith("The SHA-256 digests in this bundle")
    assert "server-local clock" in manifest["time_basis"]
    assert manifest["review_medium"].startswith("lossy working copy")
    assert manifest["candidates"][0]["synthetic"] is True
    assert manifest["candidates"][0]["src_viewed"] is True
    assert manifest["clips"][0]["cut_mode"] == "keyframe_snapped"


def test_csv_columns_and_default_scope(db, cfg, tmp_path, require_ffmpeg):
    export_id = _seed_exportable(db, cfg, tmp_path)
    outcome = _run_export(db, cfg, export_id)
    bundle = Path(outcome["bundle_path"])
    with open(bundle / "candidates.csv", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows, "the accepted candidate must ship"
    assert set(rows[0]) >= {"rank", "candidate_id", "score", "pattern_slug", "decision_state",
                            "reviewer", "src_viewed", "synthetic", "clip"}
    assert rows[0]["decision_state"] == "accepted"
    assert rows[0]["reviewer"] == "operator"
    assert rows[0]["src_viewed"] == "true"
    assert rows[0]["synthetic"] == "true"
    assert rows[0]["clip"].startswith("clips/")
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["states"] == ["accepted", "flagged"]
    assert len(rows) == len(manifest["candidates"]) == 1


def test_openssl_sigfile_command_exits_zero(db, cfg, tmp_path, require_ffmpeg):
    if not shutil.which("openssl"):
        pytest.skip("openssl is not available in this environment")
    export_id = _seed_exportable(db, cfg, tmp_path)
    outcome = _run_export(db, cfg, export_id)
    bundle = Path(outcome["bundle_path"])
    result = subprocess.run(
        ["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", "public.pem", "-rawin",
         "-in", "manifest.canonical", "-sigfile", "manifest.sig"],
        cwd=str(bundle), capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Verified Successfully" in (result.stdout + result.stderr)


def test_the_signed_manifest_states_the_signature_encoding(db, cfg, tmp_path, require_ffmpeg):
    """BUG-006: one documented encoding, stated inside the signed document —
    a third party implementing a verifier never has to guess raw vs base64."""
    export_id = _seed_exportable(db, cfg, tmp_path)
    outcome = _run_export(db, cfg, export_id)
    bundle = Path(outcome["bundle_path"])
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    # §9 step 7: no signature field exists in the signed object — the signature
    # lives in manifest.sig; the manifest only *describes* it.
    assert "signature" not in manifest
    assert manifest["detached_signature"] == {
        "algorithm": "ed25519", "encoding": "raw", "bytes": 64,
        "file": "manifest.sig", "signed_document": "manifest.canonical",
    }
    assert len((bundle / "manifest.sig").read_bytes()) == 64
    verify_md = (bundle / "VERIFY.md").read_text(encoding="utf-8")
    assert "raw 64-byte signature" in verify_md and "base64 -w0 manifest.sig" in verify_md
    assert "superseded" in verify_md, "the sketch annotation must be named, not silently contradicted"
    readme = (bundle / "README.txt").read_text(encoding="utf-8")
    assert "raw 64-byte Ed25519 signature" in readme


def test_a_tampered_clip_is_detected(db, cfg, tmp_path, require_ffmpeg):
    export_id = _seed_exportable(db, cfg, tmp_path)
    outcome = _run_export(db, cfg, export_id)
    bundle = Path(outcome["bundle_path"])
    clip = next((bundle / "clips").iterdir())
    payload = bytearray(clip.read_bytes())
    payload[-1] ^= 0xFF
    clip.write_bytes(bytes(payload))
    report = verify_bundle.verify_bundle(str(bundle))
    assert report.exit_code == 1
    assert any("digest matches the signed manifest" in name and status == "FAIL"
               for name, status, _ in report.checks)


def test_a_tampered_ledger_row_is_detected(db, cfg, tmp_path, require_ffmpeg):
    export_id = _seed_exportable(db, cfg, tmp_path)
    outcome = _run_export(db, cfg, export_id)
    bundle = Path(outcome["bundle_path"])
    lines = (bundle / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[-1])
    row["payload"]["score"] = 99
    lines[-1] = json.dumps(row, sort_keys=True, separators=(",", ":"))
    (bundle / "ledger.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = verify_bundle.verify_bundle(str(bundle))
    assert report.exit_code == 1


def test_both_cut_precision_modes_are_labeled(db, cfg, tmp_path, require_ffmpeg):
    export_id = _seed_exportable(db, cfg, tmp_path)
    outcome = _run_export(db, cfg, export_id)
    keyframe = json.loads((Path(outcome["bundle_path"]) / "manifest.json").read_text())
    assert keyframe["clips"][0]["cut_mode"] == "keyframe_snapped"
    assert keyframe["clips"][0]["requested_start_ms"] == 0
    assert keyframe["clips"][0]["actual_start_ms"] == 0
    assert "lead-in" in keyframe["clips"][0]["alignment_note"]

    with db.transaction() as cur:
        cur.execute("UPDATE exports SET cut_precision = 'exact' WHERE id = %s", (export_id,))
    outcome = _run_export(db, cfg, export_id)
    exact = json.loads((Path(outcome["bundle_path"]) / "manifest.json").read_text())
    assert exact["cut_precision"] == "exact"
    assert exact["clips"][0]["cut_mode"] == "reencoded"
    assert exact["clips"][0]["requested_start_ms"] == exact["clips"][0]["actual_start_ms"]
    assert "re-encoded" in exact["clips"][0]["alignment_note"]


def test_source_digest_mismatch_hard_fails_the_export(db, cfg, tmp_path, require_ffmpeg):
    export_id = _seed_exportable(db, cfg, tmp_path)
    video = db.fetch_one("SELECT storage_path FROM sources")["storage_path"]
    Path(video).write_bytes(b"not the same bytes")
    with pytest.raises(export_bundle.ExportError) as excinfo:
        _run_export(db, cfg, export_id)
    assert "digest mismatch" in str(excinfo.value)
    events = db.fetch_all(
        "SELECT payload FROM ledger_events WHERE event_type = 'SOURCE_DIGEST_MISMATCH'")
    assert len(events) == 1
    payload = events[0]["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["actual"] != payload["expected_sha256"]


def test_face_pixelation_fails_closed_without_a_face_model(db, cfg, tmp_path, require_ffmpeg):
    """Requested pixelation without a usable face model refuses; it never skips."""
    export_id = _seed_exportable(db, cfg, tmp_path)
    with db.transaction() as cur:
        cur.execute("UPDATE exports SET blur_faces = true WHERE id = %s", (export_id,))
    with pytest.raises(export_bundle.ExportError) as excinfo:
        _run_export(db, cfg, export_id)
    assert "no usable face model" in str(excinfo.value)
    assert db.fetch_one("SELECT bundle_path FROM exports WHERE id = %s", (export_id,))["bundle_path"] is None


def test_face_pixelation_runs_the_model_and_records_what_it_did(db, cfg, tmp_path, require_ffmpeg):
    """The positive path (§9): the face model runs, only its regions are
    mosaicked, and the custody record carries the operation, the block size,
    the counts and the model identity — not a claim the transform never made.
    The signed manifest carries the same face-model identity as the
    TRANSFORM_APPLIED ledger row."""
    from tests.test_faces import FACE_FIXTURE, FIXTURE_BOX, _block_deviation, _frame_at

    export_id = _seed_exportable(db, cfg, tmp_path)
    cfg.face_model_path = str(FACE_FIXTURE)
    with db.transaction() as cur:
        cur.execute("UPDATE exports SET blur_faces = true WHERE id = %s", (export_id,))
    outcome = _run_export(db, cfg, export_id)
    bundle = Path(outcome["bundle_path"])
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    clip = manifest["clips"][0]
    assert clip["kind"] == "blurred_excerpt"
    assert clip["clip_name"].endswith("_blurred.mp4")
    assert clip["transform"]["operation"] == "face_pixelate"
    assert clip["transform"]["block"] == 24
    assert clip["transform"]["regions_pixelated"] >= 1
    assert clip["transform"]["frames_processed"] >= 1
    assert clip["transform"]["max_faces_per_frame"] == 1
    # §9: the manifest clip carries the face model digest when a transform ran
    assert clip["face_model"]["basename"] == FACE_FIXTURE.name
    assert clip["face_model"]["sha256"] == media.sha256_file(FACE_FIXTURE)

    rows = db.fetch_all(
        "SELECT payload FROM ledger_events WHERE event_type = 'TRANSFORM_APPLIED'")
    assert len(rows) == 1
    payload = rows[0]["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["face_model"]["basename"] == FACE_FIXTURE.name
    assert payload["face_model"]["sha256"] == media.sha256_file(FACE_FIXTURE)
    assert payload["transform"] == clip["transform"]
    assert payload["face_model"] == clip["face_model"]

    # §9/§15: README.txt's digest is attested by the manifest's files map, so
    # it must not claim the blurred clips are bit-identical to the source's
    readme = (bundle / "README.txt").read_text(encoding="utf-8")
    assert "blur_faces: true" in readme
    assert "NOT bit-identical" in readme
    assert "the encoded frames are bit-identical" not in readme

    # the shipped excerpt really is mosaicked where the model found the face
    source_path = db.fetch_one("SELECT storage_path FROM sources")["storage_path"]
    shipped = bundle / clip["clip_name"]
    before = _block_deviation(_frame_at(Path(source_path), 1.0), FIXTURE_BOX)
    after = _block_deviation(_frame_at(shipped, 1.0), FIXTURE_BOX)
    assert after < before / 4.0, (before, after)

    # the added manifest key rides the signed bytes: the bundle-alone verifier
    # still passes end to end
    report = verify_bundle.verify_bundle(str(bundle))
    assert report.exit_code == 0, [check for check in report.checks if check[1] == "FAIL"]


def test_manifest_clips_omit_face_model_when_no_transform_ran(db, cfg, tmp_path,
                                                              require_ffmpeg):
    """§9 "face model digest when applicable": a plain stream-copy bundle's
    clips carry no face-model key, and its README keeps the true keyframe
    claim (blur_faces: false)."""
    export_id = _seed_exportable(db, cfg, tmp_path)
    outcome = _run_export(db, cfg, export_id)
    bundle = Path(outcome["bundle_path"])
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["clips"][0]["transform"] is None
    assert "face_model" not in manifest["clips"][0]
    readme = (bundle / "README.txt").read_text(encoding="utf-8")
    assert "blur_faces: false" in readme
    assert "the encoded frames are bit-identical" in readme
    assert "NOT bit-identical" not in readme


def test_the_config_toggle_is_the_export_default_and_the_request_wins(db, cfg):
    """§9: LONGWATCH_BLUR_FACES is the deployment's default for the option;
    an explicit request value overrides it."""
    from fastapi.testclient import TestClient

    from longwatch import app as app_mod

    client = TestClient(app_mod.create_app(cfg, db, start_workers=False,
                                           install_guard=False))

    def created_blur(payload: dict) -> bool:
        response = client.post("/api/v1/exports", json=payload)
        assert response.status_code == 200, response.text
        row = db.fetch_one("SELECT blur_faces FROM exports WHERE id = %s",
                           (response.json()["export_id"],))
        return bool(row["blur_faces"])

    cfg.blur_faces = False
    assert created_blur({"top_n": 5}) is False          # default off
    assert created_blur({"top_n": 5, "blur_faces": True}) is True
    cfg.blur_faces = True
    assert created_blur({"top_n": 5}) is True           # the deployment default
    assert created_blur({"top_n": 5, "blur_faces": False}) is False


def test_bundle_never_carries_an_unlisted_file(db, cfg, tmp_path, require_ffmpeg):
    """Nothing rides along unlisted — the un-pixelated excerpt must not ship."""
    export_id = _seed_exportable(db, cfg, tmp_path)
    outcome = _run_export(db, cfg, export_id)
    bundle = Path(outcome["bundle_path"])
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    on_disk = {
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*") if path.is_file()
    }
    manifest_artifacts = {"manifest.json", "manifest.canonical", "manifest.sig",
                          "public.pem", "checksums.sha256"}
    assert on_disk - set(manifest["files"]) - manifest_artifacts == set()
    # and the scratch directory the cut was made in is cleaned up
    scratch = Path(cfg.data_dir) / "tmp" / f"export-{export_id[:8]}"
    assert not scratch.exists()


def test_an_empty_export_still_verifies(db, cfg, tmp_path, require_ffmpeg):
    """A states filter that matches nothing is a legitimate, verifiable bundle."""
    from tests.conftest import make_video

    video = make_video(tmp_path / "quiet.mp4", seconds=4.0)
    digest = media.sha256_file(video)
    with db.transaction() as cur:
        cur.execute(
            """
            INSERT INTO sources (origin, original_name, storage_path, sha256, size_bytes,
                                 duration_s, probe, state)
            VALUES ('upload','quiet.mp4',%s,%s,%s,4,'{}'::jsonb,'ready') RETURNING id
            """, (str(video), digest, video.stat().st_size))
        cur.execute(
            """
            INSERT INTO exports (reviewer, top_n, states, blur_faces, cut_precision)
            VALUES ('operator', 200, %s, false, 'keyframe') RETURNING id
            """,
            (psycopg_json(["accepted", "flagged"]),))
        export_id = cur.fetchone()["id"]
    outcome = _run_export(db, cfg, str(export_id))
    bundle = Path(outcome["bundle_path"])
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["candidates"] == [] and manifest["clips"] == []
    with open(bundle / "candidates.csv", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert {"decision_state", "reviewer", "src_viewed", "synthetic"} <= set(
            reader.fieldnames or [])
        assert list(reader) == []
    report = verify_bundle.verify_bundle(str(bundle))
    report.print()
    assert report.exit_code == 0, [check for check in report.checks if check[1] == "FAIL"]


def test_a_reclaimed_export_job_still_finds_its_row(db, cfg, tmp_path, require_ffmpeg):
    """The lease reclaimer appends ' [lease reclaimed]' to the job detail; the
    export must parse its id past the suffix, never no-op into ExportError."""
    export_id = _seed_exportable(db, cfg, tmp_path)
    job_id = jobs_mod.enqueue(db, "export", detail=f"export_id={export_id}")
    db.execute("UPDATE jobs SET detail = detail || ' [lease reclaimed]' WHERE id = %s",
               (job_id,))
    job_row = jobs_mod.get_job(db, job_id)
    job = jobs_mod.Job.from_row(job_row)
    outcome = export_bundle.run_export(db, job, cfg)
    assert Path(outcome["bundle_path"]).is_dir()
    report = verify_bundle.verify_bundle(outcome["bundle_path"])
    assert report.exit_code == 0


def test_export_is_decision_scoped_not_version_scoped(db, cfg, tmp_path, require_ffmpeg):
    """Editing a rule never removes accepted evidence from a bundle."""
    export_id = _seed_exportable(db, cfg, tmp_path)
    from longwatch import patterns as patterns_mod
    from longwatch.ledger import append as ledger_append

    with db.connect() as conn:
        patterns_mod.create_version(conn, "stationary_animal_with_person", {
            "slug": "stationary_animal_with_person",
            "title": "Animal stationary while a person is present",
            "sentence_template": "motion held {dur}s",
            "rule": {"type": "presence", "class": "dog", "min_duration_s": 10},
            "weight": 40,
        }, "operator", ledger_append=ledger_append)
    assert db.fetch_one(
        "SELECT count(*) AS n FROM candidates WHERE pattern_version = 1 AND state = 'accepted'"
    )["n"] == 1
    outcome = _run_export(db, cfg, export_id)
    manifest = json.loads((Path(outcome["bundle_path"]) / "manifest.json").read_text())
    assert manifest["candidates"][0]["pattern_version"] == 1
    assert manifest["candidates"][0]["decision_state"] == "accepted"


# --------------------------------------------------------------------------
# Verifier behaviour on a damaged bundle — no database or ffmpeg needed.
# The verifier's whole job is to survive a hostile or truncated bundle: every
# malformed input is a named FAIL, and the "signature not checked" verdict
# (exit 2) can never be overwritten by a later failing check.

def _canonical(document: dict) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _minimal_bundle(tmp_path: Path) -> Path:
    """A hand-built bundle whose pre-signature checks pass, so a test can break
    exactly one thing and watch the verifier's reaction."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    payload = {"ledger": "longwatch", "v": 1}
    recorded_at_ms = 1_760_000_000_000
    preimage = ("longwatch-ledger-v1|1|" + "0" * 64 +
                f"|LEDGER_GENESIS|operator|{recorded_at_ms}|" +
                json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    row_hash = hashlib.sha256(preimage.encode("utf-8")).hexdigest()
    (bundle / "ledger.jsonl").write_text(json.dumps({
        "seq": 1, "prev_hash": "0" * 64, "row_hash": row_hash,
        "event_type": "LEDGER_GENESIS", "actor": "operator", "payload": payload,
        "recorded_at_ms": recorded_at_ms, "recorded_at_iso": "2026-01-01T00:00:00+00:00",
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")
    (bundle / "candidates.csv").write_text(
        "rank,candidate_id,score,pattern_slug,pattern_version,sentence,decision_state,"
        "reviewer,src_viewed,synthetic,source_id,window_id,start_ms,end_ms,clip\n",
        encoding="utf-8")
    (bundle / "VERIFY.md").write_text("verify me\n", encoding="utf-8")
    (bundle / "README.txt").write_text("read me\n", encoding="utf-8")
    shutil.copy2(REPO / "verify_bundle.py", bundle / "verify_bundle.py")
    shutil.copy2(REPO / "ed25519_verifier.py", bundle / "ed25519_verifier.py")
    files = {name: hashlib.sha256((bundle / name).read_bytes()).hexdigest()
             for name in ("verify_bundle.py", "ed25519_verifier.py", "candidates.csv",
                          "ledger.jsonl", "VERIFY.md", "README.txt")}
    manifest = {
        "created_at_ms": recorded_at_ms,
        "head_hash": row_hash,
        "ledger_head_seq": 1,
        "ledger_rows": 1,
        "candidates": [],
        "public_key_sha256": "0" * 64,
        "files": files,
    }
    canonical_bytes = _canonical(manifest)
    (bundle / "manifest.canonical").write_bytes(canonical_bytes)
    (bundle / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    (bundle / "manifest.sig").write_bytes(b"\x00" * 64)
    (bundle / "public.pem").write_text("-----BEGIN PUBLIC KEY-----\n-----END PUBLIC KEY-----\n",
                                       encoding="utf-8")
    (bundle / "checksums.sha256").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(files.items())),
        encoding="utf-8")
    return bundle


def test_a_valid_minimal_bundle_passes_its_pre_signature_checks(tmp_path):
    bundle = _minimal_bundle(tmp_path)
    report = verify_bundle.verify_bundle(str(bundle))
    assert any("digest matches the signed manifest" in name and status == "PASS"
               for name, status, _ in report.checks)
    assert any("chain walks clean" in name and status == "PASS" for name, status, _ in report.checks)
    # an empty export proves its columns, not a per-candidate agreement
    assert any("carries decision_state/reviewer/src_viewed/synthetic" in name and status == "PASS"
               for name, status, _ in report.checks)


def test_exit_2_survives_a_later_failing_check(tmp_path, monkeypatch):
    """BUG-002: 'the signature could not be checked' (exit 2) is a distinct
    verdict — a later failed check must not relabel it as 'a check failed'."""
    bundle = _minimal_bundle(tmp_path)
    (bundle / "checksums.sha256").write_text(f"{'0' * 64}  ledger.jsonl\n", encoding="utf-8")
    monkeypatch.setattr(verify_bundle, "load_verifier", lambda: None)
    report = verify_bundle.verify_bundle(str(bundle))
    assert report.signature_checked is False
    assert any("SIGNATURE NOT CHECKED" in detail for _n, _s, detail in report.checks)
    assert any("checksums.sha256 recomputes" in name and status == "FAIL"
               for name, status, _ in report.checks), "the later check must still run and FAIL"
    assert report.exit_code == 2, "exit 2 must outrank the later FAIL"


def test_exit_2_is_reported_by_the_cli(tmp_path, monkeypatch, capsys):
    bundle = _minimal_bundle(tmp_path)
    monkeypatch.setattr(verify_bundle, "load_verifier", lambda: None)
    assert verify_bundle.main(["verify_bundle.py", str(bundle)]) == 2
    assert "NOT VERIFIED" in capsys.readouterr().out


def test_a_truncated_candidates_csv_is_a_named_fail_not_a_traceback(tmp_path):
    """BUG-003: a malformed CSV (the tampering case the verifier exists for)
    yields a named FAIL and exit 1."""
    bundle = _minimal_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    manifest["candidates"] = [{"candidate_id": "c1", "score": 80}]
    canonical_bytes = _canonical(manifest)
    (bundle / "manifest.canonical").write_bytes(canonical_bytes)
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True),
                                          encoding="utf-8")
    # a truncated row: the score column is present in the header but empty
    (bundle / "candidates.csv").write_text(
        "rank,candidate_id,score,pattern_slug,pattern_version,sentence,decision_state,"
        "reviewer,src_viewed,synthetic,source_id,window_id,start_ms,end_ms,clip\n"
        "1,c1,,stationary_animal_with_person,1,text,accepted,operator,true,false,s,w,0,1,\n",
        encoding="utf-8")
    report = verify_bundle.verify_bundle(str(bundle))
    assert report.exit_code == 1
    assert any("agrees with the manifest per candidate" in name and status == "FAIL"
               for name, status, _ in report.checks)


def test_a_malformed_ledger_row_is_a_named_fail_not_a_traceback(tmp_path):
    bundle = _minimal_bundle(tmp_path)
    lines = (bundle / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    (bundle / "ledger.jsonl").write_text(lines[0][: len(lines[0]) // 2] + "\n", encoding="utf-8")
    report = verify_bundle.verify_bundle(str(bundle))
    assert report.exit_code == 1
    assert any("ledger.jsonl rows parse" in name and status == "FAIL"
               for name, status, _ in report.checks)


def test_a_malformed_manifest_is_a_named_fail_not_a_traceback(tmp_path):
    bundle = _minimal_bundle(tmp_path)
    (bundle / "manifest.json").write_text("{not json", encoding="utf-8")
    report = verify_bundle.verify_bundle(str(bundle))
    assert report.exit_code == 1
    assert any("manifest.json parses as JSON" in name and status == "FAIL"
               for name, status, _ in report.checks)
