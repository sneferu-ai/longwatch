"""ffmpeg / ffprobe as explicit unprivileged subprocesses (§5, §8).

No ``ffmpeg-python``: every invocation is an argv list, runs with ``-threads 1``
where it decodes or encodes, and carries the wall-clock hang guard
``base + factor × input-seconds``.  The exact command is recorded per clip.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from pathlib import Path

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

HASH_CHUNK = 4 * 1024 * 1024  # 4 MiB streaming chunks, never a full buffer


class MediaError(RuntimeError):
    """An ffmpeg/ffprobe invocation failed; stderr is preserved verbatim."""

    def __init__(self, message: str, stderr: str = ""):
        self.stderr = stderr
        super().__init__(message)


def hang_timeout(base_s: float, factor: float, input_seconds: float) -> float:
    """The hang guard: a wall-clock bound, not a throughput limit."""
    return float(base_s) + float(factor) * max(0.0, float(input_seconds))


def run_ffmpeg(argv: list[str], timeout_s: float) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaError(
            f"ffmpeg exceeded the {timeout_s:.1f}s hang guard: {' '.join(argv)}",
            stderr=(exc.stderr or b"").decode("utf-8", "replace")[-4000:] if isinstance(exc.stderr, bytes) else str(exc.stderr or ""),
        ) from exc
    if proc.returncode != 0:
        raise MediaError(
            f"ffmpeg exited {proc.returncode}: {' '.join(argv)}",
            stderr=proc.stderr.decode("utf-8", "replace")[-4000:],
        )
    return proc


def run_ffprobe(argv: list[str], timeout_s: float) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=timeout_s, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"ffprobe exceeded the {timeout_s:.1f}s hang guard") from exc
    if proc.returncode != 0:
        raise MediaError(
            f"ffprobe exited {proc.returncode}",
            stderr=proc.stderr.decode("utf-8", "replace")[-4000:],
        )
    return proc


def probe(path: str | Path, base_s: float = 60.0, factor: float = 4.0) -> dict:
    """``ffprobe -v error -print_format json -show_format -show_streams``, verbatim."""
    argv = [FFPROBE, "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path)]
    proc = run_ffprobe(argv, hang_timeout(base_s, factor, 0.0))
    return json.loads(proc.stdout.decode("utf-8"))


def probe_facts(probe_doc: dict) -> dict:
    """The persisted subset of a probe (observed metadata, never asserted fact)."""
    fmt = probe_doc.get("format") or {}
    video = next((s for s in probe_doc.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in probe_doc.get("streams", []) if s.get("codec_type") == "audio"), None)
    duration = None
    for source in (fmt.get("duration"), (video or {}).get("duration")):
        if source not in (None, "N/A"):
            try:
                duration = float(source)
                break
            except (TypeError, ValueError):
                continue
    fps = None
    if video and video.get("avg_frame_rate") and video["avg_frame_rate"] not in ("0/0", "N/A"):
        try:
            num, den = video["avg_frame_rate"].split("/")
            if float(den):
                fps = float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            fps = None
    return {
        "duration_s": duration,
        "fps": fps,
        "width": int(video["width"]) if video and video.get("width") else None,
        "height": int(video["height"]) if video and video.get("height") else None,
        "video_codec": (video or {}).get("codec_name"),
        "audio_codec": (audio or {}).get("codec_name"),
        "has_audio": audio is not None,
    }


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


class StreamingHasher:
    """Hash while streaming; the upload path never buffers a whole file."""

    def __init__(self):
        self._digest = hashlib.sha256()
        self.size = 0

    def update(self, chunk: bytes) -> None:
        self._digest.update(chunk)
        self.size += len(chunk)

    def hexdigest(self) -> str:
        return self._digest.hexdigest()


# ------------------------------------------------------------------ scene cuts
_SHOWINFO_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")


def scene_pass(path: str | Path, theta: float, duration_s: float,
               base_s: float = 60.0, factor: float = 4.0) -> list[float]:
    """One scene pass: the timestamps of frames surviving ``gt(scene, theta)``."""
    argv = [
        FFMPEG, "-v", "info", "-nostdin", "-threads", "1",
        "-i", str(path),
        "-vf", f"select='gt(scene,{theta:.3f})',showinfo",
        "-an", "-f", "null", "-",
    ]
    proc = run_ffmpeg(argv, hang_timeout(base_s, factor, duration_s))
    text = proc.stderr.decode("utf-8", "replace")
    cuts = []
    for match in _SHOWINFO_RE.finditer(text):
        value = float(match.group(1))
        if value > 0:
            cuts.append(value)
    return sorted(set(cuts))


def cuts_per_10min(cuts: list[float], duration_s: float) -> float:
    if duration_s <= 0:
        return 0.0
    return len(cuts) / (duration_s / 600.0)


SCENE_BAND = (2.0, 8.0)
MAX_SCENE_PASSES = 4


def scene_cut_times(path: str | Path, duration_s: float, theta0: float = 0.30,
                    base_s: float = 60.0, factor: float = 4.0) -> list[dict]:
    """Early-exit bisection tuned for the flagship corpus (§8.1 step 1).

    Evaluate at ``theta0``; stop after one pass when the cut density is already
    in the 2–8 per 10 minutes band.  When density is below the band, probe the
    most sensitive 0.15 once; if it is still below (the static-barn case) stop —
    two passes total.  Otherwise bisect within the bracketing interval, at most
    ``MAX_SCENE_PASSES`` passes overall.
    """
    theta0 = min(0.60, max(0.15, float(theta0)))
    passes: list[dict] = []

    def measure(theta: float) -> float:
        cuts = scene_pass(path, theta, duration_s, base_s, factor)
        density = cuts_per_10min(cuts, duration_s)
        passes.append({"theta": theta, "cuts": cuts, "density": density})
        return density

    density = measure(theta0)
    lo, hi = SCENE_BAND
    if lo <= density <= hi or len(passes) >= MAX_SCENE_PASSES:
        return _finish(passes)
    if density < lo:
        if theta0 <= 0.151:
            return _finish(passes)
        density_low = measure(0.15)
        if density_low < lo:
            return _finish(passes)  # static content: two passes, grid-only
        left, right = 0.15, theta0
    else:
        if theta0 >= 0.599:
            return _finish(passes)
        density_high = measure(0.60)
        if density_high > hi:
            return _finish(passes)
        left, right = theta0, 0.60
    while len(passes) < MAX_SCENE_PASSES and (right - left) > 0.05:
        mid = (left + right) / 2.0
        density_mid = measure(mid)
        if lo <= density_mid <= hi:
            break
        if density_mid < lo:
            right = mid
        else:
            left = mid
    return _finish(passes)


def _finish(passes: list[dict]) -> list[dict]:
    """Return the cuts of the pass that came closest to the band."""
    if not passes:
        return []
    lo, hi = SCENE_BAND

    def distance(entry: dict) -> float:
        if lo <= entry["density"] <= hi:
            return 0.0
        return min(abs(entry["density"] - lo), abs(entry["density"] - hi))

    best = min(passes, key=distance)
    return [{"t": t, "score": best["theta"], "theta": best["theta"]} for t in best["cuts"]]


# ------------------------------------------------------------------- decoding
def rawvideo_frames(argv: list[str], frame_bytes: int, timeout_s: float,
                    path: str | Path, grace_s: float = 60.0):
    """Pipe fixed-size rawvideo frames from ffmpeg stdout, one at a time (§5).

    The bounded ring: at most one frame plus one read chunk is ever held, so a
    decode costs O(frame), never O(stream) — a 180 s 640×640 rgb24 window is
    ~442 MB if buffered whole and ~1.2 MB here.  Every rawvideo consumer in the
    product goes through this one generator (the signals pass's gray frames, the
    detector's letterboxed RGB windows, the audio PCM buckets) so the memory
    discipline has exactly one implementation.

    The hang guard covers the stream itself, not just process exit: a decoder
    that stops producing bytes is killed at the ``base + factor × input``
    wall-clock bound every other invocation carries, and a non-zero exit raises
    :class:`MediaError` with stderr preserved — a corrupt stream never degrades
    silently into a partial series.
    """
    import select
    import time

    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    deadline = time.monotonic() + timeout_s
    eof = False
    try:
        buffer = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                proc.kill()
                raise MediaError(
                    f"ffmpeg exceeded the {timeout_s:.1f}s hang guard mid-stream "
                    f"(stalled decode of {path})")
            ready, _, _ = select.select([proc.stdout], [], [], remaining)
            if not ready:
                proc.kill()
                raise MediaError(
                    f"ffmpeg produced no bytes within the {timeout_s:.1f}s hang "
                    f"guard (stalled decode of {path})")
            chunk = proc.stdout.read1(frame_bytes)
            if not chunk:
                eof = True  # the decoder finished cleanly
                break
            buffer.extend(chunk)
            while len(buffer) >= frame_bytes:
                yield bytes(buffer[:frame_bytes])
                del buffer[:frame_bytes]
    except GeneratorExit:
        proc.kill()  # the consumer went away; never leave a decoder behind
        raise
    finally:
        try:
            proc.stdout.close()
        except Exception:  # noqa: BLE001 - closing a dead pipe
            pass
        try:
            proc.wait(timeout=max(1.0, float(grace_s)))
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001 - the process is gone either way
                pass
        stderr_tail = ""
        # only read the pipe of a provably dead process — reading a live one's
        # stderr would block forever, which is the hang this guard exists to
        # prevent
        if proc.returncode is not None and proc.stderr is not None:
            try:
                stderr_tail = proc.stderr.read().decode("utf-8", "replace")[-4000:]
            except Exception:  # noqa: BLE001 - a diagnostic tail is best-effort
                pass
    if eof and proc.returncode:
        raise MediaError(f"ffmpeg exited {proc.returncode} decoding {path}",
                         stderr=stderr_tail)


def gray_frames(path: str | Path, fps: float, width: int, height: int,
                duration_s: float, base_s: float = 60.0, factor: float = 4.0):
    """Pipe rawvideo grayscale frames from ffmpeg (bounded ring, never buffered)."""
    argv = [
        FFMPEG, "-v", "error", "-nostdin", "-threads", "1",
        "-i", str(path),
        "-vf", f"fps={fps},scale={width}:{height}:flags=area,format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    return rawvideo_frames(argv, width * height,
                           hang_timeout(base_s, factor, duration_s), path,
                           grace_s=base_s)


def decode_audio_seconds(path: str | Path, duration_s: float, sample_rate: int = 8000,
                         base_s: float = 60.0, factor: float = 4.0):
    """Mono s16le PCM at ``sample_rate``, yielded one second at a time.

    Streamed through the same bounded ring as every other decode: a source's
    whole PCM is 57.6 MB per hour at 8 kHz mono s16 (≈ 460 MB at 8 h, ≈ 11.5 GB
    at the 200 h corpus scale), so the buffer is one bucket plus one read chunk.
    A trailing partial second is dropped, exactly as a whole-buffer reader that
    truncated to ``len // sample_rate`` dropped it; a decoder that stalls or
    exits non-zero raises :class:`MediaError` rather than yielding a short
    series.
    """
    import numpy as np

    argv = [
        FFMPEG, "-v", "error", "-nostdin", "-threads", "1",
        "-i", str(path), "-vn", "-ac", "1", "-ar", str(sample_rate),
        "-f", "s16le", "-",
    ]
    for raw in rawvideo_frames(argv, int(sample_rate) * 2,
                               hang_timeout(base_s, factor, duration_s), path,
                               grace_s=base_s):
        yield np.frombuffer(raw, dtype="<i2")


_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?[0-9.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?[0-9.]+)")


def silencedetect(path: str | Path, duration_s: float, noise_db: float = -45.0,
                  min_duration_s: float = 3.0, base_s: float = 60.0,
                  factor: float = 4.0) -> list[tuple[float, float]]:
    """Silence gaps via ffmpeg's own ``silencedetect`` filter."""
    argv = [
        FFMPEG, "-v", "info", "-nostdin", "-threads", "1", "-i", str(path),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_duration_s}",
        "-f", "null", "-",
    ]
    proc = run_ffmpeg(argv, hang_timeout(base_s, factor, duration_s))
    text = proc.stderr.decode("utf-8", "replace")
    gaps: list[tuple[float, float]] = []
    start = None
    for line in text.splitlines():
        match = _SILENCE_START_RE.search(line)
        if match:
            start = float(match.group(1))
            continue
        match = _SILENCE_END_RE.search(line)
        if match and start is not None:
            gaps.append((start, float(match.group(1))))
            start = None
    if start is not None:
        gaps.append((start, float(duration_s)))
    return gaps


# -------------------------------------------------------------------- cutting
def scale_filter(long_edge: int) -> str:
    return (f"scale=w='if(gt(iw,ih),min(iw,{int(long_edge)}),-2)':"
            f"h='if(gt(iw,ih),-2,min(ih,{int(long_edge)}))'")


def proxy_command(src: str | Path, out: str | Path, start_s: float, duration_s: float,
                  long_edge: int, has_audio: bool) -> list[str]:
    argv = [
        FFMPEG, "-v", "error", "-nostdin", "-y", "-threads", "1",
        "-ss", f"{max(0.0, start_s):.3f}", "-t", f"{max(0.0, duration_s):.3f}",
        "-i", str(src), "-vf", scale_filter(long_edge),
        "-c:v", "libx264", "-crf", "28", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
    ]
    if has_audio:
        argv += ["-c:a", "aac", "-b:a", "96k"]
    else:
        argv += ["-an"]
    argv.append(str(out))
    return argv


def keyframe_cut_command(src: str | Path, out: str | Path, start_s: float,
                         duration_s: float) -> list[str]:
    """Stream-copy from the largest keyframe at or before the requested start."""
    return [
        FFMPEG, "-v", "error", "-nostdin", "-y", "-threads", "1",
        "-ss", f"{max(0.0, start_s):.3f}", "-i", str(src),
        "-t", f"{max(0.0, duration_s):.3f}",
        "-c", "copy", "-avoid_negative_ts", "make_zero", "-copyts",
        "-movflags", "+faststart", str(out),
    ]


def exact_cut_command(src: str | Path, out: str | Path, start_s: float,
                      duration_s: float) -> list[str]:
    """Re-encode with actual bounds equal to requested bounds (no lead-in)."""
    return [
        FFMPEG, "-v", "error", "-nostdin", "-y", "-threads", "1",
        "-ss", f"{max(0.0, start_s):.3f}", "-i", str(src),
        "-t", f"{max(0.0, duration_s):.3f}",
        "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart",
        str(out),
    ]


def output_bounds(path: str | Path, base_s: float = 60.0, factor: float = 4.0) -> dict:
    """Read back an excerpt's real start offset and duration."""
    argv = [FFPROBE, "-v", "error", "-print_format", "json",
            "-show_entries", "format=start_time,duration", str(path)]
    proc = run_ffprobe(argv, hang_timeout(base_s, factor, 0.0))
    doc = json.loads(proc.stdout.decode("utf-8"))
    fmt = doc.get("format") or {}
    start = fmt.get("start_time")
    duration = fmt.get("duration")
    return {
        "start_s": float(start) if start not in (None, "N/A") else 0.0,
        "duration_s": float(duration) if duration not in (None, "N/A") else 0.0,
    }


def _keyframe_search(path: str | Path, start_s: float, at_s: float,
                     base_s: float, factor: float) -> float | None:
    interval = f"{start_s:.3f}%+{float(at_s) - start_s + 0.5:.3f}"
    argv = [FFPROBE, "-v", "error", "-read_intervals", interval,
            "-select_streams", "v:0", "-skip_frame", "nokey",
            "-show_frames", "-show_entries", "frame=pts_time", "-of", "json",
            str(path)]
    try:
        proc = run_ffprobe(argv, hang_timeout(base_s, factor, 0.0))
        frames = json.loads(proc.stdout.decode("utf-8")).get("frames", [])
    except (MediaError, json.JSONDecodeError):
        return None
    times = []
    for frame in frames:
        value = frame.get("pts_time")
        if value in (None, "N/A"):
            continue
        try:
            times.append(float(value))
        except (TypeError, ValueError):
            continue
    candidates = [t for t in times if t <= float(at_s) + 1e-6]
    return max(candidates) if candidates else None


def keyframe_before(path: str | Path, at_s: float, search_window_s: float = 60.0,
                    base_s: float = 60.0, factor: float = 4.0) -> float | None:
    """The largest keyframe timestamp at or before ``at_s``.

    The search is bounded to ``search_window_s`` first; surveillance and DVR
    exports routinely carry GOPs far longer than a minute, so a miss escalates
    once to a full-prefix search before the caller records anything — the
    custody record's ``actual_start_ms`` must be the keyframe the cut actually
    shipped, never a guess.
    """
    start = max(0.0, float(at_s) - float(search_window_s))
    found = _keyframe_search(path, start, float(at_s), base_s, factor)
    if found is None and start > 0.0:
        found = _keyframe_search(path, 0.0, float(at_s), base_s, factor)
    return found


def lavfi_demo_command(out: str | Path) -> list[str]:
    """The §16 demo fixture: 100 s bright static + 60 s darkened moving, 160 s.

    The generator's parameters are **tuned to the measurement**, exactly as §16
    requires ("the fixture generator's parameters are tuned until that
    measurement holds").  The moving block is a sawtooth of period 120 px
    shifting 7 px per frame, so every frame is a pure translation of the same
    pattern: the per-second motion and luma of the second half are *exactly*
    constant, and the DEMO scenario's percentile assertion is a measurement, not
    a hope.  ``-crf 0`` keeps the encode lossless so the measurement is not
    polluted by quantization noise.  (The draft's ``testsrc2`` + ``eq
    brightness=-0.6`` block produced 6/7/8-milli motion and 6/7/8-milli luma —
    runs of 43 and 21 seconds — which cannot satisfy a ≥ 30 s percentile hold.)
    """
    return [
        FFMPEG, "-v", "error", "-nostdin", "-y", "-threads", "1",
        "-f", "lavfi", "-i", "color=white:s=480x270:d=100:r=24",
        "-f", "lavfi", "-i", "color=black:s=480x270:d=60:r=24",
        "-filter_complex",
        "[1]geq=lum='(mod(X+7*N,120))*0.5':cb=128:cr=128[v1];[0][v1]concat",
        "-r", "24", "-an", "-crf", "0", "-pix_fmt", "yuv420p", str(out),
    ]


def still_frame_command(src: str | Path, out: str | Path, at_s: float) -> list[str]:
    return [
        FFMPEG, "-v", "error", "-nostdin", "-y", "-threads", "1",
        "-ss", f"{max(0.0, at_s):.3f}", "-i", str(src), "-frames:v", "1", str(out),
    ]


def bounded(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def seconds_to_ms(seconds: float) -> int:
    """Round-half-even to integer milliseconds — the one rounding point."""
    scaled = float(seconds) * 1000.0
    floor = math.floor(scaled)
    remainder = scaled - floor
    if remainder > 0.5:
        return int(floor) + 1
    if remainder < 0.5:
        return int(floor)
    return int(floor) if floor % 2 == 0 else int(floor) + 1
