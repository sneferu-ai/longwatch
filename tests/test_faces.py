"""FACES — the export's face-localized pixelation (§9).

The ``blur_faces`` transform must be *face* pixelation: the YuNet-contract face
model runs on every excerpt frame, only the detected regions are mosaicked at
block 24, and the custody record carries the counts of what actually happened.
These scenarios pin the contract check (a non-YuNet model fails closed), the
decode (the fixture's declared box is found), the mosaic math (inside the box
the detail is destroyed; outside it is byte-identical), and the streaming clip
transform end to end against a real ffmpeg encode.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from longwatch import faces, media
from tests.conftest import make_video

REPO = Path(__file__).resolve().parent.parent
FACE_FIXTURE = REPO / "tests" / "fixtures" / "yunet_fixture.onnx"
DETECTOR_FIXTURE = REPO / "tests" / "fixtures" / "model_fixture.onnx"

# the fixture model declares one stride-8 face at cell (6, 10): a 32x32 box
# centred at (80, 48), grown by the 10% privacy margin
FIXTURE_BOX = (60.8, 28.8, 99.2, 67.2)


def _frame_at(path: Path, at_s: float) -> np.ndarray:
    facts = media.probe_facts(media.probe(str(path)))
    argv = [media.FFMPEG, "-v", "error", "-nostdin", "-ss", f"{at_s:.3f}",
            "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
            "-pix_fmt", "rgb24", "-"]
    proc = subprocess.run(argv, check=True, capture_output=True)
    frame_bytes = int(facts["width"]) * int(facts["height"]) * 3
    return np.frombuffer(proc.stdout[:frame_bytes], dtype=np.uint8).reshape(
        int(facts["height"]), int(facts["width"]), 3)


def _gradient_map(frame: np.ndarray) -> np.ndarray:
    gray = frame.astype(np.float64).mean(axis=2)
    return np.abs(np.diff(gray, axis=1))


def _gradient_mean(frame: np.ndarray, box: tuple[int, int, int, int]) -> float:
    x1, y1, x2, y2 = box
    return float(_gradient_map(frame)[y1:y2, x1:x2].mean())


def _block_deviation(frame: np.ndarray, box: tuple[float, float, float, float],
                     block: int = faces.FACE_BLOCK) -> float:
    """Mean |pixel − its 24×24 block mean| over a region.

    The mosaic makes every pixel of a block equal to the block mean, so this
    measures exactly the detail the transform destroys; it is alignment-free
    where a raw gradient would measure the block boundaries too.
    """
    gray = frame.astype(np.float64).mean(axis=2)
    x1, y1, x2, y2 = box
    left, top = int(np.floor(x1)), int(np.floor(y1))
    right, bottom = int(np.ceil(x2)), int(np.ceil(y2))
    region = gray[top:bottom, left:right]
    pad_h = (-region.shape[0]) % block
    pad_w = (-region.shape[1]) % block
    if pad_h or pad_w:
        region = np.pad(region, ((0, pad_h), (0, pad_w)), mode="edge")
    rows, cols = region.shape[0] // block, region.shape[1] // block
    blocks = region.reshape(rows, block, cols, block)
    means = blocks.mean(axis=(1, 3), keepdims=True)
    return float(np.abs(blocks - means).mean())


# ------------------------------------------------------------------ contract
def test_the_fixture_model_yields_the_declared_face_box():
    detector = faces.FaceDetector(FACE_FIXTURE)
    boxes = detector.detect(np.zeros((90, 160, 3), dtype=np.uint8))
    assert len(boxes) == 1
    for found, expected in zip(boxes[0], FIXTURE_BOX):
        assert found == pytest.approx(expected, abs=0.05)


def test_a_model_without_the_declared_heads_fails_closed():
    with pytest.raises(faces.FaceModelError) as excinfo:
        faces.FaceDetector(DETECTOR_FIXTURE)
    assert "YuNet heads" in str(excinfo.value)


def test_a_missing_model_fails_closed(tmp_path):
    with pytest.raises(faces.FaceModelError) as excinfo:
        faces.FaceDetector(tmp_path / "absent.onnx")
    assert "no usable face model" in str(excinfo.value)


# -------------------------------------------------------------- mosaic math
def test_pixelate_regions_mosaics_only_the_boxes():
    rng = np.random.default_rng(7)
    frame = rng.integers(0, 256, size=(80, 120, 3), dtype=np.uint8)
    box = (40, 20, 80, 60)
    out = faces.pixelate_regions(frame, [box], block=24)

    # inside: every 24x24 block is one value (the block mean)
    region = out[20:60, 40:80]
    for row in range(0, 36, 24):
        for col in range(0, 36, 24):
            block = region[row:row + 24, col:col + 24].reshape(-1, 3)
            assert block.max(axis=0).tolist() == block.min(axis=0).tolist()
    # the block mean (cast the same way the mosaic casts it) is what it preserves
    expected_block = frame[20:44, 40:64].reshape(-1, 3).mean(axis=0).astype(np.uint8)
    assert region[0, 0].tolist() == expected_block.tolist()
    assert (region[0:24, 0:24] == region[0, 0]).all()

    # outside: byte-identical
    outside = np.ones(frame.shape[:2], dtype=bool)
    outside[20:60, 40:80] = False
    assert (out[outside] == frame[outside]).all()


def test_pixelate_regions_without_boxes_is_a_pure_copy():
    frame = np.arange(3 * 4 * 3, dtype=np.uint8).reshape(4, 3, 3)
    out = faces.pixelate_regions(frame, [], block=24)
    assert (out == frame).all()


# --------------------------------------------------------- the clip transform
def test_pixelate_clip_mosaics_the_face_and_keeps_the_rest(tmp_path, cfg, require_ffmpeg):
    """The fixture's declared face region loses its detail; the rest survives."""
    source = make_video(tmp_path / "face-source.mp4", seconds=4.0)
    out = tmp_path / "face-blurred.mp4"
    detector = faces.FaceDetector(FACE_FIXTURE)
    counts = faces.pixelate_clip(source, out, detector, cfg)
    assert counts["frames_processed"] >= 8
    assert counts["regions_pixelated"] == counts["frames_processed"]  # one face per frame
    assert counts["max_faces_per_frame"] == 1

    original = _frame_at(source, 1.0)
    blurred = _frame_at(out, 1.0)
    face_before = _block_deviation(original, FIXTURE_BOX)
    face_after = _block_deviation(blurred, FIXTURE_BOX)
    assert face_after < face_before / 4.0, (face_before, face_after)

    # a rectangle far from the face box: the re-encode keeps its structure
    outside = (4, 4, 56, 26)
    outside_before = _gradient_mean(original, outside)
    outside_after = _gradient_mean(blurred, outside)
    assert 0.6 < outside_after / outside_before < 1.6, (outside_before, outside_after)


def test_pixelate_clip_fails_loudly_on_an_unreadable_clip(tmp_path, cfg, require_ffmpeg):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"this is not a video stream" * 32)
    detector = faces.FaceDetector(FACE_FIXTURE)
    with pytest.raises(faces.FaceModelError):
        faces.pixelate_clip(broken, tmp_path / "out.mp4", detector, cfg)


def test_pixelate_clip_keeps_the_excerpts_audio(tmp_path, cfg, require_ffmpeg):
    """Pixelation is a video-region transform: the sound rides along."""
    from tests.test_media import make_video_with_audio

    source = make_video_with_audio(tmp_path / "voiced.mp4", seconds=4.0, tone_from_s=2.0)
    out = tmp_path / "voiced-blurred.mp4"
    faces.pixelate_clip(source, out, faces.FaceDetector(FACE_FIXTURE), cfg)
    facts = media.probe_facts(media.probe(str(out)))
    assert facts["has_audio"] is True
