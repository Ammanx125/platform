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


def test_production_requires_secure_cookies() -> None:
    with pytest.raises(ValueError, match="COOKIE_SECURE must be true"):
        Settings.model_validate({
            "environment": "production",
            "database_url": "postgresql://localhost:5432/db",
            "jwt_secret": "x" * 32,
            "cookie_secure": False,
        })

    settings = Settings.model_validate({
        "environment": "production",
        "database_url": "postgresql://localhost:5432/db",
        "jwt_secret": "x" * 32,
        "cookie_secure": True,
        "redis_url": "redis://localhost:6379/0",
        "google_oauth_client_id": "client-id",
        "google_oauth_client_secret": "client-secret",
        "email_oauth_redirect_uri": "https://example.com/oauth/callback",
    })
    assert settings.cookie_secure


def test_production_requires_google_oauth_client_id() -> None:
    with pytest.raises(
        ValueError,
        match="GOOGLE_OAUTH_CLIENT_ID is required in production",
    ):
        Settings.model_validate({
            "environment": "production",
            "database_url": "postgresql://localhost:5432/db",
            "jwt_secret": "x" * 32,
            "cookie_secure": True,
            "redis_url": "redis://localhost:6379/0",
            "google_oauth_client_secret": "secret",
            "email_oauth_redirect_uri": "https://example.com/oauth/callback",
        })


def test_production_requires_google_oauth_client_secret() -> None:
    with pytest.raises(
        ValueError,
        match="GOOGLE_OAUTH_CLIENT_SECRET is required in production",
    ):
        Settings.model_validate({
            "environment": "production",
            "database_url": "postgresql://localhost:5432/db",
            "jwt_secret": "x" * 32,
            "cookie_secure": True,
            "redis_url": "redis://localhost:6379/0",
            "google_oauth_client_id": "client-id",
            "email_oauth_redirect_uri": "https://example.com/oauth/callback",
        })


def test_production_requires_https_oauth_redirect_uri() -> None:
    with pytest.raises(
        ValueError,
        match="EMAIL_OAUTH_REDIRECT_URI must be https in production",
    ):
        Settings.model_validate({
            "environment": "production",
            "database_url": "postgresql://localhost:5432/db",
            "jwt_secret": "x" * 32,
            "cookie_secure": True,
            "redis_url": "redis://localhost:6379/0",
            "google_oauth_client_id": "client-id",
            "google_oauth_client_secret": "secret",
            "email_oauth_redirect_uri": "http://example.com/oauth/callback",
        })


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