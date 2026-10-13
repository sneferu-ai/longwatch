# LongWatch — API reference

LongWatch serves one HTTP API from the same origin as its single-page application.
Every route below exists in `longwatch/api.py`; nothing here is aspirational.

- **Base URL (local development):** `http://localhost:8000` (the server binds `0.0.0.0:8000` by default; `PORT` changes the port).
- **Content type:** JSON (`application/json`) except the two byte-range media routes (`video/mp4`) and the upload route (`multipart/form-data`).
- **API prefix:** `/api/v1`. `/healthz`, `/openapi.json`, `/docs` and `/redoc` sit outside the prefix.
- **Interactive docs:** FastAPI's own `/docs` (Swagger UI) and `/redoc` render the generated OpenAPI document at `/openapi.json`. The generated document is the contract; this file explains the semantics.

---

## Authentication

There is no login system and no user accounts. One reviewer identity comes from
configuration (`LONGWATCH_REVIEWER_NAME`, default `operator`) and is stamped on
every human action. That is attribution, not access control.

The deployment optionally sets `LONGWATCH_AUTH_TOKEN`. The token is an accident
guard, not access control — with it unset, anyone who can reach the port is the
operator.

| Token state | Behaviour |
|---|---|
| Unset | Every route is open. The trust-boundary text is published by `GET /api/v1/system/status` (`auth.trust_boundary`, `disclosures.trust_boundary`) and rendered on the Settings page. No startup warning is logged. |
| Set | Every `/api/*` route requires `Authorization: Bearer <token>`, compared with `secrets.compare_digest` (constant time). A missing or wrong token returns `401` with `{"detail": "invalid or missing bearer token"}`. |

**The `?token=` query form is accepted only on the two byte-range media routes**
— `GET /api/v1/clips/{clip_id}/proxy` and `GET /api/v1/sources/{source_id}/original`
— because `<video>` elements cannot send headers. Every other route refuses a
query token. Every response, including the SPA document and both media routes,
carries `Referrer-Policy: no-referrer`, so a URL-borne token cannot leak through
a `Referer` header.

`/healthz`, `/openapi.json`, `/docs` and `/redoc` stay open either way.

Example:

```sh
curl -H "Authorization: Bearer $LONGWATCH_AUTH_TOKEN" \
     http://localhost:8000/api/v1/sources
```

The same call in Python (the backend's language; stdlib only — a runnable
monitoring-shaped version of this pattern is
[examples/05_verify_ledger.py](examples/05_verify_ledger.py)):

```python
import json
import os
import urllib.request

BASE = os.environ.get("BASE_URL", "http://localhost:8000")
TOKEN = os.environ.get("LONGWATCH_AUTH_TOKEN", "")


def get(path: str) -> dict:
    request = urllib.request.Request(BASE + path)
    if TOKEN:
        request.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


queue = get("/api/v1/candidates?state=open&limit=5")
for entry in queue["entries"]:
    if entry["kind"] == "candidate":
        print(entry["score"], entry["pattern_slug"], entry["sentence"])
    else:
        print(entry["label"], entry["source_name"], entry["start_s"], entry["end_s"])
```

---

## Error envelope

Errors are FastAPI `HTTPException` responses. The `detail` field is either a
string or a structured object. Structured errors always name their condition:

```json
{"detail": {"error": "vocabulary_breach", "term": "cruel",
            "guidance": "Describe measured observables (durations, class presences, motion magnitudes, loudness deltas) rather than conclusions."}}
```

| Status | Meaning in this API |
|---|---|
| `200` | Success; also used for idempotent no-ops (duplicate upload, existing demo source). |
| `202` | Accepted: a job was queued (`{"job_id": "…"}`) or a version was created. Poll `GET /api/v1/jobs/{job_id}`. |
| `400` | A configured feature is unavailable (`inbox_disabled`, `inbox_missing`). |
| `401` | Bearer token required and missing/wrong. |
| `404` | The named object does not exist, or an export bundle is not ready. |
| `409` | The request conflicts with current state (`window_not_failed`, `already_acknowledged`). |
| `413` | Upload exceeded `LONGWATCH_UPLOAD_LIMIT_MB` (mid-stream; the partial file is deleted). |
| `416` | The `Range` header is malformed or unsatisfiable (media routes). |
| `422` | Validation failed: `bad_action`, `bad_state`, `bad_cut_precision`, `vocabulary_breach`, `template_error`, `rule_error`. |
| `500` | `demo_seed_failed` with the underlying reason. |
| `503` | `/healthz` when the database or the detector fails its self-test; the SPA returns `spa_not_built` if the frontend was never built. |

---

## Common objects

### Source (`GET /api/v1/sources`)

```json
{
  "id": "a6723e47-ceb9-46b4-a87a-5f4d3c8d7fc2",
  "origin": "demo",
  "original_name": "demo.mp4",
  "state": "ready",
  "sha256": "4ff5bbba…f97cf3",
  "size_bytes": 697340,
  "duration_s": 160.0,
  "has_audio": false,
  "created_at": "2026-10-01T13:05:48.383329+00:00",
  "failure_reason": null,
  "signal_summary": {"luma": {"p10": 71, "…": "…"}, "motion": {"…": "…"}, "persistence": {"…": "…"}},
  "window_count": 2,
  "pending_jobs": 0
}
```

`state` is one of `registered`, `unreadable`, `missing`, `windowed`, `proxied`,
`analyzed`, `scored`, `ready`, `failed`. `origin` is `upload`, `inbox`, or `demo`.
All `signal_summary` values are integers in milli-units (thousandths; percentiles
in whole percent).

### Job (`GET /api/v1/jobs/{job_id}`)

```json
{
  "job_id": "e7797a19-8934-4d6b-92d4-870aed0f7fdc",
  "kind": "ingest",
  "state": "running",
  "stage": "signals",
  "pct": 50,
  "attempts": 0,
  "detail": "upload",
  "error": null,
  "created_at": "2026-10-01T13:05:48.394244+00:00",
  "finished_at": null
}
```

`kind` ∈ `ingest`, `export`, `purge`, `rescore`. `state` ∈ `queued`, `running`,
`succeeded`, `failed`. `stage` ∈ `probe`, `windows`, `proxies`, `signals`,
`detect`, `score` (ingest); `bundle` (export); `purge`; `rescore`.

### Queue entry (`GET /api/v1/candidates`)

Two shapes share one list. Detection-failed windows are pinned **above** ranked
candidates on every page:

```json
{"kind": "ml_failed", "window_id": "…", "source_id": "…", "source_name": "…",
 "start_s": 100.0, "end_s": 160.0, "reason": "…",
 "label": "detection incomplete — manual review required"}
```

```json
{"kind": "candidate", "candidate_id": "6c699be2-…", "window_id": "e75e4248-…",
 "source_id": "a6723e47-…", "source_name": "demo.mp4", "clip_id": "94c965da-…",
 "start_s": 0.0, "end_s": 100.0, "pattern_slug": "stationary_animal_with_person",
 "pattern_version": 1, "score": 86,
 "sentence": "dog presence held 100s at motion energy <= 0.009 (tolerance 4s) while a person was present 70s of the same span",
 "state": "open", "synthetic": true, "demo": true}
```

---

## Ingest routes

### `POST /api/v1/uploads`

Streamed browser upload. The file is hashed while it streams; nothing is buffered
whole. Duplicate digests are detected before a second copy is stored.

- **Body:** `multipart/form-data`, field name `file`.
- **Success `202`:** `{"source_id": "…", "job_id": "…"}` — ingest queued.
- **Duplicate `200`:** `{"source_id": "…", "duplicate": true, "state": "ready"}` — the existing source is returned and a `SOURCE_DUPLICATE` ledger row records the sighting.
- **`413`:** `{"detail": {"error": "upload_too_large", "limit_mb": 4096}}`.

```sh
curl -X POST http://localhost:8000/api/v1/uploads \
     -F "file=@/path/to/footage.mp4"
```

Uploaded originals land in `$LONGWATCH_DATA_DIR/originals/<digest16>-<safe-name>`.

### `POST /api/v1/ingest/scan`

One operator-invoked scan of `LONGWATCH_INBOX_DIR`. Files are registered **in
place** (a 50 GB drive's contents are never copied); new files queue ingest jobs,
duplicates record a `SOURCE_DUPLICATE` sighting.

- **Body:** none.
- **Success `202`:** a JSON array; one entry per file:

  ```json
  [{"source_id": "…", "job_id": "…"}, {"source_id": "…", "duplicate": true}]
  ```

- **`400 inbox_disabled`:** `LONGWATCH_INBOX_DIR` is unset.
- **`400 inbox_missing`:** the configured directory is not a directory.

```sh
curl -X POST http://localhost:8000/api/v1/ingest/scan
```

With `LONGWATCH_INBOX_POLL_S > 0` the same scan runs on a background cadence; the
route is the manual trigger.

### `POST /api/v1/demo/seed`

Creates the labeled synthetic demo fixture (160 s, generated locally with
ffmpeg's `lavfi` sources — no network, no bundled media). Idempotent.

- **Body:** none.
- **First call `202`:** `{"source_id": "…", "job_id": "…"}`.
- **Repeat call `200`:** `{"source_id": "…"}` — the intact demo source already exists.
- **`500 demo_seed_failed`:** the fixture could not be produced (typically ffmpeg absent); the message carries the reason.

```sh
curl -X POST http://localhost:8000/api/v1/demo/seed
```

There is deliberately **no environment variable** that seeds demo data. This
route and the Ingest page button are the only doors.

---

## Source routes

### `GET /api/v1/sources`

Lists every source, newest first, with `window_count` and `pending_jobs`.
No pagination — sources are a bounded operator-curated set.

### `GET /api/v1/sources/{source_id}`

Full source row plus:

- `job`: the most recent job for the source (`id`, `kind`, `state`, `stage`, `pct`, `detail`, `error`) or `null`.
- `window_count`, `ml_failed_count`.

`404` when the id is unknown.

### `GET /api/v1/sources/{source_id}/timeline`

Everything needed to draw a source timeline:

```json
{"candidates": [{"id": "…", "pattern_slug": "…", "pattern_version": 1, "score": 86,
                 "sentence": "…", "state": "open", "start_s": 0.0, "end_s": 100.0}],
 "events":     [{"kind": "audio_absent", "t_start": null, "t_end": null, "value_milli": null}],
 "windows":    [{"id": "…", "idx": 0, "start_s": 0.0, "end_s": 100.0,
                 "boundary_kind": "grid", "ml_state": "ok"}]}
```

`events.kind` ∈ `silence_gap`, `loudness_spike`, `audio_absent`.

### `GET /api/v1/sources/{source_id}/signals?from=<s>&to=<s>`

The deterministic 1 fps series, sliced in source-relative seconds:

```json
{"from": 0, "to": 5,
 "series": {"motion": [0, 0, 0, 0, 0], "luma": [1000, 1000, 1000, 1000, 1000],
            "persistence": [0, 0, 0, 0, 0]}}
```

Values are integers in milli-units (motion 0–1000, luma 0–1000, persistence
milli). `to` omitted means "to the end"; `from` defaults to 0.

### `GET /api/v1/sources/{source_id}/original`

Streams the **original file itself** with HTTP byte-range support — the
source-resolution view. `Content-Type: video/mp4`; `206` with `Content-Range`
for ranged requests, `200` (whole file) without a `Range` header. `416` for a
malformed or unsatisfiable range, `404` when the source or its file is absent.
This route accepts `?token=` (see Authentication).

---

## Clip routes

### `GET /api/v1/clips/{clip_id}/proxy`

Streams a derived artifact by byte-range: a review proxy (`kind='review_proxy'`)
or an export excerpt. Same semantics and errors as `/original`, including the
`?token=` form. `404` when the clip row or its file is absent.

---

## Triage routes

### `GET /api/v1/candidates`

The ranked queue. Detection-failed windows (unacknowledged) are always included
and pinned first; offset/limit paginate the ranked candidate side only.

- **Query:** `state` (filter: `open`, `accepted`, `rejected`, `flagged`), `limit` (default `LONGWATCH_QUEUE_LIMIT` = 50, clamped to a fixed maximum of 200 — the maximum is a code constant, not an environment knob), `offset` (default 0).
- **Response:** `{"total": 2, "entries": [ …queue entries… ], "limit": 50, "offset": 0}`.

Only candidates whose pattern version is `is_current` appear in the queue.

```sh
curl "http://localhost:8000/api/v1/candidates?state=open&limit=20"
```

### `GET /api/v1/candidates/{candidate_id}`

The workbench detail: `candidate_id`, `window_id`, `source_id`, `source_name`,
`clip_id` (the review proxy, or `null`), `start_s`, `end_s`, `pattern_slug`,
`pattern_version`, `score`, `sentence`, `state`, `evidence` (the measured values
that produced the sentence), `synthetic`, `demo`, `ml_state`,
`detections_summary` (per-class bounded summary, `null` when detection did not
run), and `decisions` (newest first: `id`, `action`, `note`, `reviewer`,
`src_viewed`, `created_at`). `404` when unknown.

### `POST /api/v1/candidates/{candidate_id}/decisions`

Records one triage decision and appends a `DECISION_RECORDED` ledger row in the
same transaction.

- **Body:**

  ```json
  {"action": "accept", "note": "describe observables, optional", "src_viewed": true}
  ```

  `action` ∈ `accept` | `reject` | `flag`; `src_viewed` records whether the
  reviewer opened the source-resolution pane.

- **Success `200`:** `{"decision_id": "…", "state": "accepted", "ledger_seq": 13, "reviewer": "operator", "src_viewed": true}`.
- **`422 bad_action`:** `{"detail": {"error": "bad_action", "allowed": ["accept", "reject", "flag"]}}`.
- **`422 vocabulary_breach`:** the note contains a forbidden term; `term` names the match and `guidance` explains the alternative.
- **`404`:** unknown candidate.

```sh
curl -X POST http://localhost:8000/api/v1/candidates/6c699be2-87b7-4f47-93cf-207a9a81ebb2/decisions \
     -H 'Content-Type: application/json' \
     -d '{"action": "accept", "src_viewed": true}'
```

### `POST /api/v1/windows/{window_id}/ml_ack`

Acknowledges a failed detection on one window, removing it from the pinned
queue head. Ledgered as `ML_FAILURE_ACKED`.

- **Body:** none.
- **Success `200`:** `{"window_id": "…", "acknowledged": true}`.
- **`404`:** unknown window. **`409 window_not_failed`:** the window has no failed detection. **`409 already_acknowledged`:** it was already acknowledged.

---

## Pattern routes

Patterns are `(slug, version)` rows. A `PUT` creates version N+1, flips
`is_current`, and queues a re-score job; it never evaluates rules inline. Any
candidate whose state is decided stays export-eligible forever, so exports are
decision-scoped, not version-scoped.

### `GET /api/v1/patterns`

All versions of all patterns:

```json
{"patterns": [{"slug": "stationary_animal_with_person", "version": 1,
               "title": "Animal stationary while a person is present",
               "sentence_template": "…", "rule": {"…": "…"}, "weight": 80,
               "enabled": true, "builtin": true, "is_current": true,
               "updated_at": "…", "updated_by": "migration"}],
 "schema_endpoint": "/api/v1/patterns/schema"}
```

### `GET /api/v1/patterns/schema`

The JSON Schema for a pattern document (`slug`, `version`, `title`,
`sentence_template`, `rule`, `weight`, `enabled`). Rule conditions:
`presence`, `absence`, `signal_threshold`, `signal_percentile`, and the
`all` / `any` combinators. Signals are `motion`, `luma`, `persistence`,
`silence_gap`. Class names are restricted to the eight retained classes
(`person`, `truck`, `bird`, `cat`, `dog`, `horse`, `sheep`, `cow`); groups to
`animal`, `person`, `vehicle`. Percentiles are restricted to
`10, 25, 50, 75, 85, 90, 95`.

### `GET /api/v1/patterns/stats`

Per-version fired/accepted/rejected/flagged/open/decided counts and a
`precision_estimate` = accepted ÷ decided (reviewer agreement with the pattern,
**not recall**; recall against labeled negatives is a named v1 item). `null`
until at least one candidate is decided.

### `PUT /api/v1/patterns/{slug}`

Creates a new version. The `slug` in the path is authoritative; the body does
not carry it.

- **Body:**

  ```json
  {"title": "Sustained motion in low light",
   "sentence_template": "motion above this recording's p85 for {dur}s while luma below its p10",
   "rule": {"type": "all", "of": [
      {"type": "signal_percentile", "signal": "motion", "op": "gte", "percentile": 85, "min_duration_s": 30},
      {"type": "signal_percentile", "signal": "luma", "op": "lte", "percentile": 10, "min_duration_s": 30}]},
   "weight": 61, "enabled": true}
  ```

  `weight` is an integer 1–100. The template may use only these placeholders:
  `{animal}`, `{class}`, `{dur}`, `{motion}`, `{tol}`, `{pdur}`, `{gap}`,
  `{value}`, `{p}`, `{samples}`.

- **Success `202`:** `{"version": 2, "rescore_job_id": "…"}`.
- **`422 vocabulary_breach`:** the title or template contains a forbidden term.
- **`422 template_error`:** an unknown placeholder or an empty template.
- **`422 rule_error`:** the rule failed validation (unknown condition type, unknown class, more than one anchor, and similar); the message names the problem.

```sh
curl -X PUT http://localhost:8000/api/v1/patterns/sustained_motion_low_light \
     -H 'Content-Type: application/json' \
     -d @pattern.json
```

### Built-in patterns

Seeded idempotently at migration time; editable (creating a new version) but
never deleted.

| slug | semantics |
|---|---|
| `stationary_animal_with_person` | An animal-class presence span ≥ 90 s at motion energy ≤ 0.150, while a person is present ≥ 30 s of the same span. Weight 80. |
| `sustained_motion_low_light` | Motion ≥ this recording's p85 for ≥ 30 s while luma ≤ its p10, for ≥ 30 s. Weight 60. |
| `long_silence_animals_present` | A silence gap ≥ 5 s with an animal present ≥ 60 s (anchor) and no person detected anywhere in the span. Weight 50. |

---

## Export routes

### `POST /api/v1/exports`

Queues a signed bundle. `top_n`, `blur_faces` and `states` are optional; omitted
fields fall back to the deployment defaults (`LONGWATCH_EXPORT_TOP_N`,
`LONGWATCH_BLUR_FACES`, `LONGWATCH_EXPORT_STATES`), which
`GET /api/v1/system/status` publishes as `export_defaults`.

- **Body:**

  ```json
  {"top_n": 200, "blur_faces": false, "states": ["accepted", "flagged"],
   "cut_precision": "keyframe"}
  ```

  `states` ⊆ `open`, `accepted`, `rejected`, `flagged`. `cut_precision` ∈
  `keyframe` (stream copy, lead-in footage included, frames bit-identical to the
  source) | `exact` (re-encode, bounds equal to the requested span, never labeled
  bit-identical).

- **Success `200`:** `{"export_id": "…", "job_id": "…"}` (the response body is a plain object; poll the job).
- **`422 bad_state` / `422 bad_cut_precision`:** the offending value is not allowed.
- **Export job failure (`state: "failed"`)** carries a named reason: a source digest mismatch (`SOURCE_DIGEST_MISMATCH` is ledgered and the export is refused), or `blur_faces` requested without a usable face model (fails closed — never a silent skip).

```sh
curl -X POST http://localhost:8000/api/v1/exports \
     -H 'Content-Type: application/json' \
     -d '{"states": ["accepted", "flagged"], "cut_precision": "keyframe"}'
```

### `GET /api/v1/exports`

The 50 most recent exports, each with its job joined in (`job_state`, `stage`,
`pct`, `error`), plus `download_ready` (true once the bundle directory exists).

### `GET /api/v1/exports/{export_id}`

The export row (`reviewer`, `top_n`, `states`, `blur_faces`, `cut_precision`,
`bundle_path`, `manifest_sha256`, `created_at`, `signed_at`) plus `job`.
`404` when unknown.

### `GET /api/v1/exports/{export_id}/download`

The bundle as `application/zip`, filename
`export_<UTCstamp>_<id8>.zip`. The ZIP is built on first request from the bundle
directory if it does not exist yet. `404` when the export is unknown or its
bundle is not ready. The route is **not** a media route: `?token=` is refused;
send the bearer header.

```sh
curl -H "Authorization: Bearer $LONGWATCH_AUTH_TOKEN" -OJ \
     http://localhost:8000/api/v1/exports/958bc256-3f40-43dc-8d9d-f059ad8a92f5/download
```

### Verifying the downloaded bundle

The ZIP contains one top-level directory, `export_<UTCstamp>_<id8>/`. Unzip it,
`cd` into that directory, and verify from the bundle alone — no LongWatch
install, no database, no vendor trust:

```sh
unzip export_20261001T130609Z_958bc256.zip
cd export_20261001T130609Z_958bc256
python3 verify_bundle.py .        # exit 0 pass, 1 fail, 2 signature not checked
sha256sum --check checksums.sha256
openssl pkeyutl -verify -pubin -inkey public.pem -rawin \
    -in manifest.canonical -sigfile manifest.sig
```

`verify_bundle.py` checks that every file digest matches the signed manifest's
`files` map — **including both verifier scripts** — that no unlisted file rides
in the bundle, that `manifest.json` re-canonicalizes byte-equal to
`manifest.canonical`, that the Ed25519 signature verifies, that the
`ledger.jsonl` chain walks clean, that `candidates.csv` agrees with the
manifest, and that `checksums.sha256` recomputes.

The `openssl` command requires OpenSSL ≥ 3.0 (Ed25519 `-rawin`) and the option
spelling is `-sigfile`; `openssl pkeyutl` does not accept `-sig`. `manifest.sig`
holds the **raw 64-byte** signature — `openssl` reads raw bytes and never
base64 — so the command runs verbatim; the signed manifest states the same
(`detached_signature.encoding = "raw"` — named so the signed object carries no
field called `signature`), and `base64 -w0 manifest.sig` prints the text
form. A verifier that base64-decodes `manifest.sig` first will fail on a valid
bundle.

**The trust root, stated honestly:** the signature proves the bundle's contents
are exactly what the signing key signed and have not changed since. It does not
prove the key belongs to the operator — no offline bundle can. Compare the key
fingerprint (SHA-256 of `public.pem`, shown on the Settings page and printed at
server startup) with the operator over a channel of your choosing before
trusting anything.

---

## Job, retention and ledger routes

### `GET /api/v1/jobs/{job_id}`

One job's status (see *Common objects*). `404` when unknown. The UI polls this
every `LONGWATCH_JOB_POLL_MS` (default 2000 ms).

### `POST /api/v1/retention/purge`

Queues a purge of LongWatch's own derived artifacts — review proxies, excerpts,
exports — never originals and never source rows. Every deletion is a ledger
event. Idempotent in effect: it always queues one job.

- **Body:** none. **Success `202`:** `{"job_id": "…"}`.

```sh
curl -X POST http://localhost:8000/api/v1/retention/purge
```

### `GET /api/v1/ledger/verify`

Walks the custody ledger chain and returns:

```json
{"ok": true, "length": 13, "head_hash": "6ef8adbe…", "first_divergence": null}
```

`first_divergence` is the sequence number of the first bad row, or `null` when
the chain is clean. The same walk runs incrementally in the background every
`LONGWATCH_LEDGER_VERIFY_S` (default 3600 s); a failure logs at ERROR.

---

## System routes

### `GET /api/v1/system/status`

The operator's status surface. Keys:

| Key | Contents |
|---|---|
| `service` | `"longwatch"`. |
| `database` | `"ok"` or `"error: …"`. |
| `model` | `{ok, input, output, classes, class_map, ort_threads, model, provenance}` — the ONNX contract when it loads, or `{ok: false, error, provenance}`. `provenance` ∈ `fetched`, `fetched_unpinned`, `fixture`, `operator-supplied`, `unknown`. |
| `class_map` | `{ok, classes}` or `{ok: false, error}`. |
| `workers` | `{media: {alive, kinds, jobs_processed, last_job_id, last_error}, utility: {…}}`. |
| `offline_guard` | `{installed, database_host, allowed_ips, sanctioned_egress}`. |
| `ml_failed_count` | Unacknowledged detection-failed windows (`-1` when the count itself failed). |
| `key` | `{fingerprint, public_pem}` or `{error}`. |
| `inbox` | `{dir, enabled}`. |
| `disclosures` | `integrity_statement`, `time_basis`, `review_medium`, `model_limits`, `person_absent`, `trust_boundary` — the exact strings the UI renders. |
| `auth` | `{token_required, trust_boundary}`. |
| `export_defaults` | `{blur_faces, top_n, states}` — what an export request that omits fields will use. |

### `GET /healthz`

Liveness + readiness in one: `{"ok": true, "checks": {"database": "ok", "model": {"ok": true, …}}}`.
Returns `200` when the database responds and the detector passes its self-test;
`503` with the failing check named otherwise. Unauthenticated, always.

---

## SPA routes

| Route | Behaviour |
|---|---|
| `GET /` | The SPA document. |
| `GET /{path:path}` | Serves a static asset from `longwatch/static/`, falling back to `index.html` for client-side routes. Paths beginning `api/`, `healthz`, `openapi.json`, `docs`, `redoc` are never served here. If the SPA was never built, any SPA route returns `503 {"error": "spa_not_built"}`. |

`/docs`, `/redoc` and `/openapi.json` are FastAPI's own, served unauthenticated.

---

## CLI reference

The package installs one console script, `longwatch` (identical to
`python -m longwatch`). Every subcommand reads the same environment as the
server (`longwatch/__main__.py`).

| Command | What it does | Exit codes |
|---|---|---|
| `longwatch serve [--host HOST] [--port PORT]` | Wait for the database → migrate (idempotent) → seed builtin patterns → run the HTTP server. `--host`/`--port` override `HOST`/`PORT`. | `0` on clean shutdown; non-zero with `DATABASE_URL is required and is never defaulted` or `database did not become reachable: …`. |
| `longwatch migrate` | Apply migrations, seed builtins on `(slug, version)`, ensure the genesis ledger row; prints the outcome as JSON and exits. Safe to re-run — a second run is a clean no-op. | `0`. |
| `longwatch patterns export [--out FILE]` | Print (or write) every pattern row as JSON, `updated_at` ISO-formatted. | `0`. |
| `longwatch patterns import PATH` | Create any pattern versions in the file that do not already exist; prints `{"created": [...]}`. Existing `(slug, version)` rows are skipped. | `0`; a missing path is a usage error. |
| `longwatch verify-bundle BUNDLE_DIR` | Run the bundle verifier against a directory and print its report. | The verifier's exit code (see below). |

```sh
python -m longwatch migrate
python -m longwatch patterns export --out patterns.json
python -m longwatch patterns import patterns.json
python -m longwatch verify-bundle ./export_20261001T130609Z_958bc256
```

### The bundle verifier scripts

Two scripts ship inside every export bundle and are themselves listed in the
signed manifest. A third party runs them with no LongWatch install.

**`verify_bundle.py`** — stdlib only (`hashlib`, `json`, `csv`) plus the
in-repo `ed25519_verifier.py` beside it.

| Entry point | Signature / usage | Notes |
|---|---|---|
| CLI | `python3 verify_bundle.py [bundle_dir]` | Defaults to the script's own directory. Exit `0` verified, `1` a check failed, `2` the argument is not a directory **or** the signature could not be checked (`SIGNATURE NOT CHECKED` — that verdict outranks any other failing check, so a caller can always tell "not checked" from "checked and failed"). |
| `verify_bundle(bundle: str) -> Report` | Returns a report object | `report.exit_code`, `report.checks` (list of `(name, PASS|FAIL, detail)`), `report.print()`. |
| `canonical_json(document) -> str` | The §7.1 canonical form | `json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` — reproduced so the bundle is standalone. |
| `sha256_file(path: str) -> str` | Lowercase hex digest | Streams in 1 MiB chunks. |

**`ed25519_verifier.py`** — an in-repo, MIT-licensed, verify-only implementation
of RFC 8032 §5.1.7. No third-party code is vendored.

| Entry point | Signature / usage | Notes |
|---|---|---|
| CLI | `python3 ed25519_verifier.py <key> <sig> <message-file>` | `key` is a path to a `.pem` file, or inline PEM text, or hex. `sig` is inline hex, or a path to a file holding the raw 64 bytes or their base64 text. Prints `SIGNATURE VALID` / `SIGNATURE INVALID`; exit `0` valid, `1` invalid, `2` usage/parse error. |
| `verify(public_key: bytes, message: bytes, signature: bytes) -> bool` | Raw bytes | `True` only for a valid signature. |
| `verify_hex(public_key_hex: str, message: bytes, signature_hex: str) -> bool` | Hex strings | Convenience wrapper. |

---

## Pagination, rate limits, and quotas

- **Pagination:** only `GET /api/v1/candidates` paginates (`limit`/`offset`); its
  `total` covers both the pinned ml-failed rows and the filtered candidate count.
  Other collections are bounded by construction (sources: operator-curated;
  exports: 50 most recent; patterns: version rows).
- **Rate limits:** none. LongWatch is a single-operator offline appliance; there
  is no public ingress by design.
- **Quotas that exist:** upload ceiling (`LONGWATCH_UPLOAD_LIMIT_MB`), concurrent
  uploads (`LONGWATCH_UPLOAD_CONCURRENCY`), queue page size
  (`LONGWATCH_QUEUE_LIMIT`, with a fixed maximum of 200), export candidate cap
  (`LONGWATCH_EXPORT_TOP_N`).

---

## Where to next

- [README](../README.md) — overview and quick start.
- [Product guide](USER_GUIDE.md) — the operator's journey through the UI.
- [Architecture](ARCHITECTURE.md) — how the pieces fit together.
- [UI reference](UI.md) — screens, keyboard model, and states.
- [Operations](OPERATIONS.md) — configuration, deployment, troubleshooting.
- [Examples](examples/) — runnable end-to-end scripts.
