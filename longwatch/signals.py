"""Signals (§8.2) — deterministic, full timeline, always.

One ffmpeg decode at 1 fps, 160×90 grayscale, rawvideo piped into a bounded
ring (no ML).  Motion energy, persistence and luma are computed per second as
integer milli-units; one audio pass writes silence gaps and loudness spikes;
a single O(n) pass at completion computes the nearest-rank percentile summary
into ``sources.signal_summary``.

The threshold relationship is stated once: the 3 s silence floor is the
*detection* floor; patterns select over the complete stream.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import media

ANALYSIS_WIDTH = 160
ANALYSIS_HEIGHT = 90
SIGNAL_FPS = 1.0
BLOCK_SAMPLES = 300
PERSISTENCE_WINDOW = 60
PERSISTENCE_FLOOR_MILLI = 50  # 0.05
SILENCE_DB = -45.0
SILENCE_MIN_S = 3.0
SPIKE_DB = 12.0
PERCENTILES = (10, 25, 50, 75, 85, 90, 95)
MIN_PERCENTILE_SAMPLES = 60


def _milli(value: float) -> int:
    """Round-half-even to integer thousandths (the single rounding point)."""
    scaled = float(value) * 1000.0
    floor = math.floor(scaled)
    remainder = scaled - floor
    if remainder > 0.5:
        return int(floor) + 1
    if remainder < 0.5:
        return int(floor)
    return int(floor) if floor % 2 == 0 else int(floor) + 1


class SeriesAccumulator:
    """The bounded-ring series computer (§8.2).

    Frames flow through one at a time; nothing but the previous frame and two
    per-second float arrays is retained, so a 200-hour source costs O(seconds)
    memory — never an (N, H, W) cube.  ``finish()`` applies the single
    rounding point and the vectorized persistence pass.
    """

    def __init__(self) -> None:
        self._motion: list[float] = []
        self._luma: list[float] = []
        self._previous: np.ndarray | None = None

    def add(self, frame: np.ndarray) -> None:
        current = np.asarray(frame, dtype=np.float64)
        self._luma.append(float(current.mean()) / 255.0)
        if self._previous is None:
            self._motion.append(0.0)
        else:
            self._motion.append(float(np.abs(current - self._previous).mean()) / 255.0)
        self._previous = current

    def finish(self) -> dict[str, list[int]]:
        count = len(self._motion)
        if count == 0:
            return {"motion": [], "luma": [], "persistence": []}
        motion = np.asarray(self._motion, dtype=np.float64)
        luma = np.asarray(self._luma, dtype=np.float64)
        persistence = _persistence(motion)
        return {
            "motion": [_milli(v) for v in motion],
            "luma": [_milli(v) for v in luma],
            "persistence": [_milli(v) for v in persistence],
        }


def _persistence(motion: np.ndarray) -> np.ndarray:
    """Fraction of the trailing 60 samples above 0.05 — vectorized cumsum.

    The specification is explicit: this sliding window is a cumsum over the
    whole series, never a Python loop, so a corpus-scale series costs one
    vectorized pass.
    """
    count = int(motion.shape[0])
    if count == 0:
        return np.zeros(0, dtype=np.float64)
    above = (motion * 1000.0 > PERSISTENCE_FLOOR_MILLI).astype(np.int64)
    cumulative = np.concatenate(([0], np.cumsum(above)))
    indices = np.arange(count)
    starts = np.maximum(0, indices - PERSISTENCE_WINDOW + 1)
    totals = cumulative[indices + 1] - cumulative[starts]
    spans = (indices + 1 - starts).astype(np.float64)
    return totals / spans


def compute_series(frames: np.ndarray) -> dict[str, list[int]]:
    """Per-second motion / luma / persistence, in integer milli-units.

    ``frames`` is an (N, H, W) uint8 array at 1 fps.  Pure and deterministic —
    the unit tests drive it directly with synthetic arrays, and the production
    path consumes the same math through :class:`SeriesAccumulator` over the
    decode stream.
    """
    accumulator = SeriesAccumulator()
    for index in range(int(frames.shape[0])):
        accumulator.add(frames[index])
    return accumulator.finish()


def compute_series_stream(frames, width: int = ANALYSIS_WIDTH,
                          height: int = ANALYSIS_HEIGHT) -> dict[str, list[int]]:
    """Consume a rawvideo gray frame stream into the per-second series.

    The stream is the bounded ring of §8.2: frames are processed as they
    arrive and dropped, never materialized.
    """
    accumulator = SeriesAccumulator()
    for chunk in frames:
        accumulator.add(np.frombuffer(chunk, dtype=np.uint8).reshape(height, width))
    return accumulator.finish()


def nearest_rank(values: list[int], percentile: int) -> int:
    """Nearest-rank percentile: ascending-sorted value at ceil(p/100 × N) − 1."""
    if not values:
        raise ValueError("nearest_rank needs at least one sample")
    ordered = sorted(int(v) for v in values)
    index = math.ceil(percentile / 100.0 * len(ordered)) - 1
    index = max(0, min(len(ordered) - 1, index))
    return ordered[index]


def summarize(series: dict[str, list[int]]) -> dict:
    """One O(n) pass → the stored ``sources.signal_summary`` (integer milli-units)."""
    summary: dict = {}
    for name in ("motion", "luma", "persistence"):
        values = series.get(name) or []
        if not values:
            continue
        entry = {"count": len(values)}
        for percentile in PERCENTILES:
            entry[f"p{percentile}"] = nearest_rank(values, percentile)
        summary[name] = entry
    return summary


def blocks(series: dict[str, list[int]], size: int = BLOCK_SAMPLES) -> list[dict]:
    """Split the per-second series into ``signal_blocks`` rows."""
    length = max((len(v) for v in series.values()), default=0)
    rows = []
    for index in range(0, length, size):
        rows.append({
            "idx": index // size,
            "start_s": float(index),
            "series": {name: list(values[index:index + size])
                       for name, values in series.items()},
        })
    return rows


def audio_events_from_seconds(buckets, sample_rate: int,
                              silence_gaps: list[tuple[float, float]] | None = None) -> list[dict]:
    """Silence gaps (detection floor) and loudness spikes over the running median.

    ``buckets`` is an iterable of one-second mono s16 PCM arrays: the audio
    decode is consumed as it streams (§5), so the pass holds the per-second RMS
    series — O(seconds) — and never the source's whole PCM, which is 57.6 MB
    per hour at 8 kHz mono s16 and ~11.5 GB at the 200 h corpus scale.
    """
    events: list[dict] = []
    for start, end in (silence_gaps or []):
        events.append({
            "kind": "silence_gap",
            "t_start": float(start),
            "t_end": float(end),
            "value_milli": _milli(max(0.0, float(end) - float(start))),
            "meta": {"threshold_db": SILENCE_DB, "min_duration_s": SILENCE_MIN_S},
        })
    db: list[float] = []
    for bucket in buckets:
        if bucket is None or len(bucket) == 0:
            continue
        samples = np.asarray(bucket, dtype=np.float64)
        rms = float(np.sqrt((samples ** 2).mean()) / 32768.0)
        db.append(float(20.0 * np.log10(max(rms, 1e-9))))
    seconds = len(db)
    if seconds < 2:
        return events
    for index in range(seconds):
        start = max(0, index - 30)
        if index - start < 3:
            continue
        baseline = float(np.median(db[start:index]))
        if db[index] - baseline >= SPIKE_DB:
            events.append({
                "kind": "loudness_spike",
                "t_start": float(index),
                "t_end": float(index + 1),
                "value_milli": int(round((db[index] - baseline) * 1000.0)),
                "meta": {"baseline_db_milli": int(round(baseline * 1000.0)),
                         "threshold_db": SPIKE_DB},
            })
    return events


@dataclass
class SignalsResult:
    series: dict[str, list[int]] = field(default_factory=dict)
    summary: dict = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    has_audio: bool = False
    blocks: list[dict] = field(default_factory=list)


def run_signals(path: str, duration_s: float, cfg) -> SignalsResult:
    """The real signals pass: one video decode, one audio decode, one summary.

    The decode is consumed as a stream (the bounded ring of §8.2): frames are
    reduced to per-second values as they arrive, never materialized, so the
    pass's memory is O(seconds), not O(frames × pixels).
    """
    series = compute_series_stream(
        media.gray_frames(path, SIGNAL_FPS, ANALYSIS_WIDTH, ANALYSIS_HEIGHT,
                          duration_s, cfg.ffmpeg_hang_base_s, cfg.ffmpeg_hang_factor))
    result = SignalsResult(series=series)
    probe_doc = media.probe(path, cfg.ffmpeg_hang_base_s, cfg.ffmpeg_hang_factor)
    facts = media.probe_facts(probe_doc)
    result.has_audio = bool(facts["has_audio"])
    if result.has_audio:
        gaps = media.silencedetect(path, duration_s, SILENCE_DB, SILENCE_MIN_S,
                                   cfg.ffmpeg_hang_base_s, cfg.ffmpeg_hang_factor)
        result.events = audio_events_from_seconds(
            media.decode_audio_seconds(path, duration_s, 8000,
                                       cfg.ffmpeg_hang_base_s, cfg.ffmpeg_hang_factor),
            8000, gaps)
    else:
        result.events = [{"kind": "audio_absent", "t_start": None, "t_end": None,
                          "value_milli": None, "meta": {"reason": "no audio stream"}}]
    result.summary = summarize(series)
    result.blocks = blocks(series)
    return result
