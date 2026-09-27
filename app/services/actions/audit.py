# app/services/actions/audit.py
"""
DEPRECATED. Use app.services.audit.service.emit.

This module re-exports the centralized audit helper for backward
compatibility with existing call sites in the action pipeline. New code
should import from app.services.audit.service directly.
"""
from __future__ import annotations

from app.services.audit.service import emit as emit_event  # noqa: F401

__all__ = ["emit_event"]