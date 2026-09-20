# tests/unit/test_crypto.py
import pytest

from app.core import crypto


def test_encrypt_decrypt_round_trip() -> None:
    plaintext = "shhh-this-is-a-secret"
    ciphertext = crypto.encrypt(plaintext)
    assert ciphertext != plaintext
    assert crypto.decrypt(ciphertext) == plaintext


def test_decrypt_rejects_tampered() -> None:
    ciphertext = crypto.encrypt("original")
    tampered = ciphertext[:-4] + "AAAA"
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(tampered)


def test_encrypt_is_nondeterministic() -> None:
    # Same plaintext, two calls, different ciphertexts (Fernet includes a nonce)
    a = crypto.encrypt("same")
    b = crypto.encrypt("same")
    assert a != b
    assert crypto.decrypt(a) == crypto.decrypt(b) == "same"