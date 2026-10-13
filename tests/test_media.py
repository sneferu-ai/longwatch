"""Media plumbing — the hang-guarded decode streams and keyframe seeking (§5, §9).

``gray_frames`` is the signals pass's bounded ring: it must fail loudly on a
broken stream (never degrade silently into a partial series) and hold the same
wall-clock hang guard every other ffmpeg invocation carries.  ``keyframe_before``
must find the real keyframe even in long-GOP footage, because the custody
record's ``actual_start_ms`` is only honest if the lookup is.  The audio decode
streams per-second buckets — never the source's whole PCM — and the detector's
window decode streams letterboxed frames the same way.
"""
from __future__ import annotations

import subprocess

import numpy as np
import pytest

from longwatch import detect, media, signals
from tests.conftest import make_video


def make_video_with_audio(path, seconds: float = 4.0, tone_from_s: float = 2.0) -> "object":
    """A tiny real video whose audio is silence, then a 440 Hz tone."""
    tone = f"if(lt(t,{tone_from_s}),0,0.5*sin(2*PI*440*t))"
    argv = [
        "ffmpeg", "-v", "error", "-nostdin", "-y",
        "-f", "lavfi", "-i", f"testsrc2=s=160x90:d={seconds}:r=12",
        "-f", "lavfi", "-i", f"aevalsrc=exprs='{tone}':d={seconds}:s=8000",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    ]
    subprocess.run(argv, check=True, capture_output=True)
    return path


def test_gray_streams_frames_and_matches_the_batch_math(tmp_path, require_ffmpeg):
    video = make_video(tmp_path / "gray.mp4", seconds=4.0)
    chunks = list(media.gray_frames(str(video), 1.0, 160, 90, 4.0))
    assert len(chunks) >= 3
    assert all(len(chunk) == 160 * 90 for chunk in chunks)

    frames = np.stack([np.frombuffer(chunk, dtype=np.uint8).reshape(90, 160)
                       for chunk in chunks])
    batched = signals.compute_series(frames)
    streamed = signals.compute_series_stream(chunks)
    assert batched == streamed


def test_gray_frames_fail_loudly_on_a_broken_stream(tmp_path, require_ffmpeg):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"this is not a video stream" * 64)
    with pytest.raises(media.MediaError):
        list(media.gray_frames(str(broken), 1.0, 160, 90, 4.0))


def test_audio_buckets_stream_one_second_at_a_time(tmp_path, cfg, require_ffmpeg):
    """§5: the audio decode holds one bucket, never the source's whole PCM."""
    video = make_video_with_audio(tmp_path / "tone.mp4", seconds=4.0, tone_from_s=2.0)
    buckets = list(media.decode_audio_seconds(str(video), 4.0, 8000,
                                              cfg.ffmpeg_hang_base_s, cfg.ffmpeg_hang_factor))
    assert 3 <= len(buckets) <= 4
    assert all(bucket.shape == (8000,) and bucket.dtype == np.int16 for bucket in buckets)
    # the silence/tone boundary lands in the second the fixture declares
    quiet = float(np.sqrt((buckets[0].astype(np.float64) ** 2).mean()))
    loud = float(np.sqrt((buckets[-1].astype(np.float64) ** 2).mean()))
    assert loud > quiet * 10.0


def test_audio_events_come_off_the_stream_with_the_spike_at_its_second(tmp_path, cfg, require_ffmpeg):
    video = make_video_with_audio(tmp_path / "tone.mp4", seconds=5.0, tone_from_s=3.0)
    events = signals.audio_events_from_seconds(
        media.decode_audio_seconds(str(video), 5.0, 8000,
                                   cfg.ffmpeg_hang_base_s, cfg.ffmpeg_hang_factor),
        8000, [])
    spikes = [event for event in events if event["kind"] == "loudness_spike"]
    assert spikes, events
    assert spikes[0]["t_start"] == pytest.approx(3.0, abs=1.0)
    assert spikes[0]["value_milli"] >= signals.SPIKE_DB * 1000


def test_audio_decode_fails_loudly_on_a_broken_stream(tmp_path, cfg, require_ffmpeg):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"this is not a video stream" * 64)
    with pytest.raises(media.MediaError):
        list(media.decode_audio_seconds(str(broken), 4.0, 8000,
                                        cfg.ffmpeg_hang_base_s, cfg.ffmpeg_hang_factor))


def test_decode_window_streams_letterboxed_frames(tmp_path, cfg, require_ffmpeg):
    """§5: the detector's window decode yields frames one at a time."""
    video = make_video(tmp_path / "window.mp4", seconds=4.0)
    detector = detect.Detector(cfg)
    frames = list(detector.decode_window(str(video), 0.0, 4.0, 2.0))
    assert 7 <= len(frames) <= 9, len(frames)  # 4 s at 2 fps
    assert all(frame.shape == (640, 640, 3) and frame.dtype == np.uint8
               for frame in frames)
    # the letterbox pad is the declared gray, so the frame really went through it
    assert frames[0][0, 0].tolist() == [114, 114, 114]


def test_keyframe_before_finds_the_real_keyframe(tmp_path, require_ffmpeg):
    video = make_video(tmp_path / "gop.mp4", seconds=6.0)
    found = media.keyframe_before(str(video), 5.0)
    assert found is not None
    assert 0.0 <= found <= 5.0
    # the first keyframe of any seekable file is at 0
    assert media.keyframe_before(str(video), 0.0) == pytest.approx(0.0, abs=0.1)


def test_keyframe_before_finds_the_anchor_keyframe_in_long_gop_footage(
        tmp_path, require_ffmpeg):
    """A > 60 s GOP must not defeat the lookup: the interval read anchors at
    the keyframe the demuxer actually seeks to, so the largest keyframe at or
    before the request is found even when it precedes the search window."""
    target = tmp_path / "long-gop.mp4"
    argv = [
        media.FFMPEG, "-v", "error", "-nostdin", "-y", "-f", "lavfi",
        "-i", "testsrc2=s=64x36:d=75:r=5",
        "-c:v", "libx264", "-g", "5000", "-x264-params", "scenecut=0",
        "-pix_fmt", "yuv420p", "-an", str(target),
    ]
    subprocess.run(argv, check=True, capture_output=True)
    # one keyframe in the whole file, at 0; a cut at 70 s must record 0
    assert media.keyframe_before(str(target), 70.0) == pytest.approx(0.0, abs=0.5)


def test_keyframe_before_escalates_to_a_full_prefix_search(monkeypatch):
    """When the bounded window reports nothing, one full-prefix probe runs
    before any bound is recorded."""
    calls: list[tuple[float, float]] = []

    def fake_search(path, start_s, at_s, base_s, factor):
        calls.append((start_s, at_s))
        return None if start_s > 0 else 12.0

    monkeypatch.setattr(media, "_keyframe_search", fake_search)
    assert media.keyframe_before("x.mp4", 100.0, 60.0) == 12.0
    assert calls == [(40.0, 100.0), (0.0, 100.0)]

    calls.clear()

    def fake_found(path, start_s, at_s, base_s, factor):
        calls.append((start_s, at_s))
        return 7.0

    monkeypatch.setattr(media, "_keyframe_search", fake_found)
    assert media.keyframe_before("x.mp4", 100.0, 60.0) == 7.0
    assert calls == [(40.0, 100.0)], "no escalation when the bounded search finds one"


def test_persistence_is_the_vectorized_trailing_fraction():
    """Pin the vectorized cumsum against an independent reference reading."""
    motion = np.zeros(100, dtype=np.float64)
    motion[40:] = 0.10  # above the 0.05 floor from second 40 onward
    persistence = signals._persistence(motion)

    def reference(index: int) -> float:
        start = max(0, index - 59)
        window = motion[start:index + 1]
        return float((window * 1000.0 > 50).sum()) / len(window)

    for index in (0, 39, 40, 59, 60, 98, 99):
        assert persistence[index] == pytest.approx(reference(index))
    assert persistence[99] == pytest.approx(1.0)
    assert persistence[40] == pytest.approx(1 / 41)


def test_series_accumulator_matches_the_batch_path():
    rng = np.random.default_rng(11)
    frames = rng.integers(0, 255, size=(140, 90, 160), dtype=np.uint8)
    accumulator = signals.SeriesAccumulator()
    for index in range(frames.shape[0]):
        accumulator.add(frames[index])
    assert accumulator.finish() == signals.compute_series(frames)
    assert signals.compute_series(np.zeros((0, 90, 160), dtype=np.uint8)) == {
        "motion": [], "luma": [], "persistence": []}


def test_hang_timeout_scales_with_input_seconds():
    assert media.hang_timeout(60.0, 4.0, 0.0) == 60.0
    assert media.hang_timeout(60.0, 4.0, 100.0) == 460.0
