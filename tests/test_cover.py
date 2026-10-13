"""COVER — full-coverage windowing, clamps held, count reported (§17.6).

A synthetic static 90 s source yields windows whose durations (summed with
``math.fsum``) equal the source duration within ±0.5 s, every window within the
clamps, and the actual count reported.
"""
from __future__ import annotations

import math
from pathlib import Path

from longwatch import media, windowing


def test_static_ninety_seconds_is_one_window():
    windows = windowing.plan_windows(90.0, [], coverage_s=300, min_s=10, max_s=180)
    check = windowing.coverage_check(windows, 90.0, 10, 180)
    assert check["count"] == 1
    assert check["coverage_ok"] and check["clamps_ok"] and check["contiguous"]
    assert windows[0].start_s == 0.0 and windows[0].end_s == 90.0


def test_grid_points_tile_and_split_at_the_clamp():
    windows = windowing.plan_windows(720.0, [], coverage_s=300, min_s=10, max_s=180)
    check = windowing.coverage_check(windows, 720.0, 10, 180)
    assert check["coverage_ok"] and check["clamps_ok"] and check["contiguous"]
    assert math.fsum(w.duration_s for w in windows) == 720.0
    assert check["count"] == 5  # 0-150, 150-300, 300-450, 450-600, 600-720
    assert {w.boundary_kind for w in windows} <= {"grid", "split", "merged", "scene"}


def test_a_scene_cut_near_a_grid_point_is_dropped_not_duplicated():
    windows = windowing.plan_windows(720.0, [{"t": 303.0, "score": 0.4}],
                                     coverage_s=300, min_s=10, max_s=180)
    assert windowing.coverage_check(windows, 720.0, 10, 180)["coverage_ok"]
    # the 303 s cut is inside the 300 s window: it merges rather than splitting
    assert any(w.boundary_kind == "merged" for w in windows)
    assert not any(abs(w.start_s - 303.0) < 1e-6 for w in windows)


def test_a_real_scene_cut_becomes_a_boundary():
    windows = windowing.plan_windows(400.0, [{"t": 200.0, "score": 0.5}],
                                     coverage_s=300, min_s=10, max_s=180)
    starts = [round(w.start_s, 3) for w in windows]
    assert 200.0 in starts
    assert windowing.coverage_check(windows, 400.0, 10, 180)["coverage_ok"]


def test_duration_shorter_than_the_grid_is_still_fully_covered():
    windows = windowing.plan_windows(7.0, [], coverage_s=300, min_s=10, max_s=180)
    assert len(windows) == 1
    assert windows[0].start_s == 0.0 and windows[0].end_s == 7.0


def test_real_static_source_covers_exactly(tmp_path, cfg, require_ffmpeg):
    """The ffmpeg leg: a real 90 s static source, real scene detection."""
    source = tmp_path / "static.mp4"
    media.run_ffmpeg([
        "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi",
        "-i", "color=black:s=160x90:d=90", "-r", "5", "-an",
        "-pix_fmt", "yuv420p", str(source)], 120.0)
    document = media.probe(source)
    facts = media.probe_facts(document)
    duration = facts["duration_s"]
    assert abs(duration - 90.0) <= 1.0, duration  # the muxer's own duration
    cuts = media.scene_cut_times(source, duration, cfg.scene_t0,
                                 cfg.ffmpeg_hang_base_s, cfg.ffmpeg_hang_factor)
    assert len(cuts) <= 4  # the early-exit bisection bounds the passes
    windows = windowing.plan_windows(duration, cuts, cfg.coverage_s,
                                     cfg.window_min_s, cfg.window_max_s)
    check = windowing.coverage_check(windows, duration, cfg.window_min_s,
                                     cfg.window_max_s)
    assert check["coverage_ok"], check
    assert check["clamps_ok"], check
    assert check["contiguous"], check
    assert abs(math.fsum(w.duration_s for w in windows) - duration) <= 0.5
