#!/usr/bin/env python3
"""Generate the YuNet-contract face-model fixture used by the export scenarios.

Why a fixture at all: the construction environment's dependency lane reaches
package registries only, so the digest-pinned YuNet ONNX — which the
Dockerfile's ``models`` stage fetches into the *runtime image* where the build
has network — is not fetchable here.  The export's face-pixelation positive
path therefore runs against this generated fixture, loaded through the
specification's own ``LONGWATCH_FACE_MODEL_PATH`` swap, and the real pinned
model is exercised in the running product image by the http and browser checks.

The fixture honours the declared YuNet contract exactly: input ``[1,3,96,160]``
float32 BGR (160×90 padded up to the 32-multiple heads), and the twelve
``cls/obj/bbox/kps`` heads at strides 8/16/32.  Exactly one stride-8 cell
carries a face — cell (row 6, column 10), a 32×32 box centred at (80, 48) —
and every other cell scores zero, so the detected region is a known constant
the export scenarios can assert against.

Usage:  python3 tests/fixtures/make_yunet_fixture.py [output_path]
        (regeneration needs the optional fixtures extra:
         ``pip install -e '.[fixtures]'`` — never needed to run the suite)
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent / "yunet_fixture.onnx"
INPUT_HEIGHT = 96      # 90 padded to the next multiple of 32
INPUT_WIDTH = 160
STRIDES = (8, 16, 32)
FACE_CELL = (6, 10)    # (row, column) at stride 8 -> centre (80, 48)
FACE_SCALE = 4.0       # box side = FACE_SCALE x stride = 32 px


def _head(name: str, shape: tuple[int, ...], values: np.ndarray) -> onnx.NodeProto:
    # Imported here, not at module level: this generator must stay importable —
    # and pytest-collectable — in environments where the optional ``onnx`` dev
    # dependency is absent.  The committed fixture is what ships; running this
    # script is the regeneration step that needs ``onnx`` + ``numpy``.
    import numpy as np
    from onnx import TensorProto, helper

    tensor = helper.make_tensor(name, TensorProto.FLOAT, shape,
                                values.astype(np.float32).flatten().tolist())
    return helper.make_node("Constant", [], [name], value=tensor)


def build(path: Path) -> Path:
    import numpy as np
    import onnx
    from onnx import TensorProto, helper

    images = helper.make_tensor_value_info("images", TensorProto.FLOAT,
                                           [1, 3, INPUT_HEIGHT, INPUT_WIDTH])
    nodes = []
    outputs = []
    for stride in STRIDES:
        rows, cols = INPUT_HEIGHT // stride, INPUT_WIDTH // stride
        cls = np.zeros((1, 1, rows, cols), dtype=np.float32)
        obj = np.zeros((1, 1, rows, cols), dtype=np.float32)
        bbox = np.zeros((1, 4, rows, cols), dtype=np.float32)
        kps = np.zeros((1, 10, rows, cols), dtype=np.float32)
        if stride == STRIDES[0]:
            row, col = FACE_CELL
            cls[0, 0, row, col] = 1.0
            obj[0, 0, row, col] = 1.0
            bbox[0, :, row, col] = [0.0, 0.0, math.log(FACE_SCALE), math.log(FACE_SCALE)]
            kps[0, :, row, col] = 0.5
        for kind, tensor in (("cls", cls), ("obj", obj), ("bbox", bbox), ("kps", kps)):
            name = f"{kind}_{stride}"
            nodes.append(_head(name, list(tensor.shape), tensor))
            outputs.append(helper.make_tensor_value_info(
                name, TensorProto.FLOAT, list(tensor.shape)))
    graph = helper.make_graph(nodes, "longwatch_yunet_fixture", [images], outputs)
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
