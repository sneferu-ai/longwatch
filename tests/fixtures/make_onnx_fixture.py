#!/usr/bin/env python3
"""Generate the contract-valid ONNX detector fixture used by the unit scenarios.

Why a fixture at all: the construction environment's dependency lane reaches
package registries only, so the digest-pinned YOLOv8n/YuNet artifacts — which
the Dockerfile's ``models`` stage fetches into the *runtime image* where the
build has network — are not fetchable here.  The model-loading scenarios
(CLASSES readiness legs, HEALTH self-test leg, DEMO detection pass) therefore
run against this generated fixture through the specification's own three-variable
swap path (``LONGWATCH_MODEL_PATH`` + ``LONGWATCH_MODEL_SHA256`` +
``LONGWATCH_CLASS_MAP`` = the shipped ``models/coco80.json``), preserving every
named assertion.  The real pinned models are exercised in the running product
image by the http and browser checks.

The output is committed (``tests/fixtures/model_fixture.onnx``) and is the same
file the Dockerfile's ``models`` stage **copies** when a build has no network —
the stage never runs this generator, so its fallback needs neither this script's
``onnx``/``numpy`` dependencies nor any package index.  Re-run this script only
to regenerate the fixture (the digest is recorded in the image at build time).

The fixture honours the declared contract exactly: input ``[1,3,640,640]``
float32, output ``[1,4+80,M]`` — and it emits no detections (all zeros), so the
DEMO scenario's presence evidence comes from the labeled synthetic injection,
which is the honest way to exercise the rule engine without a real detector.

Usage:  python3 tests/fixtures/make_onnx_fixture.py [output_path]
        (regeneration needs the optional fixtures extra:
         ``pip install -e '.[fixtures]'`` — never needed to run the suite)
"""
from __future__ import annotations

import sys
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent / "model_fixture.onnx"
CLASSES = 80
PROPOSALS = 16


def build(path: Path) -> Path:
    # Imported here, not at module level: this generator must stay importable —
    # and pytest-collectable — in environments where the optional ``onnx`` dev
    # dependency is absent.  The committed fixture is what ships; running this
    # script is the regeneration step that needs ``onnx`` + ``numpy``.
    import numpy as np
    import onnx
    from onnx import TensorProto, helper

    images = helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 640, 640])
    output = helper.make_tensor_value_info("output0", TensorProto.FLOAT,
                                           [1, 4 + CLASSES, PROPOSALS])
    reduce_mean = helper.make_node("ReduceMean", ["images"], ["mean"],
                                   axes=[1, 2, 3], keepdims=0)
    zero = helper.make_tensor("zero", TensorProto.FLOAT, [1], [0.0])
    scale = helper.make_node("Mul", ["mean", "zero"], ["scaled"])
    bias = helper.make_tensor("bias", TensorProto.FLOAT, [1, 4 + CLASSES, PROPOSALS],
                              np.zeros((1, 4 + CLASSES, PROPOSALS),
                                       dtype=np.float32).flatten().tolist())
    add = helper.make_node("Add", ["scaled", "bias"], ["output0"])
    graph = helper.make_graph([reduce_mean, scale, add], "longwatch_contract_fixture",
                              [images], [output], [zero, bias])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)],
                              producer_name="longwatch-tests")
    model.ir_version = 9
    onnx.checker.check_model(model)
    onnx.save(model, str(path))
    return path


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else OUTPUT
    target.parent.mkdir(parents=True, exist_ok=True)
    build(target)
    print(f"wrote {target} ({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
