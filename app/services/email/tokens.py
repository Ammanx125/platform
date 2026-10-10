# app/services/email/tokens.py
"""
Encryption and JSON (de)serialization for OAuth token bundles.

EmailAccount.oauth_tokens_encrypted stores a Fernet ciphertext of a JSON
object. This module is the only place that knows that shape. Everything
else in the codebase talks to OAuthTokenSet, never to the encrypted blob.

Encryption uses app.core.crypto, the same Fernet that protects webhook
secrets. That is intentional: both are recoverable-at-rest secrets.
Hashed credentials (Agent.credential_hash, User.password_hash) are a
different discipline and stay hashed.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from app.core.crypto import decrypt, encrypt
from app.services.email.base import EmailAuthError, OAuthTokenSet


def _tokens_to_dict(tokens: OAuthTokenSet) -> dict:
    return {
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "expires_at": tokens.expires_at.astimezone(UTC).isoformat(),
        "scope": tokens.scope,
        "token_type": tokens.token_type,
    }


def _tokens_from_dict(data: dict) -> OAuthTokenSet:
    try:
        expires_at_raw = data["expires_at"]
        expires_at = datetime.fromisoformat(expires_at_raw)
    except (KeyError, TypeError, ValueError) as exc:
        raise EmailAuthError(
            f"stored token bundle is missing or malformed: {exc}"
        ) from exc

    if expires_at.tzinfo is None:
        # Defensive: we always write tz-aware, but never trust stored data.
        expires_at = expires_at.replace(tzinfo=UTC)

    try:
        return OAuthTokenSet(
            access_token=str(data["access_token"]),
            refresh_token=(
                str(data["refresh_token"])
                if data.get("refresh_token") is not None
                else None
            ),
            expires_at=expires_at,
            scope=str(data.get("scope", "")),
            token_type=str(data.get("token_type", "Bearer")),
        )
    except KeyError as exc:
        raise EmailAuthError(
            f"stored token bundle missing required field: {exc}"
        ) from exc


def serialize_tokens(tokens: OAuthTokenSet) -> str:
    """
    Encrypt an OAuthTokenSet for storage in EmailAccount.

    Never logs. Never returns plaintext. Raises on anything that isn't
    a well-formed bundle.
    """
    payload = json.dumps(_tokens_to_dict(tokens), separators=(",", ":"))
    return encrypt(payload)


def deserialize_tokens(ciphertext: str | None) -> OAuthTokenSet:
    """
    Decrypt and parse an EmailAccount's stored tokens.

    Raises EmailAuthError if the value is absent, corrupt, or malformed.
    Callers should treat this as "the account needs to re-authorize",
    not as a hard failure.
    """
    if not ciphertext:
        raise EmailAuthError("email account has no stored tokens")
    try:
        payload = decrypt(ciphertext)
    except Exception as exc:  # noqa: BLE001 — DecryptionError is a SansaError subclass
        raise EmailAuthError(
            "stored tokens could not be decrypted (key mismatch or corruption)"
        ) from exc
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise EmailAuthError(
            "stored tokens are not valid JSON after decryption"
        ) from exc
    if not isinstance(data, dict):
        raise EmailAuthError("stored tokens are not a JSON object")
    return _tokens_from_dict(data)


def is_expired(tokens: OAuthTokenSet, *, skew_seconds: int = 60) -> bool:
    now = datetime.now(UTC)
    return (tokens.expires_at.timestamp() - now.timestamp()) <= skew_seconds