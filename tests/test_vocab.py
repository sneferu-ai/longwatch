"""VOCAB — the forbidden lexicon, three legs (§17 scenario 1).

(a) build-time: no forbidden term appears in built-in templates or UI strings;
(b) runtime: a PUT /patterns template and a decision note each containing a
    forbidden term are rejected with 422 naming the term;
(c) invariant: a crafted template whose rendering would contain a forbidden
    term is not persisted as a candidate (log line asserted).
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from longwatch import app as app_mod, patterns as patterns_mod, scoring, vocab
from longwatch.config import Config

REPO = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO / "longwatch" / "static"
UI_GLOBS = ("*.html", "*.js", "*.ts", "*.tsx")


def test_build_time_lexicon_absent_from_builtin_templates():
    for document in patterns_mod.BUILTIN_PATTERNS:
        assert vocab.is_clean(document["title"]), document["slug"]
        assert vocab.is_clean(document["sentence_template"]), document["slug"]


def test_build_time_lexicon_absent_from_ui_strings():
    checked = 0
    for pattern in UI_GLOBS:
        for path in STATIC_DIR.rglob(pattern):
            if path.is_file():
                checked += 1
                assert vocab.is_clean(path.read_text(encoding="utf-8", errors="replace")), \
                    f"forbidden term in {path}"
    assert checked or not STATIC_DIR.exists()


def test_runtime_pattern_write_is_rejected_with_named_term(db, cfg):
    client = _client(db, cfg)
    body = {
        "title": "A perfectly ordinary title",
        "sentence_template": "motion held {dur}s with no illegal conduct implied",
        "rule": {"type": "signal_threshold", "signal": "motion", "min": 0.5,
                 "min_duration_s": 10},
        "weight": 40,
    }
    response = client.put("/api/v1/patterns/stationary_animal_with_person", json=body)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "vocabulary_breach"
    assert detail["term"] == "illegal"


def test_runtime_decision_note_is_rejected_with_named_term(db, cfg):
    client = _client(db, cfg)
    candidate_id = _seed_candidate(db, cfg)
    response = client.post(f"/api/v1/candidates/{candidate_id}/decisions",
                           json={"action": "flag", "note": "looks like neglect to me",
                                 "src_viewed": False})
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["term"] == "neglect"
    assert "observables" in detail["guidance"]


def test_invariant_rendered_sentence_breach_is_not_persisted(db, cfg, caplog):
    """A crafted template whose rendering carries a term persists nothing.

    The write-time guard makes this unreachable (templates are guarded at write
    time and placeholder values are integers or fixed class names), so the
    invariant is exercised by planting a row that bypassed the API — exactly the
    defensive case the specification demands.
    """
    from longwatch import pipeline, rules as rules_mod
    from longwatch.ledger import psycopg_json

    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO patterns (slug, version, title, sentence_template, rule, weight,"
            " enabled, builtin, is_current, updated_by)"
            " VALUES ('crafted_breach', 1, 'Crafted breach', 'torture held {dur}s', %s, 30,"
            " true, false, true, 'test')",
            (psycopg_json({"type": "presence", "class": "dog", "min_duration_s": 1}),))
        cur.execute(
            "INSERT INTO sources (origin, original_name, storage_path, sha256, size_bytes,"
            " duration_s, probe, state) VALUES ('upload','clip.mp4','/tmp/breach.mp4',%s,10,"
            "5.0,'{}'::jsonb,'ready') RETURNING id", ("e" * 64,))
        source_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO windows (source_id, idx, start_s, end_s, boundary_kind, ml_state)"
            " VALUES (%s, 0, 0, 5, 'grid', 'ok') RETURNING id", (source_id,))
        window_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO presence_intervals (window_id, class_name, t_start, t_end, samples,"
            " conf_max_milli, synthetic) VALUES (%s, 'dog', 0, 5, 5, 900, false)",
            (window_id,))
    source = db.fetch_one("SELECT * FROM sources WHERE id = %s", (source_id,))

    with caplog.at_level(logging.ERROR, logger="longwatch.pipeline"):
        outcome = pipeline.score_source(db, source, "operator")
    assert outcome["renders_skipped"] == 1
    assert "VOCAB invariant violation" in caplog.text
    assert db.fetch_one("SELECT count(*) AS n FROM candidates")["n"] == 0
    # the rendered sentence itself carries the term, and the check the scorer
    # applies to it is the one that fires
    rendered = scoring.render_sentence("torture held {dur}s", {"duration_ms": 5000})
    assert rendered == "torture held 5s"
    with pytest.raises(vocab.VocabBreach) as excinfo:
        vocab.check_text(rendered)
    assert excinfo.value.term == "torture"


# ------------------------------------------------------------------ helpers
def _client(db, cfg: Config) -> TestClient:
    app = app_mod.create_app(cfg, db, start_workers=False, install_guard=False)
    return TestClient(app)


def _seed_candidate(db, cfg: Config) -> str:
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO sources (origin, original_name, storage_path, sha256, size_bytes,"
            " duration_s, probe, state) VALUES ('upload','clip.mp4','/tmp/clip.mp4',%s,10,5.0,"
            "'{}'::jsonb,'ready') RETURNING id", ("a" * 64,))
        source_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO windows (source_id, idx, start_s, end_s, boundary_kind)"
            " VALUES (%s, 0, 0, 5, 'grid') RETURNING id", (source_id,))
        window_id = cur.fetchone()["id"]
        cur.execute(
            """
            INSERT INTO candidates (window_id, pattern_slug, pattern_version, score, sentence,
                                    evidence)
            VALUES (%s, 'stationary_animal_with_person', 1, 80,
                    'dog presence held 100s at motion energy <= 0.150 (tolerance 2s) while a '
                    'person was present 70s of the same span', '{}'::jsonb)
            RETURNING id
            """, (window_id,))
        return str(cur.fetchone()["id"])
