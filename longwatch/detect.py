"""Detection (§8.3) — CPU ONNX, motion-adaptive sampling, never skipped.

The detector reads **the original file, not the proxy**: each window is decoded
from the original with ``-ss <window_start> -t <len>`` and output thinned to
the sampling rate.  Presence timestamps therefore bind to the original
timeline without proxy re-encode drift.

A window is never skipped for lacking motion — stationary content is exactly
what the flagship pattern looks for; throughput is recovered by sampling static
windows sparsely (0.5 fps), not by not looking.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import classmap as classmap_mod
from . import media

LETTERBOX_SIZE = 640
DETECTIONS_BLOB_CAP = 128 * 1024
SUMMARY_CAP = 2 * 1024


class ModelContractError(RuntimeError):
    """The model does not satisfy the declared contract — fail closed, named."""


class MissingSource(RuntimeError):
    """The source file is absent — the pre-stat rule fires, no ffmpeg runs."""


@dataclass(frozen=True)
class FrameDetection:
    class_name: str
    conf_milli: int
    box: tuple[float, float, float, float]


@dataclass
class WindowDetection:
    intervals: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    detections_blob: dict | None = None
    sample_interval_ms: int = 0
    sampling_fps: float = 0.0
    frames: int = 0


def letterbox_params(width: int, height: int, size: int = LETTERBOX_SIZE) -> dict:
    scale = min(size / float(width), size / float(height))
    new_w, new_h = width * scale, height * scale
    return {"scale": scale, "pad_x": (size - new_w) / 2.0, "pad_y": (size - new_h) / 2.0,
            "width": width, "height": height, "size": size}


def unletterbox(box, params) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = box
    scale = params["scale"]
    return (
        max(0.0, (x1 - params["pad_x"]) / scale),
        max(0.0, (y1 - params["pad_y"]) / scale),
        max(0.0, (x2 - params["pad_x"]) / scale),
        max(0.0, (y2 - params["pad_y"]) / scale),
    )


def letterbox_filter(size: int = LETTERBOX_SIZE) -> str:
    # 0x727272 is the conventional letterbox gray (114, 114, 114)
    return (f"scale={size}:{size}:force_original_aspect_ratio=decrease,"
            f"pad={size}:{size}:(ow-iw)/2:(oh-ih)/2:color=0x727272")


def iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def nms(detections: list[FrameDetection], iou_threshold: float) -> list[FrameDetection]:
    kept: list[FrameDetection] = []
    for name in sorted({d.class_name for d in detections}):
        group = sorted((d for d in detections if d.class_name == name),
                       key=lambda d: d.conf_milli, reverse=True)
        while group:
            best = group.pop(0)
            kept.append(best)
            group = [d for d in group if iou(best.box, d.box) <= iou_threshold]
    return kept


def postprocess(raw: np.ndarray, names: list[str], conf_min: float,
                iou_threshold: float, params: dict) -> list[FrameDetection]:
    """Map output index → name via the map, then filter by name."""
    array = np.asarray(raw)
    if array.ndim == 3:
        array = array[0]
    if array.ndim != 2:
        raise ModelContractError(f"unexpected detector output rank {array.ndim}")
    channels = 4 + len(names)
    if array.shape[1] == channels:
        pass
    elif array.shape[0] == channels:
        array = array.T  # (M, 4+N)
    else:
        raise ModelContractError(
            f"detector output {array.shape} matches neither (M, {channels}) nor "
            f"({channels}, M) — the declared class map has {len(names)} names")
    boxes = array[:, :4]
    scores = array[:, 4:]
    class_ids = scores.argmax(axis=1)
    confidences = scores[np.arange(scores.shape[0]), class_ids]
    threshold = int(round(float(conf_min) * 1000.0))
    results: list[FrameDetection] = []
    for index in np.where(confidences * 1000.0 >= threshold)[0]:
        class_index = int(class_ids[index])
        if class_index >= len(names):
            raise ModelContractError(
                f"detector emitted class index {class_index} beyond the declared map")
        cx, cy, w, h = (float(v) for v in boxes[index])
        box = unletterbox((cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0), params)
        results.append(FrameDetection(
            class_name=names[class_index],
            conf_milli=int(round(float(confidences[index]) * 1000.0)),
            box=box,
        ))
    return nms(results, iou_threshold)


def _percentile_milli(values: list[int], percentile: int) -> int:
    if not values:
        return 0
    ordered = sorted(int(v) for v in values)
    index = math.ceil(percentile / 100.0 * len(ordered)) - 1
    return ordered[max(0, min(len(ordered) - 1, index))]


def aggregate_frames(frame_detections: list[list[FrameDetection]], times_s: list[float],
                     sample_interval_s: float, retained: tuple[str, ...]) -> WindowDetection:
    """Per-class aggregates (bounded) and presence intervals (complete)."""
    summary: dict[str, dict] = {}
    intervals: list[dict] = []
    samples = len(frame_detections)
    interval_ms = int(round(sample_interval_s * 1000.0))
    for name in retained:
        present_flags = []
        confidences = []
        for detections in frame_detections:
            confs = [d.conf_milli for d in detections if d.class_name == name]
            present_flags.append(bool(confs))
            if confs:
                confidences.append(max(confs))
        positives = sum(1 for flag in present_flags if flag)
        summary[name] = {
            "positive_frames": positives,
            "conf_max_milli": max(confidences) if confidences else 0,
            "conf_p50_milli": _percentile_milli(confidences, 50),
            "samples": samples,
            "sample_interval_ms": interval_ms,
        }
        run_start = None
        for index, flag in enumerate(present_flags + [False]):
            if flag and run_start is None:
                run_start = index
            elif not flag and run_start is not None:
                start = times_s[run_start] if run_start < len(times_s) else 0.0
                last = min(index - 1, len(times_s) - 1)
                end = (times_s[last] if last >= 0 else start) + sample_interval_s
                confs = [d.conf_milli for frame in frame_detections[run_start:index]
                         for d in frame if d.class_name == name]
                intervals.append({
                    "class_name": name,
                    "t_start": float(start),
                    "t_end": float(end),
                    "samples": index - run_start,
                    "conf_max_milli": max(confs) if confs else 0,
                })
                run_start = None
    return WindowDetection(intervals=intervals, summary=summary,
                           sample_interval_ms=interval_ms, frames=samples)


def merge_intervals(intervals: list[dict], default_tolerance_s: float) -> list[dict]:
    """The continuity tolerance and the boundary rule of §8.3.

    Presence intervals of the same class are merged when the gap is ≤ the
    maximum of the two adjacent intervals' tolerances; the tolerance used is
    recorded on the merged interval.
    """
    merged: list[dict] = []
    for name in sorted({i["class_name"] for i in intervals}):
        group = sorted((dict(i) for i in intervals if i["class_name"] == name),
                       key=lambda i: i["t_start"])
        current: dict | None = None
        for interval in group:
            tolerance = float(interval.get("tolerance_s", default_tolerance_s))
            interval["tolerance_s"] = tolerance
            interval.setdefault("tolerance_used_s", tolerance)
            if current is None:
                current = interval
                continue
            gap = float(interval["t_start"]) - float(current["t_end"])
            allowed = max(float(current.get("tolerance_s", tolerance)), tolerance)
            if gap <= allowed:
                current["t_end"] = max(float(current["t_end"]), float(interval["t_end"]))
                current["samples"] = int(current.get("samples", 0)) + int(interval.get("samples", 0))
                current["conf_max_milli"] = max(int(current.get("conf_max_milli", 0)),
                                                int(interval.get("conf_max_milli", 0)))
                current["tolerance_used_s"] = allowed
                current["synthetic"] = bool(current.get("synthetic")) or bool(interval.get("synthetic"))
                current["window_ids"] = sorted(set(current.get("window_ids", []) + interval.get("window_ids", [])))
            else:
                merged.append(current)
                current = interval
        if current is not None:
            merged.append(current)
    return merged


def sampling_fps_for(motion_milli: int, cfg) -> float:
    """Motion-adaptive sampling rate — never a skip gate."""
    if motion_milli <= int(round(cfg.static_motion * 1000.0)):
        return float(cfg.static_fps)
    return float(cfg.detect_fps_clamped)


class Detector:
    """The ONNX Runtime CPU session (sequential, 1 intra-op thread)."""

    def __init__(self, cfg, class_map_path: str | None = None):
        self.cfg = cfg
        self.names = classmap_mod.load_class_map(class_map_path or cfg.class_map_path)
        self._session = None
        self._contract: dict | None = None

    # ------------------------------------------------------------- lifecycle
    @property
    def session(self):
        if self._session is None:
            self._session = self._create_session()
        return self._session

    def _create_session(self):
        path = Path(self.cfg.model_path)
        if not path.is_file():
            raise ModelContractError(f"detector model not found at {path}")
        if self.cfg.model_sha256:
            actual = media.sha256_file(path)
            if actual != self.cfg.model_sha256:
                raise ModelContractError(
                    f"detector digest mismatch: expected {self.cfg.model_sha256}, got {actual}")
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = int(self.cfg.ort_threads)
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        return ort.InferenceSession(str(path), sess_options=options,
                                    providers=["CPUExecutionProvider"])

    def contract(self) -> dict:
        """Validate the full declared contract: shapes, map length, retained names."""
        session = self.session
        inputs = session.get_inputs()
        outputs = session.get_outputs()
        if not inputs or not outputs:
            raise ModelContractError("model declares no inputs or outputs")
        spec = inputs[0]
        shape = list(spec.shape)
        if len(shape) != 4 or shape[1] != 3:
            raise ModelContractError(f"input tensor must be [1,3,640,640], got {shape}")
        for dim in shape[2:]:
            if isinstance(dim, int) and dim != LETTERBOX_SIZE:
                raise ModelContractError(f"input spatial dims must be 640, got {shape}")
        if "float" not in str(spec.type):
            raise ModelContractError(f"input tensor must be float32, got {spec.type}")
        out_shape = list(outputs[0].shape)
        if len(out_shape) != 3:
            raise ModelContractError(f"output tensor must be [1,4+N,M], got {out_shape}")
        channels = out_shape[1]
        if not isinstance(channels, int):
            raise ModelContractError("output channel dimension must be static")
        class_count = channels - 4
        if class_count != len(self.names):
            raise ModelContractError(
                f"class map declares {len(self.names)} names but the model exposes {class_count}")
        try:
            classmap_mod.validate_class_map(self.names)
        except classmap_mod.ClassMapError as exc:
            raise ModelContractError(str(exc)) from exc
        self._contract = {
            "input": {"name": spec.name, "shape": shape, "type": spec.type},
            "output": {"name": outputs[0].name, "shape": out_shape},
            "classes": class_count,
            "class_map": str(self.cfg.class_map_path),
            "ort_threads": int(self.cfg.ort_threads),
            "model": str(self.cfg.model_path),
        }
        return self._contract

    def self_test(self) -> dict:
        """One-tensor model self-test — the readiness half of /healthz."""
        contract = self.contract()
        session = self.session
        tensor = np.zeros((1, 3, LETTERBOX_SIZE, LETTERBOX_SIZE), dtype=np.float32)
        outputs = session.run(None, {contract["input"]["name"]: tensor})
        return {
            "ok": True,
            "output_shape": list(np.asarray(outputs[0]).shape),
            "classes": contract["classes"],
        }

    # -------------------------------------------------------------- inference
    def detect_frame(self, frame: np.ndarray, params: dict) -> list[FrameDetection]:
        contract = self.contract()
        tensor = np.asarray(frame, dtype=np.float32)
        if tensor.ndim == 2:
            tensor = np.stack([tensor] * 3, axis=-1)
        # The decode filter emits RGB; the model contract is RGB, float32, 1/255.
        tensor = np.ascontiguousarray(tensor.transpose(2, 0, 1)[None, ...]) / 255.0
        outputs = self.session.run(None, {contract["input"]["name"]: tensor})
        return postprocess(outputs[0], self.names, self.cfg.conf_min, self.cfg.nms_iou, params)

    def decode_window(self, path: str, start_s: float, duration_s: float,
                      sampling_fps: float):
        """Decode one window from the original at the sampling rate, one frame at a time.

        The stream is consumed through the same bounded ring as every other
        decode (§5): a 180 s motion-active window at the 2 fps default is
        360 × 640×640×3 ≈ 442 MB if buffered whole — and the old reader then
        copied every frame again out of that buffer — so frames are yielded as
        ffmpeg produces them and the caller consumes each one before the next
        arrives.  A stalled decoder is killed at the same
        ``base + factor × input`` hang guard every invocation carries; a
        non-zero exit raises rather than silently shortening the window.
        """
        if not Path(path).is_file():
            raise MissingSource(f"source file {path} is absent")
        argv = [
            media.FFMPEG, "-v", "error", "-nostdin", "-threads", "1",
            "-ss", f"{max(0.0, start_s):.3f}", "-t", f"{max(0.0, duration_s):.3f}",
            "-i", str(path),
            "-vf", f"fps={sampling_fps},{letterbox_filter()}",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ]
        frame_bytes = LETTERBOX_SIZE * LETTERBOX_SIZE * 3
        timeout_s = media.hang_timeout(self.cfg.ffmpeg_hang_base_s,
                                       self.cfg.ffmpeg_hang_factor, duration_s)
        for raw in media.rawvideo_frames(argv, frame_bytes, timeout_s, path,
                                         grace_s=self.cfg.ffmpeg_hang_base_s):
            yield np.frombuffer(raw, dtype=np.uint8).reshape(
                LETTERBOX_SIZE, LETTERBOX_SIZE, 3)

    def run_window(self, path: str, window: dict, motion_milli: int,
                   width: int, height: int) -> WindowDetection:
        start = float(window["start_s"])
        duration = float(window["end_s"]) - start
        sampling_fps = sampling_fps_for(motion_milli, self.cfg)
        params = letterbox_params(width, height)
        interval_s = 1.0 / sampling_fps if sampling_fps > 0 else 1.0
        times: list[float] = []
        per_frame: list[list[FrameDetection]] = []
        for index, frame in enumerate(
                self.decode_window(path, start, duration, sampling_fps)):
            times.append(start + index * interval_s)
            per_frame.append(self.detect_frame(frame, params))
        result = aggregate_frames(per_frame, times, interval_s, classmap_mod.RETAINED_NAMES)
        result.sampling_fps = sampling_fps
        if self.cfg.store_frames:
            result.detections_blob = _bounded_blob(per_frame, times)
        return result


def _bounded_blob(per_frame: list[list[FrameDetection]], times: list[float]) -> dict:
    """The opt-in debug blob, hard-capped at 128 KiB with truncation recorded."""
    records = []
    overflow = 0
    for index, detections in enumerate(per_frame):
        record = {
            "t_ms": int(round(times[index] * 1000.0)) if index < len(times) else 0,
            "detections": [
                {"class": d.class_name, "conf_milli": d.conf_milli,
                 "box_milli": [int(round(v * 1000.0)) for v in d.box]}
                for d in detections
            ],
        }
        candidate = {"frames": records + [record], "truncated": False}
        if len(json.dumps(candidate)) > DETECTIONS_BLOB_CAP:
            overflow = len(per_frame) - index
            break
        records.append(record)
    blob: dict = {"frames": records}
    if overflow:
        blob["truncated"] = True
        blob["overflow"] = overflow
    return blob
