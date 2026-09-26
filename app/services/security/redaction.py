# app/services/security/redaction.py
"""
Secret redaction.

Applied before:
  - writing structured data to the DB (evidence packages, action arguments,
    audit metadata)
  - emitting log records

Conservative: each pattern targets a specific secret format. Freeform text
is only touched where a secret-shaped token appears.

The redactor is idempotent: running it twice yields the same output.
"""
from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.services.security.patterns import REDACTION_PATTERNS


def redact(text: str) -> str:
    """
    Replace secret-shaped tokens in a string with placeholders.

    Idempotent — redacting an already-redacted string is a no-op because
    the replacement values don't match any pattern.
    """
    if not settings.log_redaction_enabled:
        return text
    if not text:
        return text
    out = text
    for pattern, replacement in REDACTION_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def redact_dict(obj: Any) -> Any:
    """
    Recursively redact secrets in a nested structure (dict / list / str).

    Non-string scalars (int, float, bool, None) pass through unchanged.
    Unrecognized types are stringified, redacted, and returned as strings —
    this is the safe default for anything that might carry a secret in its
    repr().
    """
    if not settings.log_redaction_enabled:
        return obj
    if obj is None or isinstance(obj, (int, float, bool)):
        return obj
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_dict(v) for v in obj]
    # Fall back to stringifying. Safer than passing an unknown type through.
    return redact(repr(obj))