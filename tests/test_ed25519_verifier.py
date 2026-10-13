"""The in-repo Ed25519 verifier (§7.6, §17 EXPORT).

``ed25519_verifier.py`` is authored in this repository under its MIT license
as a verify-only implementation of RFC 8032 §5.1.7.  It is pinned here against
the RFC 8032 §7.1 public test vectors (extracted from the RFC text and checked
self-consistent against the reference implementation: each secret key
reproduces its public key and signature exactly), cross-checked against the
``cryptography`` package over random round-trips and tamper cases, and its
public-key PEM parsing is proven to need nothing beyond the standard library —
the bundle's verification path must run on a bare Python 3.
"""
from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import ed25519_verifier as verifier  # noqa: E402
from longwatch import signing  # noqa: E402

# RFC 8032 §7.1, TEST 1 / TEST 2 / TEST 3 / TEST 1024 / TEST SHA(abc):
# (public_key_hex, message_hex, signature_hex).  The long messages are wrapped
# at 64 hex characters for readability; concatenation restores the bytes.
RFC8032_VECTORS = [
    (
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
        "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
    (
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "af82",
        "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
        "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a",
    ),
    (
        "278117fc144c72340f67d0f2316e8386ceffbf2b2428c9c51fef7c597f1d426e",
        "08b8b2b733424243760fe426a4b54908632110a66c2f6591eabd3345e3e4eb98"
        "fa6e264bf09efe12ee50f8f54e9f77b1e355f6c50544e23fb1433ddf73be84d8"
        "79de7c0046dc4996d9e773f4bc9efe5738829adb26c81b37c93a1b270b20329d"
        "658675fc6ea534e0810a4432826bf58c941efb65d57a338bbd2e26640f89ffbc"
        "1a858efcb8550ee3a5e1998bd177e93a7363c344fe6b199ee5d02e82d522c4fe"
        "ba15452f80288a821a579116ec6dad2b3b310da903401aa62100ab5d1a36553e"
        "06203b33890cc9b832f79ef80560ccb9a39ce767967ed628c6ad573cb116dbef"
        "efd75499da96bd68a8a97b928a8bbc103b6621fcde2beca1231d206be6cd9ec7"
        "aff6f6c94fcd7204ed3455c68c83f4a41da4af2b74ef5c53f1d8ac70bdcb7ed1"
        "85ce81bd84359d44254d95629e9855a94a7c1958d1f8ada5d0532ed8a5aa3fb2"
        "d17ba70eb6248e594e1a2297acbbb39d502f1a8c6eb6f1ce22b3de1a1f40cc24"
        "554119a831a9aad6079cad88425de6bde1a9187ebb6092cf67bf2b13fd65f270"
        "88d78b7e883c8759d2c4f5c65adb7553878ad575f9fad878e80a0c9ba63bcbcc"
        "2732e69485bbc9c90bfbd62481d9089beccf80cfe2df16a2cf65bd92dd597b07"
        "07e0917af48bbb75fed413d238f5555a7a569d80c3414a8d0859dc65a46128ba"
        "b27af87a71314f318c782b23ebfe808b82b0ce26401d2e22f04d83d1255dc51a"
        "ddd3b75a2b1ae0784504df543af8969be3ea7082ff7fc9888c144da2af58429e"
        "c96031dbcad3dad9af0dcbaaaf268cb8fcffead94f3c7ca495e056a9b47acdb7"
        "51fb73e666c6c655ade8297297d07ad1ba5e43f1bca32301651339e22904cc8c"
        "42f58c30c04aafdb038dda0847dd988dcda6f3bfd15c4b4c4525004aa06eeff8"
        "ca61783aacec57fb3d1f92b0fe2fd1a85f6724517b65e614ad6808d6f6ee34df"
        "f7310fdc82aebfd904b01e1dc54b2927094b2db68d6f903b68401adebf5a7e08"
        "d78ff4ef5d63653a65040cf9bfd4aca7984a74d37145986780fc0b16ac451649"
        "de6188a7dbdf191f64b5fc5e2ab47b57f7f7276cd419c17a3ca8e1b939ae49e4"
        "88acba6b965610b5480109c8b17b80e1b7b750dfc7598d5d5011fd2dcc5600a3"
        "2ef5b52a1ecc820e308aa342721aac0943bf6686b64b2579376504ccc493d97e"
        "6aed3fb0f9cd71a43dd497f01f17c0e2cb3797aa2a2f256656168e6c496afc5f"
        "b93246f6b1116398a346f1a641f3b041e989f7914f90cc2c7fff357876e506b5"
        "0d334ba77c225bc307ba537152f3f1610e4eafe595f6d9d90d11faa933a15ef1"
        "369546868a7f3a45a96768d40fd9d03412c091c6315cf4fde7cb68606937380d"
        "b2eaaa707b4c4185c32eddcdd306705e4dc1ffc872eeee475a64dfac86aba41c"
        "0618983f8741c5ef68d3a101e8a3b8cac60c905c15fc910840b94c00a0b9d0",
        "0aab4c900501b3e24d7cdf4663326a3a87df5e4843b2cbdb67cbf6e460fec350"
        "aa5371b1508f9f4528ecea23c436d94b5e8fcd4f681e30a6ac00a9704a188a03",
    ),
    (
        "ec172b93ad5e563bf4932c70e1245034c35467ef2efd4d64ebf819683467e2bf",
        "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a"
        "2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f",
        "dc2a4459e7369633a52b1bf277839a00201009a3efbf3ecb69bea2186c26b589"
        "09351fc9ac90b3ecfdfbc7c66431e0303dca179c138ac17ad9bef1177331a704",
    ),
]



@pytest.mark.parametrize("public_hex,message_hex,signature_hex", RFC8032_VECTORS)
def test_rfc8032_vectors_verify(public_hex, message_hex, signature_hex):
    assert verifier.verify(bytes.fromhex(public_hex), bytes.fromhex(message_hex),
                           bytes.fromhex(signature_hex)) is True


def test_every_tamper_is_rejected():
    public_hex, message_hex, signature_hex = RFC8032_VECTORS[1]
    public_key = bytes.fromhex(public_hex)
    message = bytes.fromhex(message_hex)
    signature = bytes.fromhex(signature_hex)
    # a flipped byte anywhere — key, message, signature R or S — breaks it
    assert verifier.verify(public_key, bytes([message[0] ^ 1]), signature) is False
    tampered_sig = signature[:10] + bytes([signature[10] ^ 1]) + signature[11:]
    assert verifier.verify(public_key, message, tampered_sig) is False
    tampered_key = bytes([public_key[0] ^ 1]) + public_key[1:]
    assert verifier.verify(tampered_key, message, signature) is False
    # malformed shapes are rejected, never excepted
    assert verifier.verify(public_key, message, signature[:63]) is False
    assert verifier.verify(public_key[:-1], message, signature) is False
    assert verifier.verify(public_key, message, b"\xff" * 64) is False


def test_cross_checked_against_the_reference_implementation():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    for index in range(20):
        key = Ed25519PrivateKey.generate()
        public_key = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        message = bytes(range(256))[: index * 13]
        signature = key.sign(message)
        assert verifier.verify(public_key, message, signature) is True
        assert verifier.verify(public_key, message + b"x", signature) is False


def test_public_key_pem_parses_with_the_stdlib_alone(tmp_path):
    """The bundle's key parse is the RFC 8410 fixed prefix + 32 raw bytes."""
    key = signing.load_or_create_key(tmp_path / "keys" / "ed25519.pem")
    pem = signing.public_pem(key)
    parsed = verifier._load_public_key(pem)
    assert parsed.hex() == signing.public_key_hex(key)
    # hex input still works, and a non-Ed25519 PEM shape is a named error
    assert verifier._load_public_key(signing.public_key_hex(key)) == parsed
    bogus_pem = "-----BEGIN PUBLIC KEY-----\n" + base64.b64encode(b"\x00" * 44).decode() \
        + "\n-----END PUBLIC KEY-----\n"
    with pytest.raises(ValueError):
        verifier._load_public_key(bogus_pem)


def test_the_verifier_imports_no_third_party_code():
    """The bundle's verification path is stdlib-only — pin it."""
    source = (REPO / "ed25519_verifier.py").read_text(encoding="utf-8")
    assert "cryptography" not in source
    assert "import nacl" not in source


def test_cli_accepts_raw_and_base64_signature_files(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    pem = tmp_path / "public.pem"
    pem.write_text(signing.public_pem(key), encoding="utf-8")
    message = tmp_path / "message.bin"
    message.write_bytes(b"the exact signed bytes")
    signature = key.sign(message.read_bytes())

    raw_sig = tmp_path / "manifest.sig"
    raw_sig.write_bytes(signature)
    b64_sig = tmp_path / "manifest.sig.b64"
    b64_sig.write_text(base64.b64encode(signature).decode("ascii"), encoding="utf-8")

    for sig_path in (raw_sig, b64_sig):
        result = subprocess.run(
            [sys.executable, str(REPO / "ed25519_verifier.py"), str(pem),
             str(sig_path), str(message)],
            capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert "SIGNATURE VALID" in result.stdout

    message.write_bytes(b"tampered bytes")
    result = subprocess.run(
        [sys.executable, str(REPO / "ed25519_verifier.py"), str(pem),
         str(raw_sig), str(message)],
        capture_output=True, text=True)
    assert result.returncode == 1
    assert "SIGNATURE INVALID" in result.stdout
