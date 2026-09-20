# app/core/secrets.py
"""
Credential reference resolver.

DataSources never store raw credentials in their config JSONB. Instead,
config holds a `credential_ref` (e.g. "ACME_PG"). This module resolves it
to the actual value at runtime, from environment variables.

Naming convention:
  credential_ref = "ACME_PG"
  env var        = SANSA_SECRET_ACME_PG

Rationale: keeping real secrets out of the database means a DB leak, a
backup dump, or a log line cannot expose customer credentials. When we
move to a managed secrets store (Vault, AWS Secrets Manager) later, this
module is the only thing that changes.
"""
from __future__ import annotations

import os
import re

from app.core.exceptions import SansaError


class SecretNotFound(SansaError):
    """A credential_ref that has no matching environment variable."""


# Uppercase letters, digits, underscore. Bounded length. No path traversal.
_REF_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")

_PREFIX = "SANSA_SECRET_"


def _validate_ref(ref: str) -> None:
    if not _REF_RE.match(ref):
        raise SecretNotFound(
            f"invalid credential_ref {ref!r}: must match {_REF_RE.pattern}"
        )


def resolve(ref: str) -> str:
    """
    Look up a credential by reference. Raises SecretNotFound if the
    reference is malformed or the environment variable is absent.
    """
    _validate_ref(ref)
    key = f"{_PREFIX}{ref}"
    value = os.environ.get(key)
    if value is None or value == "":
        raise SecretNotFound(f"no secret found for ref {ref!r} (env {key})")
    return value


def exists(ref: str) -> bool:
    try:
        resolve(ref)
    except SecretNotFound:
        return False
    return True