"""The declared class map (§8.3) — index → name, validated by name.

At least one seed draft carried a shifted table (bird=16 … cow=21 — those are
dog … bear).  The filter set is load-bearing, so the ids are pinned here and
the shipped map file's index→name mapping is pinned by the ``CLASSES`` test.
Detection post-processing maps output index → name **via the map, then filters
by name**; the hot path never filters by a hardcoded index.
"""
from __future__ import annotations

import json
from pathlib import Path

#: The eight retained classes and their COCO ids, exactly as declared in §8.3.
RETAINED_CLASSES: dict[str, int] = {
    "person": 0,
    "truck": 7,
    "bird": 14,
    "cat": 15,
    "dog": 16,
    "horse": 17,
    "sheep": 18,
    "cow": 19,
}

#: v0 admits no free-text class or group naming: groups are exactly these.
ANIMAL_CLASSES: tuple[str, ...] = ("bird", "cat", "dog", "horse", "sheep", "cow")
CLASS_GROUPS: dict[str, tuple[str, ...]] = {
    "animal": ANIMAL_CLASSES,
    "person": ("person",),
    "vehicle": ("truck",),
}

RETAINED_NAMES = tuple(RETAINED_CLASSES.keys())


class ClassMapError(RuntimeError):
    """The class map does not satisfy the declared contract — fail closed."""


def packaged_path() -> Path:
    """The COCO-80 map shipped in this repository."""
    return Path(__file__).resolve().parent.parent / "models" / "coco80.json"


def load_class_map(path: str | Path | None = None) -> list[str]:
    """Load the index→name map; the packaged COCO-80 map is the fallback."""
    candidate = Path(path) if path else packaged_path()
    if not candidate.is_file():
        fallback = packaged_path()
        if not fallback.is_file():
            raise ClassMapError(f"class map not found at {candidate} and no packaged map exists")
        candidate = fallback
    try:
        names = json.loads(candidate.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ClassMapError(f"class map {candidate} is not valid JSON: {exc}") from exc
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ClassMapError(f"class map {candidate} must be a JSON array of names")
    return names


def validate_class_map(names: list[str]) -> dict:
    """Readiness validation: the eight retained names exist, at their declared ids."""
    if len(names) < 8:
        raise ClassMapError(f"class map declares only {len(names)} classes")
    for name, index in RETAINED_CLASSES.items():
        if index >= len(names):
            raise ClassMapError(f"retained class {name!r} (id {index}) is outside the map")
        if names[index] != name:
            raise ClassMapError(
                f"class map index {index} is {names[index]!r}, expected {name!r}"
            )
    return {"classes": len(names), "retained": dict(RETAINED_CLASSES)}


def class_group_members(group: str) -> tuple[str, ...]:
    if group not in CLASS_GROUPS:
        raise ClassMapError(
            f"unknown class group {group!r}; v0 admits exactly {sorted(CLASS_GROUPS)}"
        )
    return CLASS_GROUPS[group]
