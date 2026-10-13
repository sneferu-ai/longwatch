"""Coverage windowing (§8.1) — coverage guaranteed, count determined.

Rules run once each, in this fixed order, and no earlier rule is reapplied
after a later one:

1. scene-cut timestamps (already computed by the early-exit bisection);
2. union with a uniform grid every ``COVERAGE_S`` — grid points are the
   strongest boundaries;
3. drop the weaker of any two boundaries closer than ``WINDOW_MIN_S``;
4. split any span longer than ``WINDOW_MAX_S`` at its midpoint, recursively;
5. done — there is no cap machinery.

The hard invariants are coverage and the clamps: the summed window durations
equal the source duration within ±0.5 s (1 fps granularity, summed with
``math.fsum``) and every window lies within [MIN, MAX].
"""
from __future__ import annotations

import math
from dataclasses import dataclass

KIND_SCENE = "scene"
KIND_GRID = "grid"
KIND_MERGED = "merged"
KIND_SPLIT = "split"


@dataclass(frozen=True)
class Window:
    idx: int
    start_s: float
    end_s: float
    boundary_kind: str
    scene_score: float | None = None

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass(frozen=True)
class _Boundary:
    t: float
    kind: str  # 'scene' | 'grid'
    score: float


def _grid_points(duration_s: float, coverage_s: float) -> list[float]:
    if coverage_s <= 0:
        return []
    points = []
    k = 1
    while k * coverage_s < duration_s - 1e-9:
        points.append(float(k) * coverage_s)
        k += 1
    return points


def _merge_boundaries(candidates: list[_Boundary], min_s: float) -> tuple[list[_Boundary], list[float]]:
    """Rule 3: drop the weaker of two boundaries closer than ``min_s``."""
    ordered = sorted(candidates, key=lambda b: b.t)
    kept: list[_Boundary] = []
    dropped: list[float] = []
    for boundary in ordered:
        if not kept:
            kept.append(boundary)
            continue
        last = kept[-1]
        if boundary.t - last.t < min_s:
            if last.kind == "scene" and boundary.kind == "grid":
                dropped.append(last.t)
                kept[-1] = boundary  # the grid point is the stronger boundary
            elif last.kind == boundary.kind and boundary.score > last.score:
                dropped.append(last.t)
                kept[-1] = boundary
            else:
                dropped.append(boundary.t)
            continue
        kept.append(boundary)
    return kept, dropped


def _split_span(start: float, end: float, max_s: float) -> list[tuple[float, float, bool]]:
    """Rule 4: recursive midpoint splitting.  Returns (start, end, was_split)."""
    if end - start <= max_s + 1e-9:
        return [(start, end, False)]
    mid = (start + end) / 2.0
    return _split_span(start, mid, max_s) + _split_span(mid, end, max_s)


def plan_windows(duration_s: float, scene_cuts: list[dict] | None = None,
                 coverage_s: float = 300.0, min_s: float = 10.0,
                 max_s: float = 180.0) -> list[Window]:
    """The determined window plan for one source."""
    duration_s = float(duration_s)
    if duration_s <= 0:
        return []
    scene_cuts = scene_cuts or []
    candidates: list[_Boundary] = [
        _Boundary(float(p), "grid", 1.0) for p in _grid_points(duration_s, coverage_s)
    ]
    for cut in scene_cuts:
        t = float(cut.get("t", cut.get("time", 0.0)))
        if 0.0 < t < duration_s:
            candidates.append(_Boundary(t, "scene", float(cut.get("score", 0.5) or 0.5)))

    kept, dropped = _merge_boundaries(candidates, min_s)
    points = [b.t for b in kept]
    kinds = {b.t: b.kind for b in kept}
    scores = {b.t: (b.score if b.kind == "scene" else None) for b in kept}

    spans: list[tuple[float, float, bool]] = []
    edges = [0.0] + points + [duration_s]
    for index in range(len(edges) - 1):
        spans.extend(_split_span(edges[index], edges[index + 1], max_s))

    windows: list[Window] = []
    for idx, (start, end, was_split) in enumerate(spans):
        contains_dropped = any(start + 1e-9 < d < end - 1e-9 for d in dropped)
        if contains_dropped:
            kind = KIND_MERGED
        elif was_split:
            kind = KIND_SPLIT
        elif start in kinds:
            kind = kinds[start]
        else:
            kind = KIND_GRID  # the origin
        windows.append(Window(idx=idx, start_s=start, end_s=end, boundary_kind=kind,
                              scene_score=scores.get(start)))
    return windows


def coverage_check(windows: list[Window], duration_s: float, min_s: float,
                   max_s: float) -> dict:
    """The COVER invariants, computed the same way the test computes them."""
    total = math.fsum(w.duration_s for w in windows)
    return {
        "count": len(windows),
        "sum_duration_s": total,
        "delta_s": total - float(duration_s),
        "coverage_ok": abs(total - float(duration_s)) <= 0.5,
        "clamps_ok": all(min_s - 1e-6 <= w.duration_s <= max_s + 1e-6 for w in windows),
        "contiguous": all(
            abs(windows[i].end_s - windows[i + 1].start_s) < 1e-6
            for i in range(len(windows) - 1)
        ) if windows else True,
        "first_start": windows[0].start_s if windows else None,
        "last_end": windows[-1].end_s if windows else None,
    }
