# LongWatch — product guide

For the person who reviews footage with LongWatch. This guide takes you from the
first URL you open to a signed bundle you can hand to someone else.

If you install and run the service yourself, start with the
[README](../README.md) quick start first, then come back here.

---

## 1. What LongWatch is, in one paragraph

LongWatch reads video that already exists on a drive, produces a **ranked queue
of spans a person should examine**, attaches a tamper-evident custody record to
every artifact it derives, and stops. It is not a case-management suite, not an
abuse classifier, and not a surveillance product. The strongest sentence it will
ever produce is: *a person should look at this span, and here are the measured
reasons.*

---

## 2. Opening LongWatch

Open the address your operator gave you — by default:

```
http://localhost:8000
```

You land on the **Ingest** page. The header shows the six sections (Ingest,
Triage, Patterns, Exports, Settings, About) and a small badge that reads
`db ok · model ok` when the service is healthy. A thin strip under the header
always shows the detector-limits disclosure — that strip is permanent on
purpose.

### Signing in

**There is no login.** LongWatch has no user accounts and no passwords. One
reviewer identity is configured by the operator (`LONGWATCH_REVIEWER_NAME`,
default `operator`), and it is stamped on every decision you record. That is
attribution, not access control.

**If the deployment asks for a token** — a banner appears with a password field
on your first action — the operator has set `LONGWATCH_AUTH_TOKEN`. Paste the
token the operator gave you and click **Use token**. It is stored in your
browser's local storage, sent as a header with every request, and attached to
video URLs. There are no default or development credentials; if you were not
given a token, the deployment does not use one.

**If you do not have a token and the banner keeps appearing**, ask the operator
for it — the API will not answer without it. Do not guess.

---

## 3. Your first ten minutes

If the database is empty, the Ingest page offers one button: **Create demo
fixture (labeled synthetic)**. Click it to see the whole product work on
synthetic footage. Everything it produces is marked `demo · synthetic`
everywhere it appears — in the queue, in the evidence, in the CSV, and in the
manifest. It is a rehearsal, not evidence.

Wait for the source row's state to become `ready` (a few seconds; the row shows
`N pending` while the ingest job runs).

---

## 4. The primary journey: footage in, signed bundle out

Follow these steps in order. Each one names what you should see when it worked.

**Step 1 — Get footage in.**

On **Ingest**, either:

- click **Upload**, choose a video file, and click **Upload** again; or
- click **Scan inbox** to pick up files the operator placed in the configured scan directory; or
- click **Create demo fixture (labeled synthetic)** if you are just practicing.

*What you should see:* a message like `ingest queued (job 6f1c…)`, and a new row
in the **Sources** table whose **state** progresses `registered → windowed →
proxied → analyzed → scored → ready`. The `windows` column fills in with the
number of windows LongWatch cut, and `jobs` shows `N pending` until the ingest
finishes.

If the file is one LongWatch has already seen, you get
`duplicate digest — registered as source …` — nothing is copied twice, and the
sighting is recorded in the ledger.

**Step 2 — Open Triage and read the queue.**

Click **Triage**. The left pane is the ranked queue. Two kinds of rows appear:

- **Ranked candidates** — a score badge, the pattern that fired, and one sentence of measured reasons, for example:
  `dog presence held 100s at motion energy <= 0.009 (tolerance 4s) while a person was present 70s of the same span`
- **Detection-incomplete spans** — pinned *above* everything else with a `detection incomplete` badge. These are spans the machine could not look at. They are deliberately first: a span nobody could rank must never hide behind spans that were ranked.

Click a row to load it in the right pane. You will see the sentence, the review
proxy player, the evidence block (every measured number behind the sentence,
in milli-units), and the decision history.

**Step 3 — Look at the pixels that will ship (recommended).**

The player shows a **lossy working copy** — that is what the machine ranked, but
it is not what ships. Press `v` (or click **Source view**) to open a second
pane streaming the **original file itself**. Judge the original before you
decide. Opening this pane sets `source viewed: true`, and that fact travels into
the decision record, the CSV, and the manifest — so whoever receives the bundle
can see which decisions were made on the original and which on the working copy.

If the original's codec cannot play in a browser, the pane says so and shows you
the file's storage path so you can open it locally.

**Step 4 — Decide.**

With a candidate selected:

- press `a` or click **Accept** — this span deserves the recipient's attention;
- press `r` or click **Reject** — it does not;
- press `f` or click **Flag** — you want to record a note. Pressing `f` opens the note field first: type observables (durations, class presences, motion magnitudes, loudness deltas) and press `Enter` to save. Clicking **Flag** records immediately, with whatever the note field currently contains.

*What you should see:* `accept recorded · ledger seq 13 · src_viewed true` (or
`reject` / `flag`). The decision is saved immediately, is appended to the
custody ledger, and appears in the decision history. You can keep moving with
`j` / `k` — your decision is already durable.

Notes are checked against a fixed lexicon. If a note contains a term like
"abuse", "neglect" or "cruelty", the save is refused with a message naming the
term. Rewrite the note to describe what you measured, not what you concluded.

**Step 5 — If a span says "detection incomplete".**

Select it and read the reason. Then click **Acknowledge (x)** — or press `x` —
to record that you reviewed the failure. Acknowledged spans leave the pinned
set. (If the button answers `window_not_failed` or `already_acknowledged`, the
span was already cleared — refresh and continue.)

**Step 6 — Cut the export.**

Click **Exports**. Choose which decision states to include (the defaults —
`accepted` and `flagged` — are what the operator configured), the cut precision
(`keyframe` keeps the source frames bit-for-bit and may include lead-in footage;
`exact` re-encodes so the clip covers precisely the span you saw), and whether
to pixelate faces (off unless your deployment turned it on).

Click **Create export**.

*What you should see:* `export 958bc256… queued (job 12d2…)`, and a row in the
**Exports** table whose job column walks `queued → running → succeeded`. When
the row offers **download ZIP**, the bundle is ready.

**Step 7 — Download and check the bundle.**

Click **download ZIP**. You get a file like
`longwatch-export-958bc256.zip`.

Anyone — including someone who has never seen LongWatch — can verify it:

```sh
unzip longwatch-export-958bc256.zip
cd export_20261001T130609Z_958bc256
python3 verify_bundle.py .        # exit 0 = verified
```

Exit 0 means every file in the bundle is byte-identical to what was signed, the
signature is valid, and the custody record inside is intact. The bundle also
contains `VERIFY.md` with the same instructions and an independent OpenSSL
command.

**One thing verification does not prove:** that the signing key belongs to the
operator. Compare the fingerprint shown on LongWatch's **Settings** page (and
printed in the server's startup log) with the operator over a channel you
already trust. The bundle prints the same fingerprint.

**Step 8 — Hand it over.**

Send the ZIP. Its `README.txt` states, in plain language, exactly what the
bundle's digests do and do not show, and that triage decisions were rendered on
a lossy working copy with the source-resolution view available and recorded.

---

## 5. Reading the disclosure strip and the About page

The strip under the header carries the detector-limits disclosure: the bundled
detector was trained on general COCO imagery, farm/wildlife/night/overhead
performance is unverified, and detections are coarse ranking inputs — not
findings. If the deployment is running the in-repo fixture instead of the real
weights, the strip says so explicitly.

The **About** page holds the full set of disclosures, verbatim: the integrity
statement (what the digests prove), the time basis (all timestamps are
server-local processing times, never capture times), the review-medium
disclosure, the detector limits, the person-absent rule (non-detection is
treated as absence for rule purposes; a detector miss would read as absence —
which is why the limits disclosure is permanent), and the trust boundary.

**In-product help:** the `?` key in Triage toggles the keyboard-shortcut panel;
the same legend is always printed under the detail pane. Everything else you
need is on the page it applies to — buttons carry their keys (`Accept (a)`),
and refusal messages name their reason.

---

## 6. Restart and recovery

**Restarting the service.** LongWatch keeps all state in its database and its
data volume. You (or the operator) stop and start the server — or restart its
container — and nothing about your review is lost:

1. Reopen the address. If the deployment uses a token, your browser remembers it; if the banner appears anyway, paste the token again (**Use token**).
2. Check the header badge: `db ok · model ok`.
3. Open **Triage** — your queue and every decision are exactly where you left them.
4. Open **Settings** and click **Verify chain now**. You want `ok=true`. If it says `ok=false`, stop and tell the operator immediately — that is the one condition the product must never have silently.

**A job that was running when the service stopped.** LongWatch reclaims jobs
whose worker stopped heartbeating (within two minutes by default) and continues
them from their last recorded stage. You do not need to re-upload or re-create
anything. If a job shows `failed`, its error text names the cause.

**You closed the browser mid-decision.** Decisions save the moment you press
`a`/`r`/`f`; there is nothing to resume. Reload the page and the history shows
what you recorded.

**An original file was moved or deleted outside LongWatch.** The source row
turns `missing` with the reason `source file absent at stage …`. LongWatch never
moves or deletes originals; restore the file at the storage path shown in the
row (or in the Settings/About views) and re-run ingest.

---

## 7. Common messages and the exact recovery

| You see | What it means | Do this |
|---|---|---|
| `401` / the token banner appears | The deployment requires a bearer token and it is missing or wrong. | Paste the operator's token and click **Use token**. If you have no token, ask the operator. |
| `413: {"error": "upload_too_large", "limit_mb": 4096}` | The file exceeds the configured upload ceiling. | Ask the operator to raise `LONGWATCH_UPLOAD_LIMIT_MB`, or use the inbox scan path instead (files are registered in place, with no upload ceiling). |
| `400: {"error": "inbox_disabled"}` | No scan directory is configured. | Ask the operator to set `LONGWATCH_INBOX_DIR`; until then, upload. |
| `400: {"error": "inbox_missing"}` | The configured scan directory is not a directory. | Check with the operator that the path exists and is mounted. |
| `vocabulary_breach: the term "…" is not permitted — <guidance>` | Your note (or pattern text) contains a term from the fixed forbidden lexicon. | Rewrite the note describing measured observables — durations, class presences, motion magnitudes, loudness deltas — rather than conclusions. |
| `template_error: unknown placeholder {…}` | A pattern sentence template names a placeholder that does not exist. | Use only: `{animal} {class} {dur} {motion} {tol} {pdur} {gap} {value} {p} {samples}`. |
| `rule_error: …` | A pattern rule failed validation; the message names the problem. | Fix the named condition; class names are limited to the eight retained classes and groups to `animal` / `person` / `vehicle`. |
| `window_not_failed` or `already_acknowledged` (409) | The detection failure was already cleared. | Refresh the queue — the span has left the pinned set. |
| Export row shows `failed` with `face pixelation was requested but no usable face model` | Face pixelation was requested but the deployment has no usable face model. It refuses rather than silently shipping un-pixelated footage. | Untick **pixelate faces**, or ask the operator to provide a face model. |
| Export row shows `failed` with `source … digest mismatch` | The original file changed on disk after LongWatch read it. | Stop and tell the operator. The ledger holds the digest LongWatch recorded; investigate before re-ingesting. |
| Export row shows `failed` with another message | The export hard-failed; the reason is named. | Read the error text — it names the file or condition. |
| `spa_not_built` (503) on every page | The web interface was never built on this deployment. | Operator action: run `scripts/build_spa.sh` (or build the Docker image, whose build does it). |
| Header badge shows `model down` | The detector failed its contract check. | The API is up but cannot rank. Tell the operator; `/healthz` names the cause (missing file, digest mismatch, wrong tensor shape). |

---

## 8. What LongWatch will not do

So you are never surprised in a review:

- It will not tell you a span is *bad*. It ranks spans and gives measured
  reasons; the judgment is yours.
- It will not delete or alter your originals. Ever. The only deletions are of
  LongWatch's own derived files, when the operator runs a retention purge, and
  each deletion is recorded.
- It will not send anything anywhere. The only network connection it makes is
  to its own database.
- It will not record or capture video. There is no camera, no stream listener,
  no watcher. The only inputs are the ones you upload or scan.
- It will not give you a login, roles, or sharing. It is a single-operator
  appliance; hand over evidence as a bundle.

---

## Where to next

- [README](../README.md) — what the project is and how to run it.
- [API reference](API.md) — if you are integrating with the API.
- [Operations guide](OPERATIONS.md) — if you run the service.
- [UI reference](UI.md) — every screen, state, and keyboard shortcut.
- [Architecture](ARCHITECTURE.md) — how it is built and why.
