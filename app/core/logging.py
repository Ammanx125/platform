# app/core/logging.py
"""
Logging configuration.

Structured logging via structlog. A redaction processor runs on every
event dict before rendering, so any string value that looks like a secret
is replaced with a placeholder. This is a safety net — callers should also
redact explicitly before persisting secrets-adjacent data.
"""
from __future__ import annotations

import logging
import sys

import structlog

from app.core.config import settings
from app.services.security.redaction import redact_dict


def _redaction_processor(logger, method_name, event_dict):
    """
    structlog processor: redact secrets in every event dict.

    Runs before the renderer. Structured fields (values in the event dict)
    are redacted recursively; the message itself is redacted as a string.
    """
    return redact_dict(event_dict)


def configure_logging() -> None:
    """
    Configure structlog + stdlib logging.

    Called once at startup (from app.main or an equivalent entry point).
    Idempotent — repeated calls are safe.
    """
    level = logging.DEBUG if settings.environment == "development" else logging.INFO

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redaction_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None):
    return structlog.get_logger(name) if name else structlog.get_logger()