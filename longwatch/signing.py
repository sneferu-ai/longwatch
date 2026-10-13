"""The Ed25519 signing key (§7.6).

Generated on first start into ``$LONGWATCH_DATA_DIR/keys/`` with mode 0600 —
data volume only.  The public key is printed to the startup log, shown in
Settings with its SHA-256 fingerprint, stored on every ``exports`` row, and
shipped inside every bundle as ``public.pem``.

Losing the key invalidates future signatures, never past ones: every past
bundle carries its own key and stays verifiable.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

KEY_MODE = 0o600


class SigningKeyError(RuntimeError):
    """The signing key could not be loaded or created."""


def _pem_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def load_or_create_key(path: str | Path) -> Ed25519PrivateKey:
    """Load the key, creating it atomically on first start.

    Two concurrent starters must reuse one key: the create path uses
    ``O_CREAT|O_EXCL`` and a loser of the race reads the winner's file.
    """
    key_path = Path(path)
    if key_path.exists():
        try:
            return serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        except Exception as exc:  # noqa: BLE001
            raise SigningKeyError(f"signing key at {key_path} is unreadable: {exc}") from exc
    key_path.parent.mkdir(parents=True, exist_ok=True)
    candidate = Ed25519PrivateKey.generate()
    payload = _pem_bytes(candidate)
    tmp = key_path.with_suffix(key_path.suffix + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, KEY_MODE)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.link(tmp, key_path)  # atomic create-if-absent
        winner = True
    except FileExistsError:
        winner = False
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    os.chmod(key_path, KEY_MODE)
    if winner:
        return candidate
    return serialization.load_pem_private_key(key_path.read_bytes(), password=None)


def public_pem(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")


def public_key_hex(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()


def fingerprint_of_pem(pem: str | bytes) -> str:
    """SHA-256 of the PEM bytes — the value recipients compare out of band."""
    if isinstance(pem, str):
        pem = pem.encode("utf-8")
    return hashlib.sha256(pem).hexdigest()


def sign(key: Ed25519PrivateKey, message: bytes) -> bytes:
    return key.sign(message)


def key_identity(path: str | Path) -> dict:
    key = load_or_create_key(path)
    pem = public_pem(key)
    return {"public_pem": pem, "fingerprint": fingerprint_of_pem(pem),
            "public_key_hex": public_key_hex(key), "path": str(path)}
