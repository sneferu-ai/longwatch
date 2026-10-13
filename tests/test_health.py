"""HEALTH — /healthz is 200 only when the DB query and the model self-test pass.

503 legs: the database is unreachable, the model file is absent, the class map
is invalid.  200 leg: a passing DB query and a one-tensor model self-test.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from longwatch import app as app_mod
from longwatch.db import Database


def _client(db, cfg) -> TestClient:
    return TestClient(app_mod.create_app(cfg, db, start_workers=False, install_guard=False))


def test_healthz_is_200_with_a_passing_database_and_model(db, cfg):
    response = _client(db, cfg).get("/healthz")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["model"]["ok"] is True
    assert body["checks"]["model"]["output_shape"] == [1, 84, 16]


def test_healthz_is_503_when_the_database_is_unreachable(db, cfg):
    broken = Database("postgresql://operator:operator@127.0.0.1:1/longwatch", size=1)
    try:
        response = TestClient(app_mod.create_app(
            cfg, broken, start_workers=False, install_guard=False)).get("/healthz")
    finally:
        broken.close()
    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert body["checks"]["database"].startswith("error")
    assert body["checks"]["model"]["ok"] is True


def test_healthz_is_503_when_the_model_file_is_absent(db, cfg, tmp_path):
    cfg.model_path = str(tmp_path / "absent.onnx")
    response = _client(db, cfg).get("/healthz")
    assert response.status_code == 503
    assert "not found" in response.json()["checks"]["model"]["error"]


def test_healthz_is_503_when_the_class_map_is_invalid(db, cfg, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(["person", "bicycle"]), encoding="utf-8")
    cfg.class_map_path = str(bad)
    response = _client(db, cfg).get("/healthz")
    assert response.status_code == 503
    assert "class map" in response.json()["checks"]["model"]["error"]


def test_system_status_reports_the_whole_surface(db, cfg):
    body = _client(db, cfg).get("/api/v1/system/status").json()
    assert body["database"] == "ok"
    assert body["model"]["ok"] is True
    assert body["class_map"]["classes"] == 80
    assert set(body["workers"]) == {"media", "utility"}
    assert body["offline_guard"]["installed"] is False  # tests do not install the guard
    assert body["ml_failed_count"] == 0
    assert body["auth"]["token_required"] is False
    assert len(body["key"]["fingerprint"]) == 64


def test_system_status_exposes_the_deployment_export_defaults(db, cfg):
    """The workbench's export form initializes from these, so the §12 export
    knobs are the defaults on every surface — not only for API callers that
    omit the fields."""
    cfg.blur_faces = True
    cfg.export_top_n = 7
    cfg.export_states = ("accepted",)
    body = _client(db, cfg).get("/api/v1/system/status").json()
    assert body["export_defaults"] == {
        "blur_faces": True, "top_n": 7, "states": ["accepted"]}


def test_openapi_is_published_on_the_same_origin(db, cfg):
    response = _client(db, cfg).get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    for required in ("/api/v1/uploads", "/api/v1/candidates", "/api/v1/exports",
                     "/api/v1/patterns/{slug}", "/api/v1/ledger/verify",
                     "/api/v1/system/status", "/healthz"):
        assert required in paths, required


def test_every_response_carries_the_referrer_policy(db, cfg):
    client = _client(db, cfg)
    for path in ("/healthz", "/api/v1/sources", "/openapi.json", "/"):
        response = client.get(path)
        assert response.headers.get("referrer-policy") == "no-referrer", path


def test_optional_bearer_token_gates_api_routes_but_not_health(db, cfg):
    cfg.auth_token = "s3cret-token"
    client = _client(db, cfg)
    assert client.get("/healthz").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    unauthorized = client.get("/api/v1/sources")
    assert unauthorized.status_code == 401
    authorized = client.get("/api/v1/sources", headers={"Authorization": "Bearer s3cret-token"})
    assert authorized.status_code == 200
    # ?token= exists SOLELY for the two byte-range media routes (a <video>
    # element cannot send headers); every other /api/* route refuses it
    assert client.get("/api/v1/sources?token=s3cret-token").status_code == 401
    assert client.get("/api/v1/sources?token=wrong").status_code == 401


def test_token_query_form_works_only_on_the_two_media_routes(db, cfg, tmp_path):
    cfg.auth_token = "s3cret-token"
    media_file = tmp_path / "clip.mp4"
    media_file.write_bytes(b"not-really-video-but-served")
    with db.transaction() as cur:
        cur.execute(
            """
            INSERT INTO sources (origin, original_name, storage_path, sha256, size_bytes,
                                 probe, state)
            VALUES ('upload','c.mp4',%s,%s,27,'{}'::jsonb,'ready') RETURNING id
            """, (str(media_file), "e" * 64))
        source_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO windows (source_id, idx, start_s, end_s, boundary_kind)"
            " VALUES (%s, 0, 0, 10, 'grid') RETURNING id", (source_id,))
        window_id = cur.fetchone()["id"]
        cur.execute(
            """
            INSERT INTO clips (window_id, kind, requested_start_ms, actual_start_ms,
                               end_ms, path, sha256, size_bytes, cut_mode, ffmpeg_cmd)
            VALUES (%s, 'review_proxy', 0, 0, 10000, %s, %s, 27, NULL, 'ffmpeg')
            RETURNING id
            """, (window_id, str(media_file), "f" * 64))
        clip_id = cur.fetchone()["id"]
    client = _client(db, cfg)
    for path in (f"/api/v1/clips/{clip_id}/proxy", f"/api/v1/sources/{source_id}/original"):
        assert client.get(path).status_code == 401, path
        assert client.get(f"{path}?token=wrong").status_code == 401, path
        ok = client.get(f"{path}?token=s3cret-token")
        assert ok.status_code == 200, path
        assert ok.headers.get("referrer-policy") == "no-referrer", path
        by_header = client.get(path, headers={"Authorization": "Bearer s3cret-token"})
        assert by_header.status_code == 200, path
    # a non-media route refuses the query form even when the token is right
    assert client.get("/api/v1/jobs/whatever?token=s3cret-token").status_code == 401
