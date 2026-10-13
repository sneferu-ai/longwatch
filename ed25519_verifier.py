#!/usr/bin/env python3
"""Verify-only Ed25519 — RFC 8032 §5.1.7, authored in this repository.

Provenance
----------
* Algorithm: RFC 8032 (EdDSA), section 5.1.7 (``Verify``).
* Code: this repository, MIT licensed.  No third-party code is vendored, so no
  third-party license applies to this file.

This module is shipped inside every LongWatch export bundle, and its SHA-256
digest is carried in the signed manifest's ``files`` map, so a tampered verifier
is detectable.  It is deliberately verify-only: no key generation, no signing.

Command line (used by ``verify_bundle.py``):

    python3 ed25519_verifier.py <public_key_hex|public.pem> <signature_hex|.sig file> <message file>

Exit codes: 0 valid, 1 invalid, 2 usage/format error.
"""
from __future__ import annotations

import base64
import hashlib
import os
import sys

# Curve constants (RFC 8032 §5.1).
P = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, P - 2, P)) % P
_I = pow(2, (P - 1) // 4, P)

_IDENTITY = (0, 1, 1, 0)


def _recover_x(y: int, sign: int):
    """Recover the x coordinate from y and the sign bit, or None if invalid."""
    if y >= P:
        return None
    x2 = ((y * y - 1) * pow(_D * y * y + 1, P - 2, P)) % P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0:
        x = (x * _I) % P
    if (x * x - x2) % P != 0:
        return None
    if (x & 1) != sign:
        x = P - x
    return x


def _decode_point(encoded: bytes):
    if len(encoded) != 32:
        return None
    value = int.from_bytes(encoded, "little")
    sign = (value >> 255) & 1
    y = value & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, (x * y) % P)


def _add(p1, p2):
    x1, y1, z1, t1 = p1
    x2, y2, z2, t2 = p2
    a = ((y1 - x1) * (y2 - x2)) % P
    b = ((y1 + x1) * (y2 + x2)) % P
    c = (t1 * 2 * _D * t2) % P
    d = (z1 * 2 * z2) % P
    e, f, g, h = (b - a) % P, (d - c) % P, (d + c) % P, (b + a) % P
    return ((e * f) % P, (g * h) % P, (f * g) % P, (e * h) % P)


def _double(point):
    x1, y1, z1, _t1 = point
    a = (x1 * x1) % P
    b = (y1 * y1) % P
    c = (2 * z1 * z1) % P
    d = (-a) % P
    e = (((x1 + y1) ** 2) - a - b) % P
    g = (d + b) % P
    f = (g - c) % P
    h = (d - b) % P
    return ((e * f) % P, (g * h) % P, (f * g) % P, (e * h) % P)


def _scalar_mult(point, scalar: int):
    result = _IDENTITY
    addend = point
    while scalar:
        if scalar & 1:
            result = _add(result, addend)
        addend = _double(addend)
        scalar >>= 1
    return result


def _points_equal(p1, p2) -> bool:
    x1, y1, z1, _ = p1
    x2, y2, z2, _ = p2
    if (x1 * z2 - x2 * z1) % P != 0:
        return False
    return (y1 * z2 - y2 * z1) % P == 0


def _base_point():
    y = (4 * pow(5, P - 2, P)) % P
    x = _recover_x(y, 0)
    if x is None:  # pragma: no cover - impossible for the curve constant
        raise RuntimeError("Ed25519 base point could not be recovered")
    return (x, y, 1, (x * y) % P)


_BASE = _base_point()
# The RFC 8032 §5.1 base point, asserted so a corrupted constant cannot ship.
assert _BASE[0] == 15112221349535400772501151409588531511454012693041857206046113283949847762202
assert _BASE[1] == 46316835694926478169428394003475163141307993866256225615783033603165251855960


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """RFC 8032 §5.1.7 verification.  Returns True only for a valid signature."""
    if not isinstance(public_key, (bytes, bytearray)) or len(public_key) != 32:
        return False
    if not isinstance(signature, (bytes, bytearray)) or len(signature) != 64:
        return False
    signature = bytes(signature)
    r_encoded = signature[:32]
    s = int.from_bytes(signature[32:], "little")
    if s >= L:
        return False
    point_a = _decode_point(bytes(public_key))
    point_r = _decode_point(r_encoded)
    if point_a is None or point_r is None:
        return False
    k = int.from_bytes(
        hashlib.sha512(r_encoded + bytes(public_key) + bytes(message)).digest(), "little") % L
    left = _add(point_r, _scalar_mult(point_a, k))
    right = _scalar_mult(_BASE, s)
    return _points_equal(left, right)


def verify_hex(public_key_hex: str, message: bytes, signature_hex: str) -> bool:
    try:
        return verify(bytes.fromhex(public_key_hex), message, bytes.fromhex(signature_hex))
    except ValueError:
        return False


# An Ed25519 SubjectPublicKeyInfo (RFC 8410) is exactly this 12-byte DER
# prefix followed by the 32 raw public-key bytes — a fixed shape, so parsing
# the PEM needs nothing beyond the standard library.  The bundle's whole
# verification path is stdlib-only: a recipient with a bare Python 3 must be
# able to verify, with no third-party package installed.
_ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")


def _load_public_key(text: str) -> bytes:
    text = text.strip()
    if text.startswith("-----BEGIN"):
        lines = [line.strip() for line in text.splitlines()
                 if line.strip() and not line.startswith("-----")]
        try:
            der = base64.b64decode("".join(lines), validate=True)
        except Exception as exc:  # noqa: BLE001 - a malformed PEM is a usage error
            raise ValueError(f"the PEM body is not valid base64: {exc}") from exc
        if len(der) != len(_ED25519_SPKI_PREFIX) + 32 or \
                not der.startswith(_ED25519_SPKI_PREFIX):
            raise ValueError(
                "not an Ed25519 SubjectPublicKeyInfo PEM (expected the RFC 8410 "
                "prefix 302a300506032b6570032100 followed by 32 key bytes)")
        return der[len(_ED25519_SPKI_PREFIX):]
    return bytes.fromhex(text)


def _load_signature(arg: str) -> bytes:
    """A hex string, or a file holding the raw 64 bytes or their base64 text."""
    try:
        return bytes.fromhex(arg.strip())
    except ValueError:
        pass
    with open(arg, "rb") as handle:
        blob = handle.read()
    if len(blob) == 64:
        return blob  # the raw .sig form `openssl pkeyutl -sigfile` reads
    decoded = base64.b64decode(blob.strip())
    if len(decoded) != 64:
        raise ValueError(f"signature decodes to {len(decoded)} bytes, expected 64")
    return decoded


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        sys.stderr.write(__doc__)
        return 2
    _, key_arg, sig_arg, message_path = argv
    try:
        # the key may be inline hex/PEM text or a path to a .pem file
        key_text = key_arg
        if os.path.isfile(key_arg):
            with open(key_arg, "r", encoding="utf-8") as handle:
                key_text = handle.read()
        public_key = _load_public_key(key_text)
        signature = _load_signature(sig_arg)
    except Exception as exc:  # noqa: BLE001 - usage error is exit 2
        sys.stderr.write(f"could not read the key or signature: {exc}\n")
        return 2
    with open(message_path, "rb") as handle:
        message = handle.read()
    ok = verify(public_key, message, signature)
    print("SIGNATURE VALID" if ok else "SIGNATURE INVALID")
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main(sys.argv))
