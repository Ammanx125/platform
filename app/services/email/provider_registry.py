# app/services/email/provider_registry.py
"""
Email provider registry.

Mirrors app/services/ingestion/registry.py: a dict of concrete providers
keyed by name. Selected by EmailAccount.provider (or DataSource.source_type
mapped through service.py).

The fake provider is always registered. Real providers are registered only
if their dependencies are importable, so that environments without the
google-api-python-client package don't crash on import.
"""
from __future__ import annotations

from app.services.email.base import EmailProvider
from app.services.email.fake import FakeEmailProvider

_PROVIDERS: dict[str, EmailProvider] = {
    "fake": FakeEmailProvider(),
}


def _try_register_gmail() -> None:
    """Register the Gmail provider if google deps are installed."""
    try:
        from app.services.email.gmail import GmailProvider
    except ImportError:
        return
    _PROVIDERS["gmail"] = GmailProvider()


def _try_register_graph() -> None:
    """Register the Graph provider if its deps are installed."""
    try:
        from app.services.email.graph import GraphProvider
    except ImportError:
        return
    _PROVIDERS["graph"] = GraphProvider()


_try_register_gmail()
_try_register_graph()


def get_provider(name: str) -> EmailProvider:
    try:
        return _PROVIDERS[name]
    except KeyError as exc:
        raise ValueError(
            f"no email provider registered for {name!r}; "
            f"known: {sorted(_PROVIDERS.keys())}"
        ) from exc


def known_providers() -> list[str]:
    return sorted(_PROVIDERS.keys())