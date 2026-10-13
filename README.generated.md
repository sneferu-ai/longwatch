# LongWatch

Offline footage triage and evidence-integrity ledger.

LongWatch reads video that already exists on a drive, produces a **ranked queue
of spans a human should examine**, attaches a tamper-evident custody record to
every artifact it derives, and stops. It is not a case-management suite, not an
abuse classifier, and not a surveillance product. The strongest sentence any
surface of the system can produce is: *a person should look at this span, and
here are the measured reasons.*

One FastAPI service plus a React single-page application served from the same
origin; PostgreSQL 16 for state, queue and custody ledger; ffmpeg/ffprobe and
ONNX Runtime on CPU. MIT licensed.

**Product guide:** [docs/USER_GUIDE.md](docs/USER_GUIDE.md) — the full walkthrough
for the person who reviews footage with LongWatch: first URL, the primary
journey from ingest to a verified bundle, restart and recovery, and every common
error with its exact fix.

---

## Documentation

| Document | For |
|---|---|
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | **Product guide** — using LongWatch end to end (no code knowledge needed). |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | C4 diagrams, module map, data model, design decisions. |
| [docs/API.md](docs/API.md) | Every HTTP route: parameters, shapes, errors, curl examples. |
| [docs/UI.md](docs/UI.md) | Screen inventory, states, keyboard model, API-to-UI map. |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Install, configure, deploy, monitor, troubleshoot. |
| [docs/examples/](docs/examples/) | Five runnable end-to-end examples (health, ingest, triage, export+verify, ledger). |

---

## The claim, verbatim

> The SHA-256 digests in this bundle show that each listed excerpt file is
> byte-identical to material cut from a source file whose digest LongWatch
> recorded when it read that file, and that the accompanying custody record has
> not changed since each entry was written. They show nothing about what
> happened to the footage before LongWatch received it, nothing about when it
> was recorded, and nothing about whether depicted conduct was lawful, humane,
> or otherwise. LongWatch makes exactly one claim: the listed time ranges
> deserve examination by a qualified person, and the measured reasons are
> attached.

The same text appears in every export manifest, in the UI's About panel, and
here.

**Time basis.** Every timestamp the system writes is a server-local processing
time, labeled as such: `server-local clock at processing time; not synchronized
to any external time authority`. Processing time is never capture time.
RFC 3161 anchoring requires an online authority and is deliberately out of scope
for an offline tool.

**Review medium, disclosed and mitigated.** Triage decisions are rendered on a
lossy working copy (long edge ≤ `LONGWATCH_PROXY_LONG_EDGE` px, CRF 28), not on
the evidence excerpt. Every manifest and CSV row says so. The workbench offers a
**source-resolution view** (`v`): the original file itself streamed by
byte-range into a second pane, so the reviewer can judge exactly the pixels that
will ship. Every decision records whether that view was used (`src_viewed`), and
that fact travels into the decision ledger event, the CSV, and the manifest.
Recipients should spot-check accepted decisions against the excerpts.

---

## Quick start

**Prerequisites.** Python 3.12, PostgreSQL 16, `ffmpeg`/`ffprobe` on `PATH`, and
Node 22 + npm to build the single-page application. (The delivered image bakes
all of these; the from-source path below needs them on the host.)

**Setup (from source).**

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'                 # needs ffmpeg/ffprobe on PATH
bash scripts/build_spa.sh               # npm ci + vite build → longwatch/static/
export DATABASE_URL='postgresql://user:pass@localhost:5432/longwatch'
```

**Start.**

```sh
python -m longwatch migrate             # idempotent; safe to re-run
python -m longwatch serve --host 0.0.0.0 --port 8000
```

**Open.** <http://localhost:8000> — the product route is `/`; the SPA is served
same-origin from `longwatch/static/`. Health is `GET /healthz` (200 only when a
database query **and** the model self-test both pass, 503 otherwise), and the
OpenAPI document is at `/openapi.json`.

**Required configuration.** `DATABASE_URL` only. `LONGWATCH_DATA_DIR` defaults to
`/data` and must be a persistent volume — the Ed25519 signing key lives there.
There are no shipped credentials: the optional `LONGWATCH_AUTH_TOKEN` is unset by
default, and the interface says so in Settings and About.

**Build and test.**

```sh
cd frontend && npm run build            # type-check (tsc) + vite build
python3 -m pytest tests/ -q             # the named scenarios of SPEC §17
python3 scripts/ui_screenshots.py       # SPA screenshots + DOM contract, mocked API
node frontend/scripts/token-audit.mjs   # no raw values outside tokens.css
node frontend/scripts/contrast-audit.mjs
```

**Stop.** `Ctrl-C` in the server's terminal (uvicorn exits cleanly; worker
threads drain on lifespan shutdown). A Docker deployment stops with
`docker stop <container>`.

**Where the output goes.** The server logs to stdout — the migration summary,
the pattern seeding, the offline-guard installation, the worker start lines, and
one line per job stage. In the browser, the UI reports every failure in place
(a notice in the panel that failed, plus a toast); nothing is swallowed to the
console. If the database is unreachable the header readout turns red and keeps
polling; on narrow screens, where the readout is hidden, the status banner
carries the same signal.

---

## Running it

### Docker (the delivered image)

```sh
docker build -t longwatch .
docker run --rm -p 8000:8000 \
  -e DATABASE_URL='postgresql://user:pass@host:5432/longwatch' \
  -v longwatch-data:/data \
  longwatch
```

Then open <http://localhost:8000>.

The image's entrypoint performs the real initialization: wait for the database →
migrate (idempotent; the runner creates `schema_migrations` itself) → seed the
three builtin patterns idempotently on `(slug, version)` → exec the server. No
demo data is seeded — see *Demo data* below.

### From source

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'                 # needs ffmpeg/ffprobe on PATH
cd frontend && npm ci && npm run build && cd ..
export DATABASE_URL='postgresql://user:pass@localhost:5432/longwatch'
python -m longwatch migrate
python -m longwatch serve --host 0.0.0.0 --port 8000
```

CLI subcommands: `serve`, `migrate`, `patterns export|import`, `verify-bundle`.

### Tests

```sh
python3 -m pytest tests/ -q
```

Thirteen named scenarios: `VOCAB`, `CANON` (with an independent JavaScript leg),
`CLASSES`, `CHAIN`, `CONCUR`, `COVER`, `PATTERNS`, `DEMO`, `QUEUE`, `EXPORT`,
`HEALTH`, `GUARD`, `E2E`. Database scenarios use `TEST_DATABASE_URL` (falling
back to `DATABASE_URL`) and each test owns a fresh schema; tool-dependent
scenarios skip with a named reason rather than passing silently.

---

## Configuration

Environment variables only; no config files. `DATABASE_URL` is required and is
never defaulted. **There is deliberately no demo-seed environment variable** —
demo data exists only behind the explicit operator action described below, so no
environment default can write synthetic rows into an evidence database.

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `8000` | HTTP bind port |
| `DATABASE_URL` | *required* | Postgres DSN (the sole external dependency) |
| `LONGWATCH_DATA_DIR` | `/data` | writable volume root; persist it — the signing key lives here |
| `LONGWATCH_INBOX_DIR` | *(unset = disabled)* | operator-designated scan directory |
| `LONGWATCH_INBOX_POLL_S` | `0` (off) | optional auto-scan cadence |
| `LONGWATCH_REVIEWER_NAME` | `operator` | attribution string |
| `LONGWATCH_OFFLINE` | `1` | install the socket guard |
| `LONGWATCH_AUTH_TOKEN` | *(unset = open)* | optional bearer gate; unset means every route is open and the trust-boundary text is shown on the Settings page |
| `LONGWATCH_DETECT_FPS` | `2.0` (clamp 0.5–5) | motion-active sampling rate |
| `LONGWATCH_STATIC_FPS` | `0.5` | sampling floor for low-motion windows |
| `LONGWATCH_STATIC_MOTION` | `0.02` | motion threshold for the downshift |
| `LONGWATCH_CONF_MIN` | `0.25` | detector confidence floor |
| `LONGWATCH_NMS_IOU` | `0.45` | per-class NMS overlap |
| `LONGWATCH_SCENE_T0` | `0.30` (range 0.15–0.60) | adaptive scene-cut start; early-exit below-band probe at 0.15 |
| `LONGWATCH_COVERAGE_S` | `300` | coverage grid interval |
| `LONGWATCH_WINDOW_MIN_S` / `_MAX_S` | `10` / `180` | window length clamps (hard invariants) |
| `LONGWATCH_PROXY_LONG_EDGE` | `640` | review proxy resolution |
| `LONGWATCH_MODEL_PATH` | `/opt/longwatch/models/yolov8n.onnx` | detector model |
| `LONGWATCH_MODEL_SHA256` | image-recorded | digest check at load; mismatch fails readiness (the entrypoint exports the digest the models stage recorded for the shipped file) |
| `LONGWATCH_CLASS_MAP` | `/opt/longwatch/models/coco80.json` | index→name map validated against the model contract |
| `LONGWATCH_BLUR_FACES` | `0` | export-time pixelation toggle |
| `LONGWATCH_FACE_MODEL_PATH` | `/opt/longwatch/models/yunet.onnx` | face detector (blur only) |
| `LONGWATCH_FACE_MODEL_SHA256` | image-recorded | face-model digest check at export load (same entrypoint rule) |
| `LONGWATCH_SIGN_KEY_PATH` | `$DATA/keys/ed25519.pem` | signing key, generated first start, mode 0600 |
| `LONGWATCH_UPLOAD_LIMIT_MB` | `4096` | upload ceiling (mid-stream 413 on breach) |
| `LONGWATCH_UPLOAD_CONCURRENCY` | `2` | concurrent-upload semaphore |
| `LONGWATCH_JOB_LEASE_S` | `120` | stale-lease reclaim threshold |
| `LONGWATCH_LEDGER_VERIFY_S` | `3600` | incremental re-verification cadence |
| `LONGWATCH_ORT_THREADS` | `1` | ONNX intra-op threads, sequential |
| `LONGWATCH_EXPORT_TOP_N` | `200` | candidates per export |
| `LONGWATCH_EXPORT_STATES` | `accepted,flagged` | default export scope |
| `LONGWATCH_JOB_POLL_MS` | `2000` | UI poll cadence |
| `LONGWATCH_QUEUE_LIMIT` | `50` (max 200) | queue page size |
| `LONGWATCH_FFMPEG_HANG_BASE_S` | `60` | hang-guard timeout base per ffmpeg invocation |
| `LONGWATCH_FFMPEG_HANG_FACTOR` | `4.0` | hang-guard timeout = base + factor × input-seconds |
| `LONGWATCH_STORE_FRAMES` | `0` | opt-in debug: bounded full per-frame detections |

### Model swap (all three variables together)

Set `LONGWATCH_MODEL_PATH` to the new ONNX, `LONGWATCH_MODEL_SHA256` to its
digest, and `LONGWATCH_CLASS_MAP` to a JSON array naming that model's output
classes in its declared order. Readiness re-validates the tensor contract
(`[1,3,640,640]` float32 in, `[1,4+N,M]` out), that the map covers N, and that
the eight retained class names exist in the map; any failure is a named, closed
failure surfaced by `/healthz` and `/api/v1/system/status` (the detector loads
on first use, so these are the readiness surfaces).

### Model license disclosure

The bundled detector weights are **AGPL-3.0** (an Ultralytics YOLOv8n COCO
export, `yolov8n.onnx`). This repository is MIT and ships a pointer, the
digest pins and a model card — never the weights. The Dockerfile's `models`
stage fetches them at image build time from their canonical public sources:

| artifact | canonical source | license |
|---|---|---|
| `yolov8n.onnx` | `github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.onnx` | AGPL-3.0 |
| `yunet.onnx` | `github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx` | Apache-2.0 |

`models/pins.json` carries those URLs plus the digest pins. A no-build-args
build therefore fetches the real weights; to make it **verify** them, run the
one pin command on a machine with network access and commit the file:

```sh
scripts/pin_models.sh --write      # fetches both artifacts, records their digests
docker build .                     # now verifies both pins; a mismatch fails the build
```

A build-arg overrides the file
(`--build-arg LONGWATCH_MODEL_SHA256=<digest>`, `…_FACE_MODEL_SHA256=<digest>`,
`--build-arg LONGWATCH_MODEL_URL=none` to disable the detector fetch, `…_FACE_MODEL_URL=none`
for the face model). A supplied digest that does not match **fails the build**.
Only a genuinely network-less build falls back to the in-repo contract-valid
fixture — a plain file copy of `tests/fixtures/model_fixture.onnx`, so the
fallback itself needs no network and no Python package index. The two fetches
are independent: a detector URL that cannot be reached never costs the build its
face model, and a face URL that cannot be reached leaves the detector in place
(pixelation then fails closed at export, as specified); a deliberate fixture-only
build passes `none` for both. The stage records which happened, and every surface
says so — `/api/v1/system/status`
reports `model.provenance`, the About panel appends the matching sentence, and
the image entrypoint exports the recorded digests so the detector and the face
model verify at load that what runs is what shipped:

| `MODEL_PROVENANCE` | meaning |
|---|---|
| `fetched` | the weights were fetched and verified against the digest pin |
| `fetched_unpinned` | the weights were fetched, but no published digest existed to verify them against; the fetched digest is recorded beside the model |
| `fixture` | the fetch could not run; the in-repo contract fixture is loaded and detections are placeholders |

---

## The pipeline

**Ingest → Signals → Detect → Score → Review.**

* **Windowing.** Scene cuts (early-exit bisection: one pass when the density is
  already in band, at most two on static-dominant footage, never more than four)
  unioned with a uniform grid every `LONGWATCH_COVERAGE_S`; boundaries closer
  than `WINDOW_MIN_S` drop the weaker one; spans longer than `WINDOW_MAX_S` are
  split at the midpoint. Coverage is guaranteed: the summed window durations
  equal the source duration within ±0.5 s and every window lies inside the
  clamps. There is no window-count cap knob — the count is what the grid and the
  clamps produce, and the job detail reports it.
* **Signals** are deterministic and cover the full timeline: motion, luma and
  persistence per second at 1 fps in integer milli-units, plus audio silence
  gaps and loudness spikes. One O(n) pass computes the nearest-rank percentile
  summary that the relative pattern semantics read.
* **Detection** runs on **every** window with motion-adaptive sampling — never a
  skip gate, because the flagship pattern is *animal stationary while person
  present*, and stationary content is exactly low-motion content.
* **Scoring** evaluates the three builtin patterns (and any you add) and writes
  one sentence per candidate quoting measured values. There is no verdict
  channel: the scoring layer emits an integer rank and one sentence, and the
  forbidden lexicon ("abuse", "cruel", "cruelty", "neglect", "suffering",
  "distress", "illegal", "unlawful", "violation", "torture") is enforced at
  runtime on every text the system accepts or generates — on pattern writes, on
  decision notes, and on the rendered sentence before a candidate is persisted.

### Patterns

Patterns live in Postgres as `(slug, version)` rows with an `is_current` flag.
An edit creates version N+1, is ledgered with the counts of decided candidates
carried over, and queues a **re-score job** — the request never evaluates rules.
Exports are **decision-scoped, not version-scoped**: any candidate with a
decided state stays export-eligible forever, so a routine rule tune can never
delete already-accepted evidence from a future bundle.

The three builtins are starting points:

1. `stationary_animal_with_person` — absolute semantics (animal presence ≥ 90 s
   at motion energy ≤ 0.150 while a person is present ≥ 30 s of the same span);
2. `sustained_motion_low_light` — per-source-relative semantics (motion above
   this recording's p85 for ≥ 30 s while luma is below its p10);
3. `long_silence_animals_present` — a silence gap ≥ 5 s with animal presence
   ≥ 60 s and no person detected. *"Person absent" is defined exactly*: no person
   interval overlaps any part of the qualifying span; non-detection is treated
   as absence for rule purposes, the sentence says "no person detected", and the
   About panel states that a detector miss would read as absence — which is why
   the detector-limits disclosure is permanent rather than conditional.

---

## Demo data — explicit, labeled, measured

**There is no automatic demo seeding.** Demo data appears only when the operator
clicks the Ingest page's demo button or calls `POST /api/v1/demo/seed`. The
action is idempotent.

The fixture is generated locally with ffmpeg's `lavfi` sources (no network, no
bundled binary media): 100 s of bright static content followed by 60 s of
darkened moving content, 160 s, no audio stream (which also exercises
`audio_absent`). The moving block is a sawtooth of period 120 px shifting 7 px
per frame, so every frame is a pure translation of the same pattern: the
per-second motion and luma of the second half are exactly constant, and the
`DEMO` scenario's assertion that `sustained_motion_low_light` fires with the
measured p85/p10 is a measurement rather than a hope. (The generator's
parameters are tuned to that measurement, as the specification requires.)

The flagship `stationary_animal_with_person` fires end to end through **labeled
synthetic injection**: the demo ingest job inserts `presence_intervals` rows with
`synthetic = true`. Every surface that touches them — the queue entry, the
candidate evidence, the CSV, the manifest — carries `synthetic: true`, the UI
shows a "demo · synthetic" badge, and the ledger records `DEMO_SEEDED` with the
fixture digest and the expected patterns.

---

## Export bundles

`POST /api/v1/exports {top_n, blur_faces, states, cut_precision}` produces one
deterministic ZIP:

```
manifest.json          human-readable rendering of the signed object
manifest.canonical     the exact bytes the signature covers
manifest.sig           the raw 64-byte Ed25519 signature (see below)
public.pem             the signing public key for THIS bundle
candidates.csv         rank order; decision_state, reviewer, src_viewed, synthetic columns
clips/                 evidence excerpts; cut_mode labeled per clip
ledger.jsonl           frozen transcript rows 1..ledger_head_seq
checksums.sha256       every file except itself
verify_bundle.py       stdlib verifier (hashlib, json, csv) + the in-repo Ed25519 check
ed25519_verifier.py    in-repo, MIT-licensed, verify-only RFC 8032 §5.1.7
VERIFY.md              re-verification instructions incl. one openssl command
README.txt             integrity statement, review-medium disclosure, time basis, model disclosure
```

`cut_precision='keyframe'` (default) stream-copies from the largest keyframe at
or before the requested start: the encoded frames are bit-identical to the
source's, and lead-in footage up to the previous keyframe is included — the
manifest labels the alignment. `cut_precision='exact'` re-encodes with actual
bounds equal to the requested bounds for span-precise, privacy-sensitive
exports; a re-encode is never labeled bit-identical.

**One signature encoding, raw bytes, everywhere.** `manifest.sig` holds the
**raw 64-byte** Ed25519 signature because `openssl` reads raw bytes and never
base64 — that is what makes the independent command below run verbatim. The
encoding is stated inside the signed document itself
(`detached_signature.encoding = "raw"` in `manifest.json` — named so the signed
object carries no field called `signature`; the signature lives in
`manifest.sig`), in this README, in the
bundle's `VERIFY.md` and `README.txt`, and in `verify_bundle.py`. The problem
statement's bundle-layout sketch annotates this file `(base64)`; that
annotation is superseded by this document and by the `openssl -sigfile`
command the same section requires to exit 0 — a third-party verifier must not
base64-decode the file first. `base64 -w0 manifest.sig` prints the text form,
and the base64 rendering is what the `exports` row's `signature` column stores.

Optional export-time face pixelation (`LONGWATCH_BLUR_FACES=1`) is a **new**
derived file: the YuNet face model runs on every excerpt frame and only the
detected faces are mosaicked at block 24, so the rest of the span keeps its
evidentiary content and the excerpt's audio track rides along unchanged. The
mosaic pass decodes and re-encodes the excerpt, so a blurred clip's frames are
not bit-identical to the source's — the bundle's own README.txt says exactly
that. The clip's `transform` records the operation, the block
size and the counts the model produced (`regions_pixelated`,
`frames_processed`, `max_faces_per_frame`), and both the `TRANSFORM_APPLIED`
ledger event and the signed manifest's clip entry carry the face model's
basename and digest — the record states what the model did, not
what the option is called. Requested without a usable face model (or with one
whose declared heads fail the contract check), the export **fails closed with a
named error** — never a silent skip.

---

## Ledger verification (third parties)

### The canonicalization rule — integer-only and byte-portable

Permitted types in any hashed payload or signed manifest document: `null`,
booleans, integers within int64, strings, arrays, objects. **Floats are
forbidden** — `ledger.append` and the manifest builder validate recursively and
raise on any float. Every quantity that was a float elsewhere in the system is
an integer in milli-units here: time spans in milliseconds, normalized 0–1
values and confidences in thousandths, percentiles in percent.

The canonical form: object keys are ASCII and sorted by code point; no
whitespace between tokens; separators are `,` and `:`; strings are UTF-8 with
RFC 8259 minimal escaping (`"` `\` `\b` `\t` `\n` `\f` `\r`, other U+0000–U+001F
as lowercase `\u00xx`, nothing else escaped).

That is exactly what `json.dumps(obj, sort_keys=True, separators=(",", ":"),
ensure_ascii=False)` produces for this type set, and what default encoders
produce in JavaScript, Go and Rust once keys are sorted. **Portability is
proven, not asserted:** `tests/fixtures/canonical_vectors.json` ships twelve
vectors (nested objects, arrays, empty containers, non-ASCII strings, strings
requiring every escape class, boundary integers) and the `CANON` scenario
reproduces every one of them in Python **and** through an independent ~30-line
canonicalizer written in JavaScript and executed under Node.

Rounding happens **once**, at computation time, by integer arithmetic
(round-half-even on the underlying fixed-point computation); the `real` columns
in the schema are renderings for query convenience and are never hash inputs. No
verifier ever rounds anything.

### The byte-exact hash preimage

```
row_hash = sha256(UTF-8 bytes of
  "longwatch-ledger-v1|" + decimal(seq) + "|" + prev_hash + "|" +
  event_type + "|" + actor + "|" + decimal(recorded_at_ms) + "|" + canonical_json)
```

`decimal(x)` is the base-ten rendering of the non-negative integer `x` — no
sign, no padding, no separators, no exponent. `recorded_at_ms` is the stored
integer column, so a verifier needs no timestamp parsing, no float formatting
and no rounding decisions. Row `N` carries `prev_hash = row_hash(N−1)`; the
genesis row's `prev_hash` is 64 zeros. The genesis timestamp is
deployment-specific **and travels in `ledger.jsonl` like every other row** —
verification requires no out-of-band constants. A re-deployment against an empty
volume starts a new ledger; old bundles remain verifiable from their own
transcripts.

### Verifying a bundle

From the bundle alone — no LongWatch install, no database, no trust in the
vendor:

```sh
python3 verify_bundle.py .        # exit 0 pass, 1 fail, 2 signature not checked

sha256sum --check checksums.sha256

openssl pkeyutl -verify -pubin -inkey public.pem -rawin \
    -in manifest.canonical -sigfile manifest.sig
```

The `openssl` command requires **OpenSSL ≥ 3.0** (Ed25519 `-rawin`). Note the
option is `-sigfile`; `openssl pkeyutl` does not accept `-sig`.

`verify_bundle.py` checks that every file digest matches the signed manifest's
`files` map — **including both verifier scripts**, so a tampered verifier is
detectable — that `manifest.json` re-canonicalizes byte-equal to
`manifest.canonical`, that the signature verifies, that the `ledger.jsonl` chain
walks clean, that the CSV agrees with the manifest, and that `checksums.sha256`
recomputes.

`ed25519_verifier.py` is authored **in this repository under its MIT license**
as a verify-only implementation of RFC 8032 §5.1.7; no third-party code is
vendored, so no third-party license applies. It is tested against the RFC 8032
§7 test vectors and cross-checked against the `cryptography` package.

### The trust root, stated honestly

The signature proves the bundle's contents are exactly what the signing key
signed and have not changed since. It does **not** prove the key belongs to the
operator — no offline bundle can prove that. **Compare the key fingerprint**
(SHA-256 of `public.pem`, shown in Settings and printed at the startup log) with
the operator over a channel of your choosing before trusting anything.

### Key persistence

The Ed25519 keypair is generated on first start into
`$LONGWATCH_DATA_DIR/keys/ed25519.pem` (mode 0600, data volume only). **Persist
the data volume**: losing the key invalidates future signatures, never past ones
— every past bundle carries its own key and stays verifiable. After any key
rotation, re-share the new fingerprint with recipients.

---

## Trust boundary and token mechanics

With `LONGWATCH_AUTH_TOKEN` unset, **anyone who can reach the port is the
operator**. LongWatch v0 is a single-operator offline appliance; deploy it on
loopback or behind an operator-controlled proxy if a boundary is needed.

The optional token is an accident guard, not access control. It is compared with
`secrets.compare_digest` (constant time) and accepted as
`Authorization: Bearer <token>` on every `/api/*` route and — solely on the two
byte-range media routes (`/api/v1/clips/{id}/proxy` and
`/api/v1/sources/{id}/original`), because `<video>` elements cannot send
headers — as a `?token=` query parameter. **Every response**, including the SPA
document and both media routes, carries `Referrer-Policy: no-referrer`, and the
SPA sets the matching `<meta name="referrer" content="no-referrer">`, so the
URL-borne token cannot leak through a `Referer` header. `/healthz` and
`/openapi.json` stay open. On the first 401 the SPA renders a token-entry field
and stores the token in `localStorage`.

## Egress posture

The running container initiates **zero outbound connections other than DNS
resolution of the configured `DATABASE_URL` hostname** — the single sanctioned
egress, exercised once at startup (a literal-IP DSN eliminates even that). A
startup socket guard makes any non-allowlisted `connect`, `connect_ex`,
`sendto`, `sendmsg` or `getaddrinfo` a loud crash before a DNS packet leaves the
machine: a runtime egress bug is a crash, not a leak. `bind`/`listen`/`accept`
are untouched so the server can serve. The SPA bundles all assets locally — no
CDN references.

## No capture path

There is no RTSP listener, no device control and no always-on watcher in this
repository. The only inputs are a browser upload and an operator-invoked scan of
a configured directory. Originals are read-only: no code path renames, moves,
re-encodes, truncates or deletes an original, and there is no API or CLI path
that deletes a source row. The retention purge (`POST /api/v1/retention/purge`)
deletes LongWatch's own derived artifacts — proxies, excerpts, exports — and
writes a ledger event per deletion.

## Secrets

`DATABASE_URL` (and optionally `LONGWATCH_AUTH_TOKEN`) are read from the
environment. No credential-shaped file is ever written into the source tree, and
no secret value appears anywhere in this repository.

## License and author

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 the Sneferu Open Builds
program. The bundled detector weights are **not** part of this repository: they
are AGPL-3.0 (Ultralytics YOLOv8n) and are fetched at image-build time; see
*Model license disclosure* above.
