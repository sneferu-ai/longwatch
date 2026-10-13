"""UI TRUTH PINS — the reference page must match the shipped poll cadences,
plus source-level pins for the frontend defects the SOD review found.

BUG-011: ``docs/UI.md`` claimed every surface polled "every 2 s" and pointed at
``frontend/src/styles.css`` / "There is no separate DESIGN.md" — both stale.
BUG-007: the Patterns re-score poll had no terminal condition.
BUG-009: the keyboard decision verbs returned silently when the selected entry
was the wrong kind (no message, no state change).
BUG-012: the indeterminate ``Progress`` bar dropped its value bounds entirely.
Each is pinned against the real source so none can drift back silently.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
UI_DOC = REPO / "docs" / "UI.md"
PAGES = REPO / "frontend" / "src" / "pages"


def _doc() -> str:
    return UI_DOC.read_text(encoding="utf-8")


def _page(name: str) -> str:
    return (PAGES / name).read_text(encoding="utf-8")


def test_the_documented_poll_cadences_are_the_shipped_ones():
    doc = _doc()
    app = (REPO / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "setInterval(load, 5000)" in app, "system/status polls every 5 s"
    assert "every 5 s (`App.tsx`)" in doc
    # Ingest: the sources table 5 s, an open source detail 2 s
    ingest = _page("Ingest.tsx")
    assert re.search(r"usePoll<[^>]*>\('/api/v1/sources', 5000\)", ingest)
    assert re.search(r"usePoll<SourceDetail>\([^)]*, 2000\)", ingest)
    assert "Ingest sources table every 5 s and an open source detail every 2 s" in doc
    # Triage: the ranked queue 2 s
    triage = _page("Triage.tsx")
    assert "const POLL_MS = 2000;" in triage
    assert "Triage ranked queue every 2 s" in doc
    # Exports: 3 s
    assert re.search(r"usePoll<[^>]*>\('/api/v1/exports', 3000\)", _page("Exports.tsx"))
    assert "Exports list every 3 s" in doc
    # Patterns: patterns + stats 5 s
    patterns = _page("Patterns.tsx")
    assert re.search(r"usePoll<[^>]*>\('/api/v1/patterns', 5000\)", patterns)
    assert re.search(r"usePoll<[^>]*>\('/api/v1/patterns/stats', 5000\)", patterns)
    assert "Patterns and its stats every 5 s" in doc


def test_the_reference_points_at_the_shipped_token_layer_and_design_doc():
    doc = _doc()
    assert "frontend/src/styles/tokens.css" in doc
    assert "frontend/src/styles.css" not in doc, "the stale stylesheet path must be gone"
    assert "There is no separate `DESIGN.md`" not in doc
    assert "`DESIGN.md` is the design system document" in doc
    assert (REPO / "DESIGN.md").is_file()
    assert (REPO / "frontend" / "src" / "styles" / "tokens.css").is_file()


def test_the_rescore_poll_stops_on_a_terminal_state():
    """BUG-007: a finished job must stop the 2 s interval."""
    patterns = _page("Patterns.tsx")
    effect = patterns[patterns.index("const tick = async () => {"):]
    effect = effect[: effect.index("}, [jobId]);")]
    assert "window.clearInterval(timer)" in effect
    assert "payload.state === 'succeeded' || payload.state === 'failed'" in effect
    # the interval is armed before the first tick, so a job that is already
    # terminal on the first response can still clear it
    assert effect.index("const timer = window.setInterval(tick, POLL_MS);") < \
        effect.index("void tick();")


def test_a_key_that_cannot_apply_says_so():
    """BUG-009: the decision verbs never no-op silently on the wrong entry."""
    triage = _page("Triage.tsx")
    decide = triage[triage.index("const decide = useCallback("):]
    decide = decide[: decide.index("const acknowledge = useCallback(")]
    assert "this entry is a detection-incomplete span, not a scored candidate" in decide
    assert "nothing is selected" in decide
    acknowledge = triage[triage.index("const acknowledge = useCallback("):]
    acknowledge = acknowledge[: acknowledge.index("const flagFlow = useCallback(")]
    assert "this entry is a scored candidate, not a detection-incomplete span" in acknowledge
    assert "setMessage" in acknowledge
    # and the source-resolution pane cannot claim a src_viewed it never showed
    toggle = triage[triage.index("const toggleSource = useCallback("):]
    toggle = toggle[: toggle.index("const togglePlay = useCallback(")]
    assert "current.kind !== 'candidate'" in toggle


def test_the_indeterminate_progress_keeps_its_aria_bounds():
    """BUG-012: only aria-valuenow is omitted when progress is unknown."""
    kit = (REPO / "frontend" / "src" / "ui" / "kit.tsx").read_text(encoding="utf-8")
    progress = kit[kit.index("export function Progress("):]
    progress = progress[: progress.index("/* ---------------------------------------------------------------- keycaps */")]
    assert "aria-valuemin={0}" in progress
    assert "aria-valuemax={100}" in progress
    assert "aria-valuenow={indeterminate ? undefined : value}" in progress


def test_console_problems_name_the_page_that_produced_them():
    """BUG-010: a console error on `patterns` is never filed under `about`
    (the page the screenshot loop happened to visit last)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "longwatch_ui_screenshots", REPO / "scripts" / "ui_screenshots.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    problems = module.format_console_problems(
        "desktop", "dark", [("patterns", "error: boom"), ("ingest", "warning: meh")])
    assert problems == ["desktop/dark/patterns: console error: boom",
                        "desktop/dark/ingest: console warning: meh"]
