# tests/unit/test_crypto.py
import pytest

from app.core import crypto
from app.core.config import Settings


def test_jwt_secret_must_be_at_least_32_bytes() -> None:
    with pytest.raises(ValueError, match="JWT_SECRET must be at least 32 bytes"):
        Settings(
            environment="development",
            database_url="postgresql+asyncpg://user:pass@localhost:5432/db",
            jwt_secret="short-secret-too-short",
        )


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