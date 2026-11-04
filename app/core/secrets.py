# app/core/secrets.py
"""
Credential reference resolver.

DataSources never store raw credentials in their config JSONB. Instead,
config holds a `credential_ref` (e.g. "ACME_PG"). This module resolves it
to the actual value at runtime, from environment variables.

Naming convention:
  credential_ref = "ACME_PG"
  env var        = SANSA_SECRET_ACME_PG

Tenant-scoped credentials are namespaced as:
  SANSA_SECRET_<TENANT_UUID>_ACME_PG

The namespaced form is checked first so a tenant cannot accidentally or
maliciously reuse a credential reference from another tenant. The legacy
global form remains as a backwards-compatible fallback for existing
deployments.
"""
from __future__ import annotations

import os
import re
from uuid import UUID

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


def _tenant_namespace(tenant_id: str | UUID | None) -> str:
    if tenant_id is None:
        return ""
    return str(tenant_id).upper().replace("-", "_")


def resolve(ref: str, *, tenant_id: str | UUID | None = None) -> str:
    """
    Look up a credential by reference.

    Namespaced tenant credentials win before the legacy global fallback,
    preventing cross-tenant secret reuse while preserving compatibility with
    existing single-tenant deployments.
    """
    _validate_ref(ref)
    namespace = _tenant_namespace(tenant_id)
    keys: list[str] = []
    if namespace:
        keys.append(f"{_PREFIX}{namespace}_{ref}")
    keys.append(f"{_PREFIX}{ref}")

    for key in keys:
        value = os.environ.get(key)
        if value is not None and value != "":
            return value

    tried = ", ".join(keys)
    raise SecretNotFound(f"no secret found for ref {ref!r} (tried: {tried})")


def exists(ref: str, *, tenant_id: str | UUID | None = None) -> bool:
    try:
        resolve(ref, tenant_id=tenant_id)
    except SecretNotFound:
        return False
    return True