"""CANON — twelve vectors, Python and an independent JavaScript canonicalizer.

Portability is proven, not asserted: every vector in
``tests/fixtures/canonical_vectors.json`` reproduces byte-exactly under the
Python canonicalizer **and** under the independent ~30-line canonicalizer in
``tests/fixtures/canonical.js`` executed under Node.  A payload containing a
float raises :class:`FloatInPayload`.
"""
from __future__ import annotations

import json
import subprocess
import shutil
from pathlib import Path

import pytest

from longwatch import canonical

FIXTURES = Path(__file__).resolve().parent / "fixtures"
VECTORS = FIXTURES / "canonical_vectors.json"


def load_vectors() -> list[dict]:
    document = json.loads(VECTORS.read_text(encoding="utf-8"))
    return document["vectors"]


def test_twelve_vectors_ship():
    assert len(load_vectors()) == 12


@pytest.mark.parametrize("vector", load_vectors(), ids=lambda v: v["name"])
def test_python_canonicalizer_reproduces_every_vector(vector):
    produced = canonical.canonical_json(vector["value"])
    assert produced == vector["canonical"]
    # and the round trip through the permitted type set is stable
    assert canonical.canonical_json(canonical.parse(produced)) == produced


def test_python_canonicalizer_is_type_restricted():
    with pytest.raises(canonical.FloatInPayload):
        canonical.canonical_json({"motion": 0.15})
    with pytest.raises(canonical.FloatInPayload):
        canonical.canonical_json({"nested": [1, {"deep": [0.5]}]})
    with pytest.raises(canonical.NonAsciiKey):
        canonical.canonical_json({"clé": 1})


def test_integers_beyond_int64_are_refused():
    """§7.1 permits integers *within int64* — beyond it, a JavaScript verifier
    would read a different number, so the value is refused at the boundary."""
    with pytest.raises(canonical.IntOutOfRange):
        canonical.canonical_json({"big": 2 ** 63})
    with pytest.raises(canonical.IntOutOfRange):
        canonical.canonical_json({"big": [-(2 ** 63) - 1]})
    # the boundaries themselves are permitted
    assert canonical.canonical_json({"lo": -(2 ** 63), "hi": 2 ** 63 - 1}) == \
        '{"hi":9223372036854775807,"lo":-9223372036854775808}'


def test_javascript_canonicalizer_reproduces_every_vector():
    if not shutil.which("node"):
        pytest.skip("node is not available in this environment")
    result = subprocess.run(
        ["node", str(FIXTURES / "canonical.js"), str(VECTORS)],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "byte-exactly" in result.stdout


def test_javascript_leg_reports_divergence(tmp_path):
    """The Node leg is a real check: a corrupted vector must fail it."""
    if not shutil.which("node"):
        pytest.skip("node is not available in this environment")
    document = json.loads(VECTORS.read_text(encoding="utf-8"))
    document["vectors"][0]["canonical"] = '{"corrupted":true}'
    corrupted = tmp_path / "vectors.json"
    corrupted.write_text(json.dumps(document), encoding="utf-8")
    result = subprocess.run(
        ["node", str(FIXTURES / "canonical.js"), str(corrupted)],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 1
    assert "diverged" in result.stderr


def test_milli_rounding_is_half_even_and_single():
    assert canonical.to_milli(0.0005) == 0      # ties to even
    assert canonical.to_milli(0.0015) == 2
    assert canonical.to_milli(0.15) == 150
    assert canonical.to_milli(0.9999) == 1000
    assert canonical.milli_str(150) == "0.150"
    assert canonical.milli_str(1000) == "1.000"
    assert canonical.ms_to_seconds_str(100_000) == "100"
