# DESIGN — LongWatch

The design contract for the LongWatch SPA. `SOUL.md` is the anchor; this file is
the translation of that anchor into tokens, states, and rules. If a decision
here contradicts `SOUL.md`, `SOUL.md` wins and this file is wrong.

## 0. Soul

One sentence: **a phosphor readout bench for footage** — dense, dark, exact, with
one amber lamp that belongs to the flag (see `SOUL.md`).

## 1. Reference anchor

**Reference (one, not three):** Bloomberg Terminal —
<https://www.bloomberg.com/professional/products/bloomberg-terminal/>

The Terminal is a licensed appliance and is not publicly viewable, so the
patterns below are cited from its publicly documented and photographed design
(press imagery, the product page, and the terminal's own documentation of its
keyboard contract) rather than from a session. I name that limitation rather
than pretend to a screenshot I did not take.

**Why this one.** Dana's hour-14 problem is the same problem a trader's hour-14
problem is: thousands of rows, a keyboard, no patience for chrome, and a
decision that must be defensible afterwards. The Terminal is the shipping
product that solved that problem by refusing to decorate: hairline panels, one
signal colour, a printed key legend, and a value that never appears without its
unit. That is exactly the posture LongWatch needs — an instrument that a
professional learns once and then stops noticing — and it is the opposite of the
consumer-video and security-ops looks that this product's domain would otherwise
attract.

### Five pixel-level patterns we mirror

1. **The function-key legend strip.** Every Terminal panel carries a bottom edge
   strip of ~10 px uppercase key mnemonics, always visible, one per column, on
   the panel itself — the machine's keyboard contract is printed on the machine
   and is never a tooltip. *Mirrored:* the workbench action bar carries a
   persistent key legend (`j/k` `space` `a` `r` `f` `n` `v` `x` `?`) rendered as
   20 px-high bordered keycaps with 11 px uppercase labels, on the panel edge,
   visible in every state.
2. **Hairline panel grid, no elevation.** Panels are separated by exactly one
   hairline on near-black; the Terminal has no drop shadows, no rounded corners,
   no frosted surfaces. Depth is expressed by hairline weight and text colour.
   *Mirrored:* `--border-subtle` is the only separator on panels, tables, and
   rows; radius is ≤ 6 px everywhere except pills; `--shadow-*` exists for true
   overlays (modal, toast, popover) and is forbidden on panels and rows.
3. **Signal colour on black, with unit-bearing headers.** Data is rendered in a
   single warm signal colour on black; column headers carry the unit and the
   code, and numeric columns are right-aligned with tabular numerals. *Mirrored:*
   every numeric cell uses `--font-mono` + `font-variant-numeric: tabular-nums`;
   every column header names its unit (`ms`, `s`, `%`, `milli`); the amber
   `--accent` is the only saturated hue on a data surface besides the three
   status hues.
4. **Dense rows with no per-row chrome.** Rows are separated by hairlines or a
   faint alternating tint; there is no per-row button, no card, no avatar, no
   chevron. Actions are keyboard or panel-level. *Mirrored:* queue rows and
   table rows carry no inline buttons; the selection indicator is a 2 px amber
   bar at `left: 0` plus a surface tint — never a floating card.
5. **The "as of" stamp.** Every Terminal panel header states its data timestamp
   and source; nothing on screen is timeless. *Mirrored:* every panel header
   carries a freshness stamp — last poll time, job `pct`, or the ledger head
   seq — so an operator can always tell how stale a reading is.

### Three deliberate divergences, with the soul reason

1. **Amber is a signal, not the body text.** The Terminal renders everything in
   its signal colour; we keep body text at a warm paper-white and reserve amber
   for attention, focus, selection, and the flag. *Reason:* `SOUL.md` ¶2 — "the
   brightest colour in the room belongs to the flag"; if prose were amber, the
   flag would be indistinguishable from a sentence, and hour-14 legibility would
   suffer.
2. **Rows are 40 px, not 18 px.** The Terminal optimises for hundreds of pure
   number rows; our queue rows carry a full sentence of prose — the measured
   reason, which is the product's evidence — and must be a real hover/focus/touch
   target. *Reason:* `SOUL.md` ¶1 — Dana has to be able to read the reason at a
   glance and defend it later; the sentence is the payload, not a ticker line.
3. **No command grammar.** No `GO`, no function codes, no `⌘K` palette. The key
   legend covers one screen's flow and nothing else. *Reason:* the product has
   six screens and one keyboard flow; inventing a command language would imply
   capabilities the backend does not have, and the hard rule is that no control
   ships unless a real route backs it.

## 2. Density

**Dense.** Row height 40 px (queue 56 px — it carries a sentence), table rows
36 px, panel padding 12–16 px, page gutters 24 px desktop / 12 px mobile. The
only generous surface is About, which is prose and is allowed to breathe.

## 3. Color tokens

The closed layer is `frontend/src/styles/tokens.css`. Dark is the product
default (video work happens in dark rooms; the player is the brightest object on
screen). Light is a designed peer — warm paper, ink text — not a filter.

| Role | Dark | Light |
|---|---|---|
| `--bg-base` | `#0c0d0f` | `#f4f2ed` |
| `--bg-elevated` | `#141519` | `#fcfbf8` |
| `--bg-overlay` | `#1b1d22` | `#ffffff` |
| `--bg-inset` | `#08090a` | `#eae7e0` |
| `--fg-primary` | `#e9e6df` | `#1a1b1d` |
| `--fg-secondary` | `#a9a69e` | `#4c4a46` |
| `--fg-tertiary` | `#8a8780` | `#5f5c56` |
| `--fg-disabled` | `#5a5852` | `#a5a29b` |
| `--accent` | `#e2a44f` | `#7a5210` |
| `--accent-hover` | `#f0b768` | `#6a4710` |
| `--accent-pressed` | `#c88c3b` | `#573a0c` |
| `--accent-ink` (text on an accent fill) | `#1a1305` | `#ffffff` |
| `--border-subtle` | `#22242a` | `#e4e0d7` |
| `--border-default` | `#2e3138` | `#d2cdc2` |
| `--border-strong` | `#4a4e58` | `#a8a294` |
| `--border-control` | `#6b6f7a` | `#8f8a7d` |
| `--status-open` | `#8a8780` | `#5f5c56` |
| `--status-running` | `#e2a44f` | `#7a4e0e` |
| `--status-paused` | `#8fa3b0` | `#41616f` |
| `--status-halted` | `#d5814c` | `#8c4a22` |
| `--status-complete` | `#7fb069` | `#2f6b3e` |
| `--status-failed` | `#e06a4e` | `#a33b26` |
| `--status-orphan` | `#8a8780` | `#5f5c56` |
| `--status-accepted` | `#7fb069` | `#2f6b3e` |
| `--status-rejected` | `#8e8b84` | `#5f5c56` |
| `--status-flagged` | `#e2a44f` | `#7a4e0e` |
| `--status-ml-failed` | `#e06a4e` | `#a33b26` |
| `--trace-motion` | `#e2a44f` | `#7a5210` |
| `--trace-luma` | `#7c7a74` | `#7c7a74` |
| `--trace-presence` | `rgba(226,164,79,.22)` | `rgba(122,82,16,.18)` |
| `--trace-audio` | `#8fa3b0` | `#41616f` |
| `--trace-grid` | `#1e2024` | `#e0dcd3` |

**Control boundaries vs separators.** `--border-control` is the boundary of an
interactive control (input, select, textarea, button, the segmented control, the
dropzone) and clears 3:1 against every surface it sits on, per WCAG 2.2 SC
1.4.11. `--border-subtle` / `--border-default` / `--border-strong` are structural
separators — panel edges, row rules, table rules — and are deliberately quiet:
they are never the only thing that identifies a control, and the audit reports
them as informational rather than as a pass/fail pair.

Every text/background pair above is checked against WCAG 2.2 AA (≥ 4.5:1 normal
text, ≥ 3:1 large text and UI boundaries) in both themes by
`frontend/scripts/contrast-audit.mjs`, which parses `tokens.css`, composites the
alpha tints, and prints a pass/fail table. Status is never carried by hue alone:
every status token is rendered with an icon glyph or a text label beside it.

## 4. Type tokens

Two families, both self-hosted (bundled into the build — no CDN, per the
product's egress posture):

- `--font-display` / `--font-body`: **IBM Plex Sans** — an engineered grotesque
  with slightly humanist terminals; it reads as instrument labelling, not as
  marketing.
- `--font-mono`: **IBM Plex Mono** — used for every ID, digest, timecode,
  timestamp, count, and unit-bearing number.

Scale (ratio ≈ 1.2): `--text-xs` 11 px · `--text-sm` 12 px · `--text-base`
14 px · `--text-lg` 16 px · `--text-xl` 20 px · `--text-2xl` 24 px ·
`--text-3xl` 30 px. At ≤ 640 px `--text-base` becomes 16 px so mobile inputs
never trigger iOS auto-zoom. Only the Latin subsets of both families are
bundled — the interface is English, and shipping Cyrillic/Latin-Ext would put
megabytes into the image for nothing.

Weights: 400 / 500 / 600 / 700. Line heights: `--line-tight` 1.2,
`--line-snug` 1.35, `--line-normal` 1.5, `--line-relaxed` 1.65.

Hierarchy is always built from three differences at once — size **and** weight
**and** colour — never size alone.

## 5. Spacing tokens

4 pt scale: `--space-1` 4 · `2` 8 · `3` 12 · `4` 16 · `5` 20 · `6` 24 · `7` 28 ·
`8` 32 · `10` 40 · `12` 48 · `16` 64. Every gap, padding and margin in the app
lands on this scale; a section's `margin-bottom` is always larger than its
`margin-top` so vertical rhythm flows downward.

## 6. Component states

| Component | default | hover | focus-visible | active/pressed | disabled | loading |
|---|---|---|---|---|---|---|
| `.btn` (secondary) | `--bg-overlay` fill, 1 px `--border-default`, `--fg-primary` | border `--border-strong`, bg lifts one step | 2 px `--border-focus` ring, 2 px offset | bg `--bg-elevated`, text `--fg-secondary` | 45 % opacity, `cursor: not-allowed`, `title` carries the reason | label swaps to `Working…`, `aria-busy`, pointer blocked, no layout shift |
| `.btn-primary` | `--accent` fill, `--accent-ink` text | `--accent-hover` | ring | `--accent-pressed` | 45 % opacity | as above |
| `.btn-danger` | transparent fill, `--status-failed` border+text | fill `rgba(210,89,63,.12)` | ring in `--status-failed` | fill 20 % | 45 % | as above |
| `.btn-ghost` | transparent, `--fg-secondary` | bg `--bg-overlay` | ring | bg `--bg-elevated` | 45 % | as above |
| `input` / `select` / `textarea` | `--bg-inset`, 1 px `--border-default` | border `--border-strong` | 2 px ring + border `--border-focus` | caret + text `--fg-primary` | 45 %, no pointer | n/a (inline `…` in the field label) |
| `.chip` (state badge) | tinted 12 % fill + 1 px tinted border + glyph + label | n/a (not interactive) | n/a | n/a | n/a | n/a |
| `.row` (queue/table) | transparent | bg `--bg-overlay` | inset 2 px ring, no offset | bg `--bg-elevated` | n/a | skeleton row (shimmer) |
| `.panel` | `--bg-elevated`, 1 px `--border-subtle`, radius 6 px, no shadow | n/a | n/a | n/a | n/a | skeleton body |
| `.modal` | `--bg-overlay`, 1 px `--border-default`, `--shadow-3`, backdrop `rgba(0,0,0,.55)` + blur 6 px | n/a | focus trapped inside | n/a | n/a | n/a |
| `.toast` | `--bg-overlay`, `--shadow-2`, 1 px border tinted by kind | dismiss button lifts | ring on the dismiss button | n/a | n/a | n/a |
| `nav` item | `--fg-secondary`, transparent | text `--fg-primary`, bg `--bg-overlay` | ring | bg `--bg-elevated` | n/a | n/a |
| `.tab`/segmented | transparent, `--fg-secondary` | text `--fg-primary` | ring | — | 45 % | — |
| active nav / tab | `--bg-overlay` fill + 1 px `--border-default` + 2 px amber bar | — | — | — | — | — |

**Document outline.** One `h1` per page; each panel title is an `h2`; a data
section inside a panel is an `h3` (Triage: "Measured evidence", "Decision
record"). Captions that label a table or a value block are not headings — the
Ingest source detail's "Signal percentiles" line is a `span.field-label`
caption, so the outline descends h1 → h2 with no skipped level. The Triage
bench is a companion region, not a child of "Ranked queue": its candidate panel
carries an `sr-only` `h2` ("Workbench") so heading navigation reaches the pane,
while the empty and detection-incomplete states carry visible panel titles.

**Radius assignment.** `.filter-chip` — a control you press — is a pill
(`--radius-pill`); `.chip` — a state readout you read — keeps `--radius-sm`.
The pill marks interactivity; the small radius matches the ≤ 6 px panel rule.

**Queue rows and the keyboard.** The ranked queue is one `role="listbox"` with
`aria-activedescendant`; rows are native `<button role="option">` elements with
`tabIndex={-1}`, so the listbox keeps the single tab stop while each row has
native activation (Enter/Space select it, a click selects it). Space stays the
transport key on a focused row; a focused player owns the arrow keys natively
(seek/volume) and j/k keep moving the queue there. The selected row is scrolled
into view only when the selection actually moves: `scrollIntoView` moves the
browser's sequential-focus starting point, so firing it on mount or on a poll
tick would steal the first Tab stop from the header and re-steal it mid-session
whenever the queue length changed.

**Queue pagination.** The head page (the top 100 of the ranking) polls on the
2 s cadence and is always live. Pages beyond the head load only on demand —
a quiet full-width "Load the next 100 — _n_ of _N_ candidates shown" button at
the foot of the queue scroll — and are snapshots: a re-score can re-rank them,
but the list never re-orders under the cursor. The merge dedupes on the window
identity (the server's ml_failed pins ride every page but render only from the
head), and a decision recorded on a tail row patches that row's state chip in
place rather than refetching the tail.

Four lifecycle states on every async surface: **skeleton** (shape-matched, never
a spinner), **empty** (tells the operator the next action, never "no data"),
**error** (names what failed, where, and the recovery), **success** (the real
result, with its units).

## 7. Motion

Durations: `--dur-instant` 50 ms · `--dur-fast` 120 ms · `--dur-base` 200 ms ·
`--dur-slow` 320 ms. Easings: `--ease-default` `cubic-bezier(.4,0,.2,1)`,
`--ease-emphasized` `cubic-bezier(.2,0,0,1)`. Only `transform` and `opacity`
animate; `width`/`height`/`top`/`left` never do.

- **The progress fill is the worked example of that rule.** The track is the
  layout box; the fill is a static `width: 100%` bar whose fraction lives in
  `transform: scaleX(pct/100)` with `transform-origin: left`, so the upload,
  rescore and assembly bars interpolate on the compositor and never relayout.
  The unknown-progress variant keeps its static `width: 40%` and travels with
  `translateX` keyframes. `frontend/scripts/token-audit.mjs` fails the build on
  any transition or keyframe that animates a geometry property, so this cannot
  regress silently.

- **Choreographed entrance** (page mount): header 0 ms → disclosure 40 ms →
  first panel 80 ms → remaining panels 120 ms; each 200 ms, `translateY(4px)` +
  opacity, no bounce.
- **Queue re-rank:** the list never re-orders under the cursor. New ranks are
  applied on the next explicit navigation (keypress or click); a changed row
  fades its score cell once (`--dur-slow`) instead of moving.
- **Skeletons, not spinners**, wherever the layout is known (queue, tables,
  panels). A spinner is permitted only inside a button's loading state.
- `prefers-reduced-motion: reduce` collapses every transition and animation to
  0.01 ms and removes the entrance stagger.

## 8. Voice and copy

**Voice:** a lab technician who has read the spec — exact, unit-bearing, second
person, never a verdict, never a joke, no exclamation marks. The interface says
what it measured and what it did; it never says what anything *means*.

Three empty states:
- Ingest, no sources: "No footage registered yet. Upload a file, scan the inbox
  directory, or create the labeled demo fixture to see the pipeline end to end."
- Triage, empty queue: "The queue is empty. Candidates appear here as soon as a
  source finishes scoring — ingest footage first."
- Exports, none: "No bundles yet. Choose the decision states to ship and create
  the first export; every bundle carries its own key and ledger."

Three error strings:
- "Upload failed: the connection dropped mid-stream. The partial file was
  discarded and nothing was registered."
- "The template names a placeholder this pattern's rule does not measure, so no
  sentence can be rendered from it."
- "“distress” is a conclusion term and cannot be recorded. Describe what you
  observed instead: durations, class presences, motion magnitudes, loudness
  deltas."

Three button labels: "Upload and hash" · "Save as v{n+1} and re-score" ·
"Create signed bundle".

Never: "Oops", "Loading magic", "Welcome back", "Are you sure?" (a destructive
confirm states the exact consequence instead), or any conclusion vocabulary
("suspicious", "abnormal", "alert") — the vocabulary guard that governs the
product's data governs its copy too.

## 9. Anti-defaults forbidden in this project

From `SOUL.md` ¶3, plus the project-specific list:

1. No security-ops furniture: threat map, radar sweep, scanning reticle, neon
   cyan, "THREAT LEVEL", globe.
2. No SaaS dashboard furniture: three KPI cards, hero stat, gradient banner,
   "Welcome back", floating-pill nav over a gradient.
3. No consumer-video furniture: rounded thumbnail grid, big red play button,
   autoplay, carousel, giant rounded cards.
4. No TV-forensics furniture: "ENHANCE", red reticles, scanlines, dramatic zoom.
5. No purple/violet gradients anywhere; the only gradients in the app are the
   two-stop vertical washes inside the timeline strip and the skeleton shimmer,
   and both are data, not decoration.
6. No emoji as icons; one icon set (inline SVG, 1.5 px stroke) for the whole app.
7. No shadows on panels, rows, or tables; hairlines only.
8. No spinner where a skeleton fits; no skeleton where the layout is unknown
   (that case gets one labelled line of text).
9. Never a number without its unit in a data surface; never a raw milli-unit
   where a human-readable seconds value exists (show both when the raw value is
   the evidence).
10. No control backed by a guessed endpoint: every clickable thing calls a route
    in `BACKEND_MANIFEST.md` or performs a real local-only UI operation (copy,
    expand, filter, navigate, toggle theme).
11. No verdict vocabulary in UI strings; the forbidden lexicon is also forbidden
    in our own copy.
12. No mixed icon sets, no mixed type pairings, no per-component radii or
    shadows.

## 10. Audit

- **Raw-value audit:** `frontend/scripts/token-audit.mjs` greps every file under
  `frontend/src` (except `styles/tokens.css`) for hex colours, `rgb()`/`rgba()`
  literals, and `ms`/`s` durations in CSS/TSX and fails on any hit. The same
  script enforces the § 7 motion contract: any `transition`/`transition-property`
  declaration or `@keyframes` block that animates a geometry property (`width`,
  `height`, `top`, `left`, `right`, `bottom`, their min/max variants, or `all`)
  fails. Run as part of verification.
- **Contrast audit:** `frontend/scripts/contrast-audit.mjs` (see § 3).
- **Browser walk:** `tests/test_ui_journey.py` drives the built SPA in
  Playwright against a real server (launch → authenticate via the preview
  bootstrap → create and save a decision → open About → fresh browser context →
  recover the same saved decision).
- **Screenshot + DOM pass:** `scripts/ui_screenshots.py` serves the built
  `longwatch/static` bundle with a fixture API (shaped exactly like
  `longwatch/api.py` responses) and writes 1440 px and 375 px screenshots of
  every page in both themes, failing on any console error or missing DOM hook.
  It needs no database, so it runs anywhere Node and a browser do.
- Manual audit of the six categories in `claudopus_ui_mastery` is performed at
  handoff and reported in the round summary.
