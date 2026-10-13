"""Face localization for the export's optional pixelation (§9).

``blur_faces`` is *face* pixelation: the YuNet face model — Apache-2.0,
fetched at image-build time, digest-pinned — locates the faces in each excerpt
frame, and only those regions are mosaicked at block 24.  Every other pixel of
the excerpt keeps its evidentiary content, and the custody record states what
actually happened: the operation, the block size, the face-model identity and
the number of regions pixelated.

Model contract (the OpenCV Zoo YuNet export, ``face_detection_yunet_2023mar``):

* input ``[1, 3, H, W]`` float32, BGR channel order, raw 0–255 pixel scale;
* twelve heads at strides 8/16/32 — ``cls_<s>``/``obj_<s>`` ``[1, 1, h, w]``,
  ``bbox_<s>`` ``[1, 4, h, w]``, ``kps_<s>`` ``[1, 10, h, w]``;
* anchor-free decode, one prior per cell: ``score = sqrt(cls·obj)`` and
  ``cx = (j + dx)·s``, ``cy = (i + dy)·s``, ``w = e^{dw}·s``, ``h = e^{dh}·s``.

A model whose heads do not match that contract **fails closed with a named
error** — the export refuses rather than shipping an excerpt it did not
pixelate (the same rule §9 applies to a missing model).

The clip transform streams: one frame in flight, decoded through the shared
bounded ring, mosaicked in place and written straight into the encoder's stdin
— the excerpt's pixels are never all resident.
"""
from __future__ import annotations

import math
import os
import select
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np

from . import media

FACE_BLOCK = 24
"""The §9 mosaic block size, in pixels."""

FACE_SCORE_THRESHOLD = 0.60
FACE_NMS_IOU = 0.30
FACE_BOX_MARGIN = 0.10
"""Grow each detected box by this fraction of its size — over-cover is the
safe direction for bystander privacy; the box a detector reports can clip a
chin or hairline."""

FACE_PAD = 32
"""YuNet's heads stride at 8/16/32, so both input sides must be multiples of 32."""

FACE_FRAME_BUDGET_S = 0.5
"""Per-frame wall-clock allowance added to the clip's hang guard: CPU YuNet
inference plus the mosaic, with a wide margin over the ~10–50 ms measured."""

STRIDES = (8, 16, 32)
HEAD_NAMES = tuple(f"{kind}_{stride}" for kind in ("cls", "obj", "bbox", "kps")
                   for stride in STRIDES)


class FaceModelError(RuntimeError):
    """The face model is absent, unusable, or fails its declared contract."""


def _nms(boxes: list[tuple[float, float, float, float]], scores: list[float],
         iou_threshold: float) -> list[int]:
    order = sorted(range(len(boxes)), key=lambda index: scores[index], reverse=True)
    kept: list[int] = []
    while order:
        best = order.pop(0)
        kept.append(best)
        bx1, by1, bx2, by2 = boxes[best]
        area = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
        survivors = []
        for other in order:
            ox1, oy1, ox2, oy2 = boxes[other]
            ix = max(0.0, min(bx2, ox2) - max(bx1, ox1))
            iy = max(0.0, min(by2, oy2) - max(by1, oy1))
            inter = ix * iy
            oarea = max(0.0, ox2 - ox1) * max(0.0, oy2 - oy1)
            union = area + oarea - inter
            if union <= 0.0 or inter / union <= iou_threshold:
                survivors.append(other)
        order = survivors
    return kept


class FaceDetector:
    """The YuNet face model, contract-checked at load and per inference."""

    def __init__(self, model_path: str | Path, *, score_threshold: float = FACE_SCORE_THRESHOLD,
                 nms_iou: float = FACE_NMS_IOU, expected_sha256: str | None = None):
        self.model_path = Path(model_path)
        if not self.model_path.is_file():
            raise FaceModelError(f"no usable face model is present at {self.model_path}")
        if expected_sha256:
            actual = media.sha256_file(self.model_path)
            if actual != expected_sha256:
                raise FaceModelError(
                    f"face model digest mismatch: expected {expected_sha256}, got {actual}")
        self.score_threshold = float(score_threshold)
        self.nms_iou = float(nms_iou)
        try:
            import onnxruntime as ort
        except ImportError as exc:  # pragma: no cover - the image installs it
            raise FaceModelError(f"onnxruntime is unavailable: {exc}") from exc
        try:
            self.session = ort.InferenceSession(str(self.model_path),
                                                providers=["CPUExecutionProvider"])
        except Exception as exc:  # noqa: BLE001 - any load failure is a named refusal
            raise FaceModelError(f"face model could not be loaded: {exc}") from exc
        self._input = self.session.get_inputs()[0]
        self._output_names = [output.name for output in self.session.get_outputs()]
        self._check_contract()

    def _check_contract(self) -> None:
        shape = list(self._input.shape)
        if len(shape) != 4 or shape[0] != 1 or shape[1] != 3:
            raise FaceModelError(
                f"face model input must be [1,3,H,W] float32, got {shape}")
        if "float" not in str(self._input.type):
            raise FaceModelError(
                f"face model input must be float32, got {self._input.type}")
        missing = [name for name in HEAD_NAMES if name not in self._output_names]
        if missing:
            raise FaceModelError(
                "face model does not expose the declared YuNet heads; missing "
                + ", ".join(missing))
        self._head_index = {name: index for index, name in enumerate(self._output_names)}

    def contract(self) -> dict:
        """The declared, validated identity of the loaded face model."""
        return {
            "model": str(self.model_path),
            "input": {"name": self._input.name, "shape": list(self._input.shape),
                      "type": self._input.type},
            "heads": list(HEAD_NAMES),
            "score_threshold": self.score_threshold,
            "nms_iou": self.nms_iou,
        }

    # -------------------------------------------------------------- inference
    def detect(self, frame_bgr: np.ndarray) -> list[tuple[float, float, float, float]]:
        """The face boxes (x1, y1, x2, y2) in the frame's own pixel coordinates."""
        if frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3:
            raise FaceModelError(f"frame must be H×W×3 BGR, got {frame_bgr.shape}")
        height, width = int(frame_bgr.shape[0]), int(frame_bgr.shape[1])
        image = frame_bgr
        pad_h = (-height) % FACE_PAD
        pad_w = (-width) % FACE_PAD
        if pad_h or pad_w:
            # edge replication: a padded border never invents a face at the edge
            image = np.pad(frame_bgr, ((0, pad_h), (0, pad_w), (0, 0)), mode="edge")
        blob = np.ascontiguousarray(image.transpose(2, 0, 1)[None, ...], dtype=np.float32)
        outputs = self.session.run(self._output_names, {self._input.name: blob})
        return self._decode(outputs, width, height)

    def _decode(self, outputs: list, width: int, height: int
                ) -> list[tuple[float, float, float, float]]:
        boxes: list[tuple[float, float, float, float]] = []
        scores: list[float] = []
        for stride in STRIDES:
            cls = np.asarray(outputs[self._head_index[f"cls_{stride}"]], dtype=np.float64)
            obj = np.asarray(outputs[self._head_index[f"obj_{stride}"]], dtype=np.float64)
            bbox = np.asarray(outputs[self._head_index[f"bbox_{stride}"]], dtype=np.float64)
            if cls.ndim != 4 or cls.shape[0] != 1 or cls.shape[1] != 1:
                raise FaceModelError(
                    f"cls_{stride} must be [1,1,h,w], got {list(cls.shape)}")
            if bbox.ndim != 4 or bbox.shape[1] != 4:
                raise FaceModelError(
                    f"bbox_{stride} must be [1,4,h,w], got {list(bbox.shape)}")
            score = np.sqrt(np.clip(cls[0, 0], 0.0, 1.0) * np.clip(obj[0, 0], 0.0, 1.0))
            rows, cols = np.nonzero(score >= self.score_threshold)
            for row, col in zip(rows.tolist(), cols.tolist()):
                dx, dy, dw, dh = (float(value) for value in bbox[0, :, row, col])
                cx = (col + dx) * stride
                cy = (row + dy) * stride
                box_w = math.exp(min(max(dw, -8.0), 8.0)) * stride
                box_h = math.exp(min(max(dh, -8.0), 8.0)) * stride
                x1, y1 = cx - box_w / 2.0, cy - box_h / 2.0
                x2, y2 = cx + box_w / 2.0, cy + box_h / 2.0
                margin_x, margin_y = (x2 - x1) * FACE_BOX_MARGIN, (y2 - y1) * FACE_BOX_MARGIN
                x1, y1 = max(0.0, x1 - margin_x), max(0.0, y1 - margin_y)
                x2, y2 = min(float(width), x2 + margin_x), min(float(height), y2 + margin_y)
                if x2 - x1 < 1.0 or y2 - y1 < 1.0:
                    continue
                boxes.append((x1, y1, x2, y2))
                scores.append(float(score[row, col]))
        kept = _nms(boxes, scores, self.nms_iou)
        return [boxes[index] for index in kept]


def pixelate_regions(frame: np.ndarray, boxes: list[tuple[float, float, float, float]],
                     block: int = FACE_BLOCK) -> np.ndarray:
    """Return a copy of ``frame`` with each box region replaced by a block mosaic.

    Each block becomes the mean of the pixels it covers — the same operation
    the documented block-24 mosaic performs, localized to the face regions.
    Pixels outside every box are byte-identical to the input.
    """
    out = np.array(frame, dtype=np.uint8, copy=True)
    block = max(2, int(block))
    height, width = out.shape[:2]
    for x1, y1, x2, y2 in boxes:
        left = max(0, int(math.floor(x1)))
        top = max(0, int(math.floor(y1)))
        right = min(width, int(math.ceil(x2)))
        bottom = min(height, int(math.ceil(y2)))
        if right - left < 1 or bottom - top < 1:
            continue
        region = out[top:bottom, left:right]
        pad_h = (-region.shape[0]) % block
        pad_w = (-region.shape[1]) % block
        if pad_h or pad_w:
            region = np.pad(region, ((0, pad_h), (0, pad_w), (0, 0)), mode="edge")
        rows, cols = region.shape[0] // block, region.shape[1] // block
        blocks = region.reshape(rows, block, cols, block, 3).mean(axis=(1, 3))
        mosaic = np.repeat(np.repeat(blocks, block, axis=0), block, axis=1)
        out[top:bottom, left:right] = mosaic[:bottom - top, :right - left].astype(np.uint8)
    return out


# ------------------------------------------------------------------- transform
def _write_all(handle, payload: bytes, deadline: float, src: str | Path) -> None:
    """Write a frame into the encoder's raw stdin, bounded by the deadline."""
    view = memoryview(payload)
    while view:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FaceModelError(
                f"face pixelation exceeded its wall-clock bound writing {src}")
        _, writable, _ = select.select([], [handle], [], min(remaining, 1.0))
        if not writable:
            continue
        written = os.write(handle.fileno(), view[:1 << 16])
        view = view[written:]


def pixelate_clip(src: str | Path, out: str | Path, detector: FaceDetector, cfg,
                  *, block: int = FACE_BLOCK) -> dict:
    """Rewrite ``src`` as ``out`` with only the detected faces mosaicked.

    One frame in flight: the decode streams through :func:`media.rawvideo_frames`
    (the same bounded ring the signals pass and the detector use), each frame is
    passed through the face model, its regions mosaicked, and the bytes are
    written straight to the encoder.  The excerpt's audio track is stream-copied
    into the output.  Returns the counts the custody record carries —
    ``{"frames_processed", "regions_pixelated", "max_faces_per_frame"}``.
    """
    src = str(src)
    try:
        info = media.probe(src, cfg.ffmpeg_hang_base_s, cfg.ffmpeg_hang_factor)
    except media.MediaError as exc:
        raise FaceModelError(f"cannot pixelate {src}: {exc}") from exc
    facts = media.probe_facts(info)
    width, height = facts["width"], facts["height"]
    if not width or not height:
        raise FaceModelError(f"cannot pixelate {src}: no decodable video stream")
    fps = float(facts["fps"] or 25.0)
    duration = float(facts["duration_s"] or 0.0)
    frame_bytes = width * height * 3
    decode_argv = [
        media.FFMPEG, "-v", "error", "-nostdin", "-threads", "1",
        "-i", src, "-f", "rawvideo", "-pix_fmt", "bgr24", "-",
    ]
    encode_argv = [
        media.FFMPEG, "-v", "error", "-nostdin", "-y", "-threads", "1",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
        "-r", f"{fps:.6f}", "-i", "-",
        # the excerpt's own audio rides along (stream-copied): pixelation is a
        # video-region transform, and dropping the sound would destroy evidence
        # the option never claimed to touch
        "-i", src, "-map", "0:v:0", "-map", "1:a?",
        "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest",
        "-movflags", "+faststart", str(out),
    ]
    frames_budget = max(1, int(round(duration * fps)))
    timeout_s = media.hang_timeout(cfg.ffmpeg_hang_base_s, cfg.ffmpeg_hang_factor,
                                   duration) + frames_budget * FACE_FRAME_BUDGET_S
    deadline = time.monotonic() + timeout_s
    stderr_file = tempfile.TemporaryFile()
    encoder = subprocess.Popen(encode_argv, stdin=subprocess.PIPE, bufsize=0,
                               stdout=subprocess.DEVNULL, stderr=stderr_file)
    frames = 0
    regions = 0
    max_faces = 0
    failed = True
    try:
        for raw in media.rawvideo_frames(decode_argv, frame_bytes, timeout_s, src,
                                         grace_s=cfg.ffmpeg_hang_base_s):
            if time.monotonic() > deadline:
                raise FaceModelError(
                    f"face pixelation exceeded its {timeout_s:.1f}s wall-clock bound on {src}")
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
            boxes = detector.detect(frame)
            if boxes:
                payload = pixelate_regions(frame, boxes, block).tobytes()
                regions += len(boxes)
                max_faces = max(max_faces, len(boxes))
            else:
                payload = raw
            _write_all(encoder.stdin, payload, deadline, src)
            frames += 1
        try:
            encoder.stdin.close()
        except OSError:
            pass
        try:
            returncode = encoder.wait(timeout=media.hang_timeout(
                cfg.ffmpeg_hang_base_s, cfg.ffmpeg_hang_factor, 0.0))
        except subprocess.TimeoutExpired:
            encoder.kill()
            raise FaceModelError(
                f"the face-pixelation encoder did not exit within its hang guard on {src}")
        if returncode != 0:
            raise FaceModelError(
                f"face-pixelation encode exited {returncode} on {src}: "
                + _stderr_tail(stderr_file))
        failed = False
    except media.MediaError as exc:
        raise FaceModelError(f"face pixelation could not decode {src}: {exc}") from exc
    except (BrokenPipeError, OSError, ValueError) as exc:
        raise FaceModelError(
            "the face-pixelation encoder exited before the stream ended: "
            + _stderr_tail(stderr_file)) from exc
    finally:
        if failed:
            try:
                encoder.stdin.close()
            except (OSError, ValueError):
                pass
            if encoder.poll() is None:
                encoder.kill()
            try:
                encoder.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        stderr_file.close()
    return {"frames_processed": frames, "regions_pixelated": regions,
            "max_faces_per_frame": max_faces}


def _stderr_tail(handle) -> str:
    try:
        handle.seek(0)
        return handle.read().decode("utf-8", "replace")[-4000:]
    except OSError:
        return ""
