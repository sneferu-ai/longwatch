# LongWatch — operations guide

Everything an operator needs to run, deploy, watch, and debug LongWatch.

- **Audience:** the person who installs and keeps the service running.
- **Scope:** prerequisites, configuration, local and Docker operation, observability, troubleshooting, backup.
- **Related:** [README](../README.md) · [Product guide](USER_GUIDE.md) · [API reference](API.md) · [Architecture](ARCHITECTURE.md) · [UI reference](UI.md)

---

## 1. Prerequisites

| Requirement | Version / notes |
|---|---|
| Python | 3.12+ (`requires-python = ">=3.12"` in `pyproject.toml`). |
| PostgreSQL | 16 — what the product is built and tested against. The schema needs `gen_random_uuid()` (built-in since 13) and statement-level triggers. |
| ffmpeg / ffprobe | On `PATH`. The suite was last proven green against ffmpeg 7.0.2; the Docker image installs Debian bookworm's ffmpeg (5.1). |
| Node.js | Only to build the SPA (`frontend/`). Node 22 is what the Docker image uses. |
| Disk | Originals are referenced in place, not copied. Derived artifacts (proxies, excerpts, exports) live under `LONGWATCH_DATA_DIR`; budget roughly the proxy bitrate × corpus duration for proxies, plus exports. |
| CPU | Everything runs on CPU — ffmpeg decode and ONNX Runtime inference. No GPU is required or used. |

Python dependencies (installed from `pyproject.toml`): `fastapi`, `uvicorn[standard]`,
`psycopg[binary]`, `onnxruntime`, `numpy`, `cryptography`, `python-multipart`,
`pydantic`. Dev extras: `pytest`, `httpx`, `playwright`.

---

## 2. Configuration — environment variables only

There are no config files. Every knob is an environment variable, read once at
process start (`longwatch/config.py`). `DATABASE_URL` is required and is never
defaulted.

### Core

| Variable | Default | Required | Purpose |
|---|---|---|---|
| `DATABASE_URL` | *(none)* | **yes** | Postgres DSN. Also the host whose DNS resolution is the single sanctioned egress. A literal-IP DSN eliminates even that. |
| `PORT` | `8000` | no | HTTP bind port. (`LONGWATCH_PORT` is accepted as a fallback; `PORT` wins.) |
| `HOST` | `0.0.0.0` | no | HTTP bind address. (`LONGWATCH_HOST` is the fallback.) |
| `LONGWATCH_DATA_DIR` | `/data` | no | Writable volume root: originals, proxies, excerpts, exports, keys, fixtures. **Persist it — the signing key lives here.** (`SOD_DATA_DIR` is honoured when `LONGWATCH_DATA_DIR` is absent.) |
| `LONGWATCH_REVIEWER_NAME` | `operator` | no | The attribution string stamped on every human action. Not a login. |
| `LONGWATCH_AUTH_TOKEN` | *(unset)* | no | Optional bearer token. Unset ⇒ anyone who can reach the port is the operator; the trust-boundary text is published by `/api/v1/system/status` and shown on the Settings page. |
| `LONGWATCH_OFFLINE` | `1` | no | Install the socket guard. Set `0` only in tests or when you deliberately want unrestricted networking (the guard is the product's egress refusal). |

### Ingest

| Variable | Default | Purpose |
|---|---|---|
| `LONGWATCH_INBOX_DIR` | *(unset = disabled)* | Operator-designated scan directory. Files are registered **in place** — never copied. |
| `LONGWATCH_INBOX_POLL_S` | `0` (off) | Optional auto-scan cadence in seconds (minimum effective 5 s). |
| `LONGWATCH_UPLOAD_LIMIT_MB` | `4096` | Upload ceiling; a breach aborts mid-stream with `413` and deletes the partial file. |
| `LONGWATCH_UPLOAD_CONCURRENCY` | `2` | Concurrent upload streams. |

### Analysis knobs

| Variable | Default | Purpose |
|---|---|---|
| `LONGWATCH_DETECT_FPS` | `2.0` (clamped 0.5–5) | Motion-active sampling rate. |
| `LONGWATCH_STATIC_FPS` | `0.5` | Sampling floor for low-motion windows (never a skip). |
| `LONGWATCH_STATIC_MOTION` | `0.02` | Motion threshold for the downshift. |
| `LONGWATCH_CONF_MIN` | `0.25` | Detector confidence floor. |
| `LONGWATCH_NMS_IOU` | `0.45` | Per-class NMS overlap. |
| `LONGWATCH_SCENE_T0` | `0.30` | Adaptive scene-cut start (specified range 0.15–0.60); an early-exit probe runs below band at 0.15. |
| `LONGWATCH_COVERAGE_S` | `300` | Coverage grid interval in seconds. |
| `LONGWATCH_WINDOW_MIN_S` / `LONGWATCH_WINDOW_MAX_S` | `10` / `180` | Window length clamps (hard invariants). |
| `LONGWATCH_PROXY_LONG_EDGE` | `640` | Review proxy long edge; the value travels in every manifest's review-medium disclosure. |
| `LONGWATCH_STORE_FRAMES` | `0` | Debug only: store bounded full per-frame detections (≤ 128 KiB, truncation flagged). |

### Models

| Variable | Default | Purpose |
|---|---|---|
| `LONGWATCH_MODEL_PATH` | `/opt/longwatch/models/yolov8n.onnx` | Detector ONNX file. |
| `LONGWATCH_MODEL_SHA256` | *(image-recorded)* | Digest check at load. The image entrypoint exports the digest the models stage recorded for the shipped file; a mismatch fails readiness. |
| `LONGWATCH_CLASS_MAP` | `/opt/longwatch/models/coco80.json` | Index→name class map, validated against the model contract. |
| `LONGWATCH_BLUR_FACES` | `0` | Deployment default for export-time face pixelation (an explicit request value wins). |
| `LONGWATCH_FACE_MODEL_PATH` | `/opt/longwatch/models/yunet.onnx` | Face detector — used only when pixelation is requested. |
| `LONGWATCH_FACE_MODEL_SHA256` | *(image-recorded)* | Face-model digest check at export load. |

### Queue, ledger, exports

| Variable | Default | Purpose |
|---|---|---|
| `LONGWATCH_JOB_LEASE_S` | `120` | A `running` job whose heartbeat is older than this is reclaimed. Worker heartbeats run every 15 s. |
| `LONGWATCH_LEDGER_VERIFY_S` | `3600` | Incremental chain re-verification cadence. |
| `LONGWATCH_ORT_THREADS` | `1` | ONNX intra-op threads, sequential. |
| `LONGWATCH_EXPORT_TOP_N` | `200` | Candidates per export (deployment default). |
| `LONGWATCH_EXPORT_STATES` | `accepted,flagged` | Default export scope (comma-separated). |
| `LONGWATCH_QUEUE_LIMIT` | `50` | Queue page size (`limit` query param default). The maximum accepted is a fixed 200. |
| `LONGWATCH_JOB_POLL_MS` | `2000` | UI poll cadence for jobs and queues. |
| `LONGWATCH_FFMPEG_HANG_BASE_S` | `60` | Hang-guard timeout base per ffmpeg invocation. |
| `LONGWATCH_FFMPEG_HANG_FACTOR` | `4.0` | Hang-guard timeout = base + factor × input-seconds. |
| `LONGWATCH_SIGN_KEY_PATH` | `$LONGWATCH_DATA_DIR/keys/ed25519.pem` | Signing key location (generated on first start, mode 0600). |

### Entrypoint-only

| Variable | Default | Purpose |
|---|---|---|
| `LONGWATCH_DB_WAIT_S` | `180` | How long the container entrypoint waits for the database before exiting non-zero. |

### Test-only

| Variable | Purpose |
|---|---|
| `TEST_DATABASE_URL` | Database for the test suite (falls back to `DATABASE_URL`). Each test function owns a fresh schema; the suite never touches the app schema. |

### Docker build args

| Build arg | Default | Purpose |
|---|---|---|
| `LONGWATCH_MODEL_URL` | Ultralytics YOLOv8n ONNX release URL | Detector source. `none` disables **this** fetch; the in-repo contract fixture (`tests/fixtures/model_fixture.onnx`, a plain file copy) is used instead. |
| `LONGWATCH_MODEL_SHA256` | *(from `models/pins.json`, empty until pinned)* | Digest pin; a mismatch **fails the build**. |
| `LONGWATCH_FACE_MODEL_URL` | OpenCV Zoo YuNet ONNX URL | Face model source. `none` disables **this** fetch. |
| `LONGWATCH_FACE_MODEL_SHA256` | *(from `models/pins.json`)* | Face-model digest pin. |

The two fetches are independent: a detector URL that cannot be reached never
costs the build its face model, and a face URL that cannot be reached leaves the
detector in place (pixelation then fails closed at export, as specified). A
deliberate fixture-only build passes `none` for **both** URLs. The detector
fallback is a file copy of the committed fixture — no network, no `pip`, no
generator — so a genuinely network-less build still produces the documented
`MODEL_PROVENANCE=fixture` image.

---

## 3. Running locally (from source)

```sh
git clone YOUR_REPOSITORY_URL longwatch && cd longwatch
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
cd frontend && npm ci && npm run build && cd ..     # builds longwatch/static/
export DATABASE_URL='postgresql://user:pass@localhost:5432/longwatch'
python -m longwatch migrate
python -m longwatch serve --host 0.0.0.0 --port 8000
```

`scripts/build_spa.sh` runs the `npm ci && npm run build` step for you. Without a
built SPA the API still works; every SPA route returns
`503 {"error": "spa_not_built"}`.

**What success looks like**

- `python -m longwatch migrate` prints
  `{"migrations_applied": [...], "patterns_seeded": [...], "genesis": {...}}` and exits 0.
- `serve` logs `LongWatch signing public key fingerprint: <64 hex>` and
  `Application startup complete.` Then:

  ```sh
  curl -s http://localhost:8000/healthz
  # {"ok":true,"checks":{"database":"ok","model":{"ok":true,...}}}
  ```

  A 200 with `"ok": true` means database and detector are both ready.

**Running against a live database without the entrypoint:** `python -m longwatch
serve` itself waits for the database, migrates, and seeds builtins before
binding — the entrypoint script adds only the digest exports and the `exec`.

### Tests

```sh
TEST_DATABASE_URL='postgresql://user:pass@localhost:5432/longwatch_test' \
  python3 -m pytest tests/ -q
```

Expected result in a full environment (Postgres 16, ffmpeg 7, Node, a browser):
**`180 passed`**. Scenarios that need a real tool skip with a named reason when
the tool is absent (`ffmpeg/ffprobe are not available in this environment`,
`no TEST_DATABASE_URL/DATABASE_URL configured — database scenarios skipped`).
A skip is never reported as a pass. The suite was last verified green in this
repository against Postgres 16 + ffmpeg 7.0.2 + Node.

Thirteen named scenarios: `VOCAB`, `CANON` (with an independent JavaScript leg),
`CLASSES`, `CHAIN`, `CONCUR`, `COVER`, `PATTERNS`, `DEMO`, `QUEUE`, `EXPORT`,
`HEALTH`, `GUARD`, `E2E` (Playwright).

---

## 4. Deployment — Docker

The delivered artifact is one root Dockerfile producing one image.

```sh
docker build -t longwatch .
docker run --rm -p 8000:8000 \
  -e DATABASE_URL='postgresql://user:pass@host:5432/longwatch' \
  -v longwatch-data:/data \
  longwatch
```

Then open <http://localhost:8000>.

Notes:

- The image runs as non-root (`uid 10001`) and declares `VOLUME /data`.
- The container needs a reachable Postgres; there is no compose file in this
  repository. Any Postgres 16 instance works — a plain `docker run postgres:16`
  with a `DATABASE_URL` pointing at it is sufficient:

  ```sh
  docker network create longwatch
  docker run -d --name lw-pg --network longwatch \
    -e POSTGRES_PASSWORD=longwatch -e POSTGRES_USER=longwatch -e POSTGRES_DB=longwatch \
    postgres:16-alpine
  docker run --rm -p 8000:8000 --network longwatch \
    -e DATABASE_URL='postgresql://longwatch:longwatch@lw-pg:5432/longwatch' \
    -v longwatch-data:/data longwatch
  ```

- **Model provisioning.** The `models` stage fetches the detector and face model
  from their canonical public sources at image build time and records
  `MODEL_PROVENANCE` (`fetched`, `fetched_unpinned`, `fixture`). A no-build-args
  build fetches the real weights. To make it **verify** them, run the one pin
  command on a machine with network access and commit the file:

  ```sh
  scripts/pin_models.sh --write      # fetches both artifacts, records their digests
  docker build .                     # now verifies both pins; a mismatch fails the build
  ```

  A genuinely network-less build falls back to the in-repo contract-valid
  fixture (`MODEL_PROVENANCE=fixture`) and every surface says so — the About
  panel, `/api/v1/system/status` (`model.provenance`), and the UI disclosure
  line. **Detections from the fixture are placeholders.** Never present a
  fixture-provenance deployment as a working detector.

- **Upgrades.** Build a new image, stop the old container, start the new one
  against the same `DATABASE_URL` and the same data volume. The entrypoint
  migrates idempotently and re-seeds builtins on `(slug, version)`; a second
  `migrate` is a clean no-op. The signing key persists on the volume, so old and
  new bundles share one key identity.

- **Re-deploy against an empty volume** starts a **new ledger** (new genesis).
  Old bundles stay verifiable from their own `ledger.jsonl`. Restore the data
  volume for chain continuity.

---

## 5. Observability

### Health and status

| Surface | What it tells you |
|---|---|
| `GET /healthz` | `200` when the database responds and the detector passes its self-test; `503` with the failing check named otherwise. Unauthenticated. |
| `GET /api/v1/system/status` | Database, model contract + provenance, class map, both worker threads (`alive`, `jobs_processed`, `last_job_id`, `last_error`), offline-guard state, unacknowledged ml-failed window count, signing-key fingerprint, inbox state, and the exact disclosure strings. |
| `GET /api/v1/ledger/verify` | Full chain walk: `{ok, length, head_hash, first_divergence}`. |
| `GET /api/v1/jobs/{job_id}` | Stage-level progress (`probe`, `windows`, `proxies`, `signals`, `detect`, `score`, `bundle`, `purge`, `rescore`) and `pct`. |

### Logs

The server logs to stdout with `INFO` level by default
(`%(asctime)s %(levelname)s %(name)s %(message)s`). Under Docker:

```sh
docker ps                              # find the container name or id
docker logs -f CONTAINER               # follow the server log
```

Lines worth knowing:

- `LongWatch signing public key fingerprint: <hex>` — printed once per boot; compare it with bundle recipients out of band.
- `offline guard installed: {'installed': True, 'host': …, 'ips': [...]}` — the sanctioned egress allowlist.
- `LEDGER CORRUPTION detected at seq <n>` — logged at `ERROR` by the background loop; this is the one failure the product may never have silently.
- `worker <name> failed a job: …` — a job exception; the job row carries the error text.
- `inbox polling every <n>s on <dir>`.

### The ledger banner

The UI is designed to show a blocking red banner when ledger verification fails.
In the current build the failure is surfaced through
`GET /api/v1/ledger/verify` (`ok: false`, `first_divergence`), the Settings
page's **Verify chain now** button, and the `LEDGER CORRUPTION` ERROR log line —
the banner itself is driven by that verify result. If you suspect tampering,
treat `first_divergence` as the row number to investigate and do not mutate the
table.

### What leaves the box

Nothing, except the operator's own bundle downloads. The socket guard refuses
any outbound `connect` / `sendto` / `getaddrinfo` other than the configured
database host; a runtime egress bug crashes loudly instead of leaking.

---

## 6. Troubleshooting

Each entry names the symptom, the exact diagnosis, and the fix.

### 6.1 `/healthz` returns 503

**Diagnose:**

```sh
curl -s http://localhost:8000/healthz | python3 -m json.tool
```

The `checks` object names the failing leg.

- `"database": "error: …"` — Postgres is unreachable or the DSN is wrong. Test
  the DSN directly: `psql "$DATABASE_URL" -c 'select 1'`.
- `"model": {"ok": false, "error": "…"}` — the detector failed its contract
  check. The error names the cause: file absent, digest mismatch, wrong tensor
  shape, or a class map that does not cover the model's classes.

**Fix:** for a model error, confirm `LONGWATCH_MODEL_PATH` exists inside the
container and that `LONGWATCH_MODEL_SHA256` matches it (`sha256sum <path>`); a
digest mismatch is deliberate fail-closed behavior — either fix the file or
unset the digest if you trust the file. For a database error, fix connectivity;
the entrypoint waits `LONGWATCH_DB_WAIT_S` (180 s) before giving up with
`database did not become reachable`.

### 6.2 A job sits in `running` forever

**Diagnose:**

```sh
JOB_ID=e7797a19-8934-4d6b-92d4-870aed0f7fdc    # the id returned by the POST that queued the job
curl -s "http://localhost:8000/api/v1/jobs/${JOB_ID}" | python3 -m json.tool
curl -s http://localhost:8000/api/v1/system/status | python3 -c \
  "import json,sys; print(json.load(sys.stdin)['workers'])"
```

- If `workers.media.alive` (or `utility.alive`) is `false` or `last_error` is
  non-null, the worker thread died or is failing every claim — read the server
  log around `worker … failed a job`.
- If the workers are alive but the job's `beat_at` is stale, the lease reclaimer
  is about to take it: any `running` job whose heartbeat is older than
  `LONGWATCH_JOB_LEASE_S` (default 120 s) is reclaimed and retried
  (`attempts` increments; the detail gains `[lease reclaimed]`). After three
  attempts a stale job is marked `failed` with the error
  `lease expired after N attempts` — a job is never silently retried forever.

**Fix:** for a crashed worker, restart the process — both threads start with the
lifespan. For a job that keeps failing, the `error` column on the job names the
cause (missing source file, ffmpeg failure with stderr preserved, model error).
A source that was moved or deleted outside LongWatch lands in state `missing`
with `failure_reason: "source file absent at stage …"` — restore the file at its
recorded `storage_path` or accept that source is no longer processable; the
windows are marked failed for manual review.

### 6.3 Uploads fail with 413

**Diagnose:** the response body is
`{"detail": {"error": "upload_too_large", "limit_mb": 4096}}`.

**Fix:** raise `LONGWATCH_UPLOAD_LIMIT_MB` and restart, or split the corpus and
use the inbox scan path (files are registered in place — no copy, no upload
ceiling).

### 6.4 Export fails with "face pixelation was requested but no usable face model"

**Diagnose:** the export job's `error` column contains that sentence, or the
face model's contract check failed with a named reason.

**Fix:** the option fails closed by design — it never silently ships
un-pixelated footage. Either unset `LONGWATCH_BLUR_FACES` / untick the box, or
provide a valid YuNet model at `LONGWATCH_FACE_MODEL_PATH` (the Docker image
ships one when the fetch succeeded; a fixture/network-less build may not).

### 6.5 The inbox scan returns 400

- `{"error": "inbox_disabled"}` — `LONGWATCH_INBOX_DIR` is unset. Set it and restart.
- `{"error": "inbox_missing"}` — the configured path is not a directory (typo, unmounted volume).

### 6.6 A pattern edit is refused with 422

- `vocabulary_breach` names the matched term; rewrite the title/template using
  measured observables.
- `template_error` names the unknown placeholder; only `{animal}`, `{class}`,
  `{dur}`, `{motion}`, `{tol}`, `{pdur}`, `{gap}`, `{value}`, `{p}`, `{samples}`
  are accepted.
- `rule_error` names the rule problem (unknown condition type, class outside the
  eight retained classes, more than one anchor, and similar).

### 6.7 Export refuses with a source digest mismatch

The export job fails with `source <name> digest mismatch (<actual>); export
refused`, and a `SOURCE_DIGEST_MISMATCH` ledger row records the expected and
actual digests. This means the file at the source's `storage_path` changed after
ingest. LongWatch never writes originals; something else did. Investigate before
re-ingesting: the ledger holds the digest recorded when LongWatch read the file.

### 6.8 Every SPA route returns 503 `spa_not_built`

The frontend was never built. Run `cd frontend && npm ci && npm run build`
(output lands in `longwatch/static/`), or use `scripts/build_spa.sh`, or build
the Docker image, whose `spa` stage does it.

---

## 7. Data, backup, and recovery

### Layout under `$LONGWATCH_DATA_DIR` (default `/data`)

```
inbox/       operator scan directory (only when configured elsewhere)
originals/   uploaded originals: <digest16>-<safe-name>
proxies/     per-window review proxies (derived; purgeable)
excerpts/    derived excerpts (derived; purgeable)
exports/     export_<UTCstamp>_<id8>/ bundle directories + .zip files
keys/        ed25519.pem (mode 0600) — back this up with the database
tmp/         scratch (upload staging, export cuts) — safe to clear when idle
fixtures/    the generated demo fixture
```

### What to back up

1. **PostgreSQL** — the state, queue, and custody ledger. Without it, the chain history is gone.
2. **`$LONGWATCH_DATA_DIR/keys/`** — the Ed25519 signing key. Losing it invalidates *future* signatures, never past ones (every past bundle carries its own key), but recipients will need the new fingerprint.
3. **`originals/`** — only if the originals do not already live elsewhere. They are references, not copies.

Proxies, excerpts, and exports are derived artifacts; the retention purge
(`POST /api/v1/retention/purge`, or the Settings page button) deletes them and
ledgers every deletion. It never touches originals or source rows.

### Recovery after a restart

1. Start the process. The lifespan installs the guard, re-checks migrations
   (no-op), loads the key, runs one incremental ledger verification, and starts
   both workers.
2. Check `GET /healthz` for `"ok": true`.
3. Check `GET /api/v1/ledger/verify` for `"ok": true`.
4. Open the UI and confirm sources, candidates, and decisions are intact — they
   are database rows; nothing about a restart rewrites them.
5. Any job that was mid-flight when the process died is reclaimed by the lease
   reclaimer within `LONGWATCH_JOB_LEASE_S` and retried from its recorded stage.

### Retention policy

`LONGWATCH_LEDGER_VERIFY_S` controls re-verification cadence, not retention. The
ledger itself is never trimmed in v0. Derived artifacts persist until an
operator-invoked purge.

---

## 8. Security notes for operators

- **Trust boundary.** Without `LONGWATCH_AUTH_TOKEN`, anyone who can reach the
  port is the operator. Deploy on loopback or behind an operator-controlled
  proxy if a boundary is needed. With the token set, it is compared in constant
  time and accepted as `Authorization: Bearer <token>` on every `/api/*` route
  and — solely on the two byte-range media routes — as `?token=`. Every response
  carries `Referrer-Policy: no-referrer`.
- **Secrets.** `DATABASE_URL` and (optionally) `LONGWATCH_AUTH_TOKEN` are read
  from the environment. No credential-shaped file is written into the source
  tree, and no secret value appears in the repository.
- **Key hygiene.** The signing key is generated on first start into
  `$LONGWATCH_DATA_DIR/keys/ed25519.pem` with mode 0600. Rotating it is safe for
  past bundles; re-share the new fingerprint with recipients after any rotation.
- **Egress.** The socket guard is installed before the DB pool opens. If a
  dependency ever attempts an outbound connection it does not need, the process
  crashes with `OfflineViolation` — that is the intended, loud failure mode.

---

## Where to next

- [README](../README.md) — overview and quick start.
- [Product guide](USER_GUIDE.md) — the operator's journey through the UI.
- [API reference](API.md) — every route and its shapes.
- [Architecture](ARCHITECTURE.md) — how the pieces fit together.
- [UI reference](UI.md) — screens, states, and the keyboard model.
