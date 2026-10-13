"""MODEL PINS — the image build fetches the real weights, and says so honestly.

The blocker this file pins: a no-build-args image build must fetch the
canonical YOLOv8n and YuNet artifacts — not silently ship the in-repo contract
fixture — and the provenance it records must distinguish verified (`fetched`),
fetched-but-unpinned (`fetched_unpinned`) and fixture builds.  `models/pins.json`
carries the sources and digest pins, `models/resolve_pin.py` resolves a
build-arg over the pins, and the entrypoint exports the recorded digests so the
runtime verifies what shipped.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PINS = REPO / "models" / "pins.json"
RESOLVE = REPO / "models" / "resolve_pin.py"
DOCKERFILE = REPO / "Dockerfile"
ENTRYPOINT = REPO / "scripts" / "entrypoint.sh"
PIN_SCRIPT = REPO / "scripts" / "pin_models.sh"


def _load_resolver():
    spec = importlib.util.spec_from_file_location("longwatch_resolve_pin", RESOLVE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pins_declare_both_artifacts_with_canonical_https_sources():
    document = json.loads(PINS.read_text(encoding="utf-8"))
    rows = {row["name"]: row for row in document["models"]}
    assert set(rows) == {"yolov8n.onnx", "yunet.onnx"}
    for row in rows.values():
        assert row["urls"], row["name"]
        for url in row["urls"]:
            assert url.startswith("https://"), url
        # a pin is either absent (honest unpinned) or a full sha256
        assert row["sha256"] == "" or re.fullmatch(r"[0-9a-f]{64}", row["sha256"]), row["sha256"]


def test_build_args_override_the_pins_file_and_the_file_is_the_fallback():
    resolver = _load_resolver()
    assert resolver.resolve("yolov8n.onnx", "url") == [
        "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.onnx",
        "https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8n.onnx",
    ]
    assert resolver.resolve("yunet.onnx", "url", "https://example.invalid/yunet.onnx") == [
        "https://example.invalid/yunet.onnx"]
    assert resolver.resolve("yunet.onnx", "sha256") == [""]
    assert resolver.resolve("yunet.onnx", "sha256", "a" * 64) == ["a" * 64]
    assert resolver.resolve("absent.onnx", "url") == []


def test_the_no_build_args_image_fetches_the_real_artifacts():
    """The Dockerfile's own defaults must be the canonical sources — the
    launch-proof build passes no build-args."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(
        r'ARG LONGWATCH_MODEL_URL="https://github\.com/ultralytics/assets/[^"]+\.onnx"', text)
    assert re.search(
        r'ARG LONGWATCH_FACE_MODEL_URL="https://github\.com/opencv/opencv_zoo/[^"]+\.onnx"', text)
    assert "resolve_pin.py" in text, "the models stage must resolve the pins file"
    assert "MODEL_PROVENANCE" in text
    for provenance in ("fetched", "fetched_unpinned", "fixture"):
        assert provenance in text, provenance


def test_the_entrypoint_exports_the_recorded_digests():
    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert "MODEL_SHA256" in text and "FACE_MODEL_SHA256" in text
    assert "LONGWATCH_MODEL_SHA256" in text and "LONGWATCH_FACE_MODEL_SHA256" in text


def test_the_pin_script_writes_the_pins_file():
    text = PIN_SCRIPT.read_text(encoding="utf-8")
    assert "--write" in text
    assert "sha256sum" in text
    assert "pins.json" in text
    assert PIN_SCRIPT.stat().st_mode & 0o111, "pin_models.sh must be executable"
