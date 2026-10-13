"""Patterns (§8.4) — ``(slug, version)`` rows with an ``is_current`` flag.

A single ``slug``-keyed row cannot store version N after an edit to N+1 — the
history, the prior candidates' rules, and the per-version fired/decided counts
would all be fictions.  The composite key makes all three real.

Built-ins may be edited (creating a new version) but not deleted.
"""
from __future__ import annotations

from . import rules as rules_mod
from .ledger import psycopg_json

BUILTIN_PATTERNS: list[dict] = [
    {
        "slug": "stationary_animal_with_person",
        "version": 1,
        "title": "Animal stationary while a person is present",
        "sentence_template": (
            "{animal} presence held {dur}s at motion energy <= {motion} "
            "(tolerance {tol}s) while a person was present {pdur}s of the same span"
        ),
        "rule": {
            "type": "all",
            "of": [
                {"type": "presence", "class_group": "animal", "min_duration_s": 90,
                 "max_motion_energy": 0.15},
                {"type": "presence", "class": "person", "min_overlap_s": 30},
            ],
        },
        "weight": 80,
    },
    {
        "slug": "sustained_motion_low_light",
        "version": 1,
        "title": "Sustained motion in low light",
        "sentence_template": (
            "motion above this recording's p85 for {dur}s while luma below its p10"
        ),
        "rule": {
            "type": "all",
            "of": [
                {"type": "signal_percentile", "signal": "motion", "op": "gte",
                 "percentile": 85, "min_duration_s": 30},
                {"type": "signal_percentile", "signal": "luma", "op": "lte",
                 "percentile": 10, "min_duration_s": 30},
            ],
        },
        "weight": 60,
    },
    {
        "slug": "long_silence_animals_present",
        "version": 1,
        "title": "Long silence with animals present and no person detected",
        "sentence_template": (
            "silence gap of {gap}s with {animal} present {dur}s and no person detected"
        ),
        "rule": {
            "type": "all",
            "of": [
                {"type": "presence", "class_group": "animal", "min_duration_s": 60,
                 "anchor": True},
                {"type": "signal_threshold", "signal": "silence_gap", "min_duration_s": 5},
                {"type": "absence", "class": "person"},
            ],
        },
        "weight": 50,
    },
]


class PatternError(ValueError):
    """A pattern edit was refused — named reason."""


def seed_builtins(conn, actor: str = "migration") -> list[str]:
    """Seed the three built-ins idempotently on ``(slug, version)``."""
    inserted: list[str] = []
    with conn.cursor() as cur:
        for document in BUILTIN_PATTERNS:
            cur.execute(
                "SELECT 1 FROM patterns WHERE slug = %s AND version = %s",
                (document["slug"], document["version"]),
            )
            if cur.fetchone() is not None:
                continue
            cur.execute(
                """
                INSERT INTO patterns (slug, version, title, sentence_template, rule,
                                      weight, enabled, builtin, is_current, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s, true, true, true, %s)
                """,
                (document["slug"], document["version"], document["title"],
                 document["sentence_template"], psycopg_json(document["rule"]),
                 document["weight"], actor),
            )
            inserted.append(f"{document['slug']}@{document['version']}")
    conn.commit()
    return inserted


def list_patterns(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT slug, version, title, sentence_template, rule, weight, enabled,
                   builtin, is_current, updated_at, updated_by
              FROM patterns
             ORDER BY slug, version DESC
            """
        )
        return list(cur.fetchall())


def current_patterns(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT slug, version, title, sentence_template, rule, weight, enabled
              FROM patterns
             WHERE is_current AND enabled
             ORDER BY slug
            """
        )
        return list(cur.fetchall())


def get_pattern(conn, slug: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT slug, version, title, sentence_template, rule, weight, enabled, builtin,"
            " is_current, updated_at, updated_by FROM patterns WHERE slug = %s ORDER BY version DESC",
            (slug,),
        )
        return list(cur.fetchall())


def _decided_carried(cur, slug: str, version: int) -> dict:
    cur.execute(
        """
        SELECT c.state, count(*) AS n
          FROM candidates c
         WHERE c.pattern_slug = %s AND c.pattern_version = %s
         GROUP BY c.state
        """,
        (slug, version),
    )
    counts = {"accepted": 0, "flagged": 0, "rejected": 0, "open": 0}
    for row in cur.fetchall():
        counts[row["state"]] = int(row["n"])
    return {"accepted": counts["accepted"], "flagged": counts["flagged"],
            "rejected": counts["rejected"]}


def create_version(conn, slug: str, document: dict, actor: str,
                   ledger_append=None) -> dict:
    """Insert version N+1 and flip ``is_current`` in one short SQL transaction.

    The caller is responsible for the transaction boundary; this function
    performs no media or file work.  Returns ``{version, decided_carried}``.
    """
    rules_mod.validate_rule(document)
    with conn.cursor() as cur:
        cur.execute("SELECT max(version) AS v FROM patterns WHERE slug = %s", (slug,))
        row = cur.fetchone()
        next_version = int(row["v"] or 0) + 1
        cur.execute(
            "SELECT version FROM patterns WHERE slug = %s AND is_current", (slug,)
        )
        previous = cur.fetchone()
        previous_version = int(previous["version"]) if previous else None
        if previous_version is not None:
            carried = _decided_carried(cur, slug, previous_version)
            cur.execute(
                "UPDATE patterns SET is_current = false WHERE slug = %s AND is_current",
                (slug,),
            )
        else:
            carried = {"accepted": 0, "flagged": 0, "rejected": 0}
        cur.execute(
            """
            INSERT INTO patterns (slug, version, title, sentence_template, rule,
                                  weight, enabled, builtin, is_current, updated_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s,
                    COALESCE((SELECT bool_or(builtin) FROM patterns WHERE slug = %s), false),
                    true, %s)
            """,
            (slug, next_version, document["title"], document["sentence_template"],
             psycopg_json(document["rule"]), int(document["weight"]),
             bool(document.get("enabled", True)), slug, actor),
        )
        if ledger_append is not None:
            ledger_append(
                cur,
                "PATTERN_UPDATED",
                actor,
                {
                    "slug": slug,
                    "version": next_version,
                    "rule": document["rule"],
                    "weight": int(document["weight"]),
                    "updated_by": actor,
                    "decided_carried": carried,
                },
            )
    conn.commit()
    return {"version": next_version, "decided_carried": carried,
            "superseded_version": previous_version}


def stats(conn) -> list[dict]:
    """Fired / accepted / rejected / flagged counts per slug and version."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.slug, p.version, p.is_current, p.builtin,
                   COALESCE(c.fired, 0) AS fired,
                   COALESCE(c.accepted, 0) AS accepted,
                   COALESCE(c.rejected, 0) AS rejected,
                   COALESCE(c.flagged, 0) AS flagged,
                   COALESCE(c.open, 0) AS open
              FROM patterns p
              LEFT JOIN (
                   SELECT pattern_slug, pattern_version,
                          count(*) AS fired,
                          count(*) FILTER (WHERE state = 'accepted') AS accepted,
                          count(*) FILTER (WHERE state = 'rejected') AS rejected,
                          count(*) FILTER (WHERE state = 'flagged') AS flagged,
                          count(*) FILTER (WHERE state = 'open') AS open
                     FROM candidates
                    GROUP BY pattern_slug, pattern_version
              ) c ON c.pattern_slug = p.slug AND c.pattern_version = p.version
             ORDER BY p.slug, p.version DESC
            """
        )
        rows = list(cur.fetchall())
    for row in rows:
        decided = int(row["accepted"]) + int(row["rejected"]) + int(row["flagged"])
        row["decided"] = decided
        row["precision_estimate"] = (
            round(int(row["accepted"]) / decided, 3) if decided else None
        )
        row["precision_note"] = (
            "accepted / decided (reviewer agreement with the pattern) — not recall; "
            "recall against labeled negatives is v1"
        )
    return rows
