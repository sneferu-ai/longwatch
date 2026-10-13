"""MODELS STAGE — the Dockerfile's fetch/provenance logic, exercised without Docker.

The construction environment has no Docker daemon, so the image build itself
cannot run here; this harness extracts the models stage's shell script from the
Dockerfile and runs it against a stubbed ``curl``, so every branch the reviewer
blocked on is a test:

* the no-build-args defaults fetch the canonical URLs (never the fixture);
* a matching pin verifies and stamps ``fetched``;
* a missing pin still fetches and stamps ``fetched_unpinned`` (honest);
* a mismatching pin fails the build (exit 2);
* a network-less fetch falls back to the **committed** contract fixture by a
  plain file copy — the stub ``python3`` fails the run if the stage ever shells
  out to pip, so the offline fallback cannot regress into a network call;
* the detector and face fetches are independent: a dead detector URL never
  costs the build its face model (and vice versa).
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO / "Dockerfile"
MODELS = REPO / "models"
MODEL_FIXTURE = REPO / "tests" / "fixtures" / "model_fixture.onnx"

STAGE_START = "FROM python:3.12-bookworm AS models"
STAGE_END = "# ------------------------------------------------------------ runtime stage"
DETECTOR_BYTES = b"a stand-in for the pinned yolov8n weights\n"
FACE_BYTES = b"a stand-in for the pinned yunet weights\n"


def _models_stage_block() -> str:
    text = DOCKERFILE.read_text(encoding="utf-8")
    return text[text.index(STAGE_START):text.index(STAGE_END)]


def _stage_script() -> str:
    block = _models_stage_block()
    lines = block.splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("RUN "))
    script = [lines[start][len("RUN "):]]
    index = start
    while script[-1].rstrip().endswith("\\"):
        index += 1
        script.append(lines[index])
    return "\n".join(line.rstrip() for line in script).replace("\\\n", "\n")


def _arg_default(name: str) -> str:
    match = re.search(rf'ARG {name}="([^"]*)"', _models_stage_block())
    assert match, name
    return match.group(1)


def _stub_bin(tmp_path: Path, *, serve: dict[str, bytes] | None) -> Path:
    """A bin dir with a curl stub (serve or fail) and a python3 wrapper that
    passes through to the real interpreter — and *fails the run* if the stage
    ever invokes ``python3 -m pip`` (the offline fallback must never need it)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real_python = shutil.which("python3") or "python3"
    curl_lines = ["#!/bin/sh", 'out=""', 'url=""']
    curl_lines += [
        'while [ "$#" -gt 0 ]; do',
        '  case "$1" in',
        '    -o) out="$2"; shift 2 ;;',
        '    -*) shift ;;',
        '    *) url="$1"; shift ;;',
        '  esac',
        'done',
        'printf "%s" "$url" >> "$CURL_LOG"',
        'printf "\\n" >> "$CURL_LOG"',
    ]
    if serve is None:
        curl_lines += ['echo "curl: could not resolve host" >&2', "exit 6"]
    else:
        for url, payload in serve.items():
            curl_lines.append(f'if [ "$url" = "{url}" ]; then')
            curl_lines.append(f'  printf "%s" "{payload.decode()}" > "$out"; exit 0')
            curl_lines.append("fi")
        curl_lines += ['echo "curl: 404" >&2', "exit 22"]
    (bin_dir / "curl").write_text("\n".join(curl_lines) + "\n", encoding="utf-8")
    (bin_dir / "curl").chmod(0o755)
    (bin_dir / "python3").write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then\n'
        '  echo "the models stage must not need pip (offline fallback regression)" >&2\n'
        "  exit 3\n"
        "fi\n"
        f'exec "{real_python}" "$@"\n',
        encoding="utf-8")
    (bin_dir / "python3").chmod(0o755)
    return bin_dir


def _run_stage(tmp_path: Path, *, pins: dict, serve: dict[str, bytes] | None,
               args: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    work = tmp_path / "models"
    work.mkdir()
    shutil.copy2(MODELS / "coco80.json", work / "coco80.json")
    shutil.copy2(MODELS / "resolve_pin.py", work / "resolve_pin.py")
    # the models stage COPYs exactly this file from the build context
    shutil.copy2(MODEL_FIXTURE, work / "model_fixture.onnx")
    (work / "pins.json").write_text(json.dumps(pins), encoding="utf-8")
    bin_dir = _stub_bin(tmp_path, serve=serve)
    curl_log = tmp_path / "curl.log"
    curl_log.write_text("", encoding="utf-8")
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "CURL_LOG": str(curl_log),
        "LONGWATCH_MODEL_URL": _arg_default("LONGWATCH_MODEL_URL"),
        "LONGWATCH_MODEL_SHA256": _arg_default("LONGWATCH_MODEL_SHA256"),
        "LONGWATCH_FACE_MODEL_URL": _arg_default("LONGWATCH_FACE_MODEL_URL"),
        "LONGWATCH_FACE_MODEL_SHA256": _arg_default("LONGWATCH_FACE_MODEL_SHA256"),
    }
    env.update(args or {})
    result = subprocess.run(["/bin/sh", "-c", _stage_script()], cwd=work, env=env,
                            capture_output=True, text=True)
    result.workdir = work  # type: ignore[attr-defined]
    result.curl_log = curl_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    return result


def _pins(detector_sha: str = "", face_sha: str = "") -> dict:
    return {"schema": "longwatch-model-pins-v1", "models": [
        {"name": "yolov8n.onnx", "urls": [_arg_default("LONGWATCH_MODEL_URL")],
         "sha256": detector_sha},
        {"name": "yunet.onnx", "urls": [_arg_default("LONGWATCH_FACE_MODEL_URL")],
         "sha256": face_sha},
    ]}


CANONICAL = {
    _arg_default("LONGWATCH_MODEL_URL"): DETECTOR_BYTES,
    _arg_default("LONGWATCH_FACE_MODEL_URL"): FACE_BYTES,
}


def test_a_matching_pin_verifies_and_stamps_fetched(tmp_path):
    detector_sha = hashlib.sha256(DETECTOR_BYTES).hexdigest()
    result = _run_stage(tmp_path, pins=_pins(detector_sha), serve=CANONICAL)
    assert result.returncode == 0, result.stderr
    work = result.workdir
    assert (work / "MODEL_PROVENANCE").read_text().strip() == "fetched"
    assert (work / "MODEL_SHA256").read_text().strip() == detector_sha
    assert (work / "FACE_MODEL_SHA256").read_text().strip() == hashlib.sha256(FACE_BYTES).hexdigest()
    assert result.curl_log.splitlines()[0] == _arg_default("LONGWATCH_MODEL_URL")


def test_a_missing_pin_still_fetches_and_says_it_is_unpinned(tmp_path):
    result = _run_stage(tmp_path, pins=_pins(), serve=CANONICAL)
    assert result.returncode == 0, result.stderr
    work = result.workdir
    assert (work / "MODEL_PROVENANCE").read_text().strip() == "fetched_unpinned"
    assert (work / "MODEL_SHA256").read_text().strip() == hashlib.sha256(DETECTOR_BYTES).hexdigest()
    assert "UNPINNED" in result.stdout


def test_a_mismatching_pin_fails_the_build(tmp_path):
    result = _run_stage(tmp_path, pins=_pins("0" * 64), serve=CANONICAL)
    assert result.returncode == 2
    assert "DIGEST MISMATCH" in result.stderr


def test_a_network_less_build_falls_back_to_the_fixture(tmp_path):
    """The fallback is a plain copy of the committed fixture: no network, no
    pip, no generator — the stub python3 fails the run on any pip call."""
    result = _run_stage(tmp_path, pins=_pins(), serve=None)
    assert result.returncode == 0, result.stderr
    work = result.workdir
    assert (work / "MODEL_PROVENANCE").read_text().strip() == "fixture"
    assert (work / "yolov8n.onnx").read_bytes() == MODEL_FIXTURE.read_bytes()
    assert (work / "MODEL_SHA256").read_text().strip() == \
        hashlib.sha256(MODEL_FIXTURE.read_bytes()).hexdigest()
    assert "must not need pip" not in result.stderr
    assert (work / "yunet.onnx.absent").exists()
    # the face URL was still attempted (network-less: it fails) — a fixture
    # build does not silently claim a face model it does not have
    assert "face model unavailable" in result.stdout


def test_a_dead_detector_url_does_not_cost_the_build_its_face_model(tmp_path):
    """BUG-005 regression: the face fetch is independent of the detector fetch.
    A build whose detector URL is unreachable still ships yunet.onnx when its
    own URL answers."""
    result = _run_stage(tmp_path, pins=_pins(), serve={
        _arg_default("LONGWATCH_FACE_MODEL_URL"): FACE_BYTES})
    assert result.returncode == 0, result.stderr
    work = result.workdir
    assert (work / "MODEL_PROVENANCE").read_text().strip() == "fixture"
    assert (work / "yolov8n.onnx").read_bytes() == MODEL_FIXTURE.read_bytes()
    assert (work / "yunet.onnx").read_bytes() == FACE_BYTES
    assert (work / "FACE_MODEL_SHA256").read_text().strip() == \
        hashlib.sha256(FACE_BYTES).hexdigest()
    assert not (work / "yunet.onnx.absent").exists()


def test_a_dead_face_url_does_not_cost_the_build_its_detector(tmp_path):
    detector_sha = hashlib.sha256(DETECTOR_BYTES).hexdigest()
    result = _run_stage(tmp_path, pins=_pins(detector_sha), serve={
        _arg_default("LONGWATCH_MODEL_URL"): DETECTOR_BYTES})
    assert result.returncode == 0, result.stderr
    work = result.workdir
    assert (work / "MODEL_PROVENANCE").read_text().strip() == "fetched"
    assert (work / "MODEL_SHA256").read_text().strip() == detector_sha
    assert (work / "yunet.onnx.absent").exists()
    assert (work / "FACE_MODEL_SHA256").read_text().strip() == ""


def test_the_none_sentinel_disables_the_fetch_deliberately(tmp_path):
    """A fixture-only build passes `none` for both artifacts: nothing is
    fetched, no URL is even attempted, and the committed fixture is used."""
    result = _run_stage(tmp_path, pins=_pins(), serve=CANONICAL,
                        args={"LONGWATCH_MODEL_URL": "none",
                              "LONGWATCH_FACE_MODEL_URL": "none"})
    assert result.returncode == 0, result.stderr
    assert (result.workdir / "MODEL_PROVENANCE").read_text().strip() == "fixture"
    assert (result.workdir / "yolov8n.onnx").read_bytes() == MODEL_FIXTURE.read_bytes()
    assert result.curl_log.strip() == "", "a disabled fetch must not touch the network"
    assert "fetch disabled" in result.stdout


def test_an_empty_download_is_not_stamped_as_fetched(tmp_path):
    """A URL that answers with zero bytes is a failed fetch: the fixture is
    used and the provenance says so, never 'fetched_unpinned' over an empty
    file."""
    empty = {_arg_default("LONGWATCH_MODEL_URL"): b"",
             _arg_default("LONGWATCH_FACE_MODEL_URL"): b""}
    result = _run_stage(tmp_path, pins=_pins(), serve=empty)
    assert result.returncode == 0, result.stderr
    work = result.workdir
    assert (work / "MODEL_PROVENANCE").read_text().strip() == "fixture"
    assert (work / "yolov8n.onnx").read_bytes() == MODEL_FIXTURE.read_bytes()
    assert "empty download" in result.stderr
    assert (work / "yunet.onnx.absent").exists()


def test_the_fixture_the_fallback_copies_is_in_the_build_context():
    """The models stage COPYs tests/fixtures/model_fixture.onnx; .dockerignore
    must not exclude it, and the fixture must be the contract-valid one the
    unit scenarios use (a single committed artifact, no second copy)."""
    ignored = (REPO / ".dockerignore").read_text(encoding="utf-8")
    assert "!tests/fixtures/model_fixture.onnx" in ignored
    stage = _models_stage_block()
    assert "COPY tests/fixtures/model_fixture.onnx" in stage
    assert "make_onnx_fixture.py" not in stage
    assert "pip install" not in stage
