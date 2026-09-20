# app/core/crypto.py
"""
Symmetric encryption for at-rest secrets that must be recoverable.

Only use this for values that need to be *decrypted* later (e.g. webhook
signing secrets, where HMAC verification needs the plaintext). Passwords and
API keys that only need to be *verified* must be hashed, not encrypted.

The key comes from SANSA_WEBHOOK_ENC_KEY. In development, if the key is
unset, a deterministic local key is derived from JWT_SECRET so local work
doesn't require key management. In production, the key MUST be set
explicitly — the derived-key fallback is refused.
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.exceptions import SansaError


class DecryptionError(SansaError):
    """A ciphertext could not be decrypted (wrong key, tampered, corrupt)."""


def _derive_key_from_jwt_secret() -> bytes:
    """
    Deterministic dev-only key. Takes SHA-256 of the JWT secret, then
    base64-url-encodes it (Fernet requires 32 bytes, URL-safe-b64).
    """
    digest = hashlib.sha256(settings.jwt_secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _get_fernet() -> Fernet:
    key_str = settings.webhook_enc_key
    if key_str:
        try:
            return Fernet(key_str.encode("utf-8"))
        except Exception as exc:
            raise SansaError(f"invalid WEBHOOK_ENC_KEY: {exc}") from exc

    if settings.environment != "development":
        raise SansaError(
            "WEBHOOK_ENC_KEY must be set in non-development environments"
        )
    return Fernet(_derive_key_from_jwt_secret())


def generate_key() -> str:
    """
    Utility to generate a fresh Fernet key. Call once per environment:
        python -c "from app.core.crypto import generate_key; print(generate_key())"
    """
    return Fernet.generate_key().decode("utf-8")


def encrypt(plaintext: str) -> str:
    f = _get_fernet()
    token = f.encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt(ciphertext: str) -> str:
    f = _get_fernet()
    try:
        return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise DecryptionError(
            "ciphertext could not be decrypted (wrong key or tampered value)"
        ) from exc