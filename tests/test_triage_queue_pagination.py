"""QUEUE PAGINATION — the shipped frontend module, checked under Node.

BUG-004 (SOD static review): the triage queue's tail cursor was a stored
position (`headCandidates + extraFetched`) that drifted whenever a re-score
shrank, grew or re-ranked the head page, so rows that were never served could
be skipped silently.  The pagination logic now lives in
``frontend/src/pages/triageQueue.ts`` (imported by ``Triage.tsx``) and this
module check pins its invariants:

* the offsets are derived from what is held, never from a stored cursor;
* the merged view never duplicates a row and keeps the live head first;
* under a stable ranking, a shrunken head, an insertion above the loaded
  region and an ml_ack growth, clicking Load-more reaches **every** ranked row;
* the harness detects the removed positional cursor when it is re-enabled
  (``TRIAGE_QUEUE_LEGACY_CURSOR=1``), so a passing run is evidence.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HARNESS = REPO / "tests" / "fixtures" / "triage_queue.mjs"
MODULE = REPO / "frontend" / "src" / "pages" / "triageQueue.ts"
COMPONENT = REPO / "frontend" / "src" / "pages" / "Triage.tsx"


def _run(env: dict | None = None) -> subprocess.CompletedProcess:
    if not shutil.which("node"):
        pytest.skip("node is not available in this environment")
    return subprocess.run(
        ["node", "--experimental-strip-types", str(HARNESS)],
        capture_output=True, text=True, timeout=120, env={**os.environ, **(env or {})})


def test_the_component_uses_the_shipped_module_not_a_second_copy():
    """One implementation: the workbench imports the module this check pins."""
    component = COMPONENT.read_text(encoding="utf-8")
    assert "from './triageQueue'" in component
    for helper in ("tailPageOffsets(", "mergeTail(", "mergeEntries(", "countCandidates("):
        assert helper in component, helper
    assert "extraFetched" not in component, "the positional cursor must be gone"


def test_the_pagination_invariants_hold_under_node():
    result = _run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "invariants hold" in result.stdout


def test_the_check_detects_the_removed_positional_cursor():
    """The harness is falsifiable: with the old cursor restored it must fail
    the drift scenarios (a check that cannot fail proves nothing)."""
    result = _run({"TRIAGE_QUEUE_LEGACY_CURSOR": "1"})
    assert result.returncode == 1, result.stdout + result.stderr
    assert "every ranked row is reachable" in result.stderr
    assert "diverged" in result.stderr
