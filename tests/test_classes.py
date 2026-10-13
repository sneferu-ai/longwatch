"""CLASSES — the shipped class map, pinned exactly (§17 scenario 3).

At least one seed draft carried a shifted table (bird=16 … cow=21 — those are
dog … bear).  The filter set is load-bearing: the shipped map's index→name
mapping is pinned here, the loaded model's contract is validated against it,
and a swapped map missing a retained name fails readiness closed.
"""
from __future__ import annotations

import json

import pytest

from longwatch import classmap, detect

EXPECTED = {0: "person", 7: "truck", 14: "bird", 15: "cat",
            16: "dog", 17: "horse", 18: "sheep", 19: "cow"}


def test_shipped_map_is_exactly_the_declared_eight():
    names = classmap.load_class_map()
    assert len(names) == 80
    for index, name in EXPECTED.items():
        assert names[index] == name, f"index {index} is {names[index]!r}, expected {name!r}"
    assert classmap.RETAINED_CLASSES == {name: index for index, name in EXPECTED.items()}


def test_validate_class_map_accepts_the_shipped_map():
    assert classmap.validate_class_map(classmap.load_class_map())["classes"] == 80


def test_swapped_map_missing_a_retained_name_fails_closed(tmp_path):
    names = classmap.load_class_map()
    swapped = list(names)
    swapped[16] = "bear"          # the classic shifted table
    swapped[17] = "zebra"
    with pytest.raises(classmap.ClassMapError) as excinfo:
        classmap.validate_class_map(swapped)
    assert "expected 'dog'" in str(excinfo.value)


def test_class_groups_are_closed():
    assert classmap.CLASS_GROUPS["animal"] == ("bird", "cat", "dog", "horse", "sheep", "cow")
    assert classmap.CLASS_GROUPS["person"] == ("person",)
    assert classmap.CLASS_GROUPS["vehicle"] == ("truck",)
    with pytest.raises(classmap.ClassMapError):
        classmap.class_group_members("livestock")


def test_loaded_model_contract_is_validated(db, cfg):
    detector = detect.Detector(cfg)
    contract = detector.contract()
    assert contract["input"]["shape"] == [1, 3, 640, 640]
    assert contract["output"]["shape"][0] == 1
    assert contract["output"]["shape"][1] == 4 + 80
    assert contract["classes"] == 80
    result = detector.self_test()
    assert result["ok"] is True
    assert result["output_shape"] == [1, 84, 16]


def test_model_contract_refuses_a_mismatched_class_map(db, cfg, tmp_path):
    short_map = tmp_path / "short.json"
    short_map.write_text(json.dumps(["person", "bicycle"]), encoding="utf-8")
    cfg.class_map_path = str(short_map)
    detector = detect.Detector(cfg)
    with pytest.raises(detect.ModelContractError) as excinfo:
        detector.contract()
    assert "class map declares" in str(excinfo.value)


def test_model_contract_refuses_a_missing_model(db, cfg, tmp_path):
    cfg.model_path = str(tmp_path / "absent.onnx")
    detector = detect.Detector(cfg)
    with pytest.raises(detect.ModelContractError) as excinfo:
        detector.contract()
    assert "not found" in str(excinfo.value)


def test_model_digest_mismatch_fails_readiness(db, cfg):
    cfg.model_sha256 = "0" * 64
    detector = detect.Detector(cfg)
    with pytest.raises(detect.ModelContractError) as excinfo:
        detector.contract()
    assert "digest mismatch" in str(excinfo.value)


def test_postprocessing_filters_by_name_not_index():
    """Index → name via the map, then filter by name (§8.3)."""
    names = classmap.load_class_map()
    # a proposal that says "class index 16" with high confidence = a dog
    row = [320.0, 320.0, 100.0, 100.0] + [0.0] * 80
    row[4 + 16] = 0.9
    raw = [[row]]
    params = detect.letterbox_params(640, 360)
    found = detect.postprocess(raw, names, 0.25, 0.45, params)
    assert [d.class_name for d in found] == ["dog"]
    assert found[0].conf_milli == 900


def test_nms_keeps_the_highest_confidence_per_class():
    names = classmap.load_class_map()
    box = [320.0, 320.0, 100.0, 100.0]
    first = [box + [0.0] * 80]
    first[0][4 + 16] = 0.9
    second = [box + [0.0] * 80]
    second[0][4 + 16] = 0.4
    detections = detect.postprocess([first], names, 0.25, 0.45, detect.letterbox_params(640, 360))
    detections += detect.postprocess([second], names, 0.25, 0.45, detect.letterbox_params(640, 360))
    kept = detect.nms(detections, 0.45)
    assert len(kept) == 1
    assert kept[0].conf_milli == 900
