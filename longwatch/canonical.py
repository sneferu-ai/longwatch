"""Canonical JSON — integer-only and byte-portable (§7.1).

Permitted types in any hashed payload or signed manifest document: ``null``,
booleans, integers, strings, arrays, objects.  **Floats are forbidden** and
raise :class:`FloatInPayload`.  Every quantity that was a float elsewhere in
the system is an integer in milli-units here.

The canonical form is exactly what ``json.dumps(obj, sort_keys=True,
separators=(",", ":"), ensure_ascii=False)`` produces for this type set, and
what a default encoder produces in JavaScript, Go and Rust once keys are
sorted.  ``tests/fixtures/canonical_vectors.json`` ships the twelve vectors
that pin it, including the independent JavaScript leg.
"""
from __future__ import annotations

import json

#: Keys are system-defined ASCII field names; a non-ASCII key is a bug.
class NonAsciiKey(ValueError):
    """Raised when a payload object key is not ASCII."""


class FloatInPayload(ValueError):
    """Raised when a float appears anywhere in a hashed/signed document."""

    def __init__(self, path: str):
        self.path = path
        super().__init__(f"float present at {path}: floats are forbidden in hashed payloads")


class NonIntegerNumber(ValueError):
    """Raised for numeric types that are neither int nor bool (e.g. Decimal)."""


class IntOutOfRange(ValueError):
    """Raised when an integer exceeds the int64 envelope of §7.1.

    The canonical form is only byte-portable for integers every mainstream
    encoder reproduces exactly; beyond int64 a JavaScript verifier would read
    a different number, so the out-of-range value is refused at the boundary.
    """


INT64_MIN = -(2 ** 63)
INT64_MAX = 2 ** 63 - 1


def _walk(value, path: str) -> None:
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int):
        if not INT64_MIN <= value <= INT64_MAX:
            raise IntOutOfRange(f"integer at {path} exceeds the int64 envelope")
        return
    if isinstance(value, float):
        raise FloatInPayload(path)
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _walk(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise NonAsciiKey(f"payload key {key!r} at {path} is not a string")
            if not key.isascii():
                raise NonAsciiKey(f"payload key {key!r} at {path} is not ASCII")
            _walk(item, f"{path}.{key}")
        return
    raise NonIntegerNumber(f"unsupported value at {path}: {type(value).__name__}")


def validate(document) -> None:
    """Recursively validate a document against the permitted type set."""
    _walk(document, "$")


def canonical_json(document) -> str:
    """Serialize ``document`` to its canonical byte string (as ``str``)."""
    validate(document)
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_bytes(document) -> bytes:
    return canonical_json(document).encode("utf-8")


def parse(text: str | bytes):
    """Parse JSON text into the permitted type set, refusing floats."""
    if isinstance(text, (bytes, bytearray)):
        text = text.decode("utf-8")
    document = json.loads(text)
    validate(document)
    return document


def pretty_json(document) -> str:
    """Human-readable rendering of the *same* object.

    ``verify_bundle.py`` re-canonicalizes this rendering and asserts byte
    equality with ``manifest.canonical`` — a divergent pretty copy is detected.
    """
    validate(document)
    return json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False)


# --------------------------------------------------------------------- units
def to_milli(value: float) -> int:
    """Round-half-even a normalized value to integer thousandths.

    The single rounding point of the system: every signal, confidence and
    percentile is rounded here, once, at computation time.
    """
    scaled = float(value) * 1000.0
    floor = int(scaled // 1)
    remainder = scaled - floor
    if remainder > 0.5:
        return floor + 1
    if remainder < 0.5:
        return floor
    return floor if floor % 2 == 0 else floor + 1


def milli_str(milli: int, decimals: int = 3) -> str:
    """Render integer milli-units as a fixed-decimal string — no float parsing."""
    milli = int(milli)
    sign = "-" if milli < 0 else ""
    milli = abs(milli)
    whole, frac = divmod(milli, 1000)
    if decimals <= 0:
        return f"{sign}{whole}"
    digits = f"{frac:03d}"[:decimals]
    return f"{sign}{whole}.{digits}"


def ms_to_seconds_str(ms: int) -> str:
    """Durations render as integer seconds from milliseconds."""
    return str(int(ms) // 1000)
