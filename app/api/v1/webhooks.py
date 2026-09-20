# app/api/v1/webhooks.py
"""
Inbound webhook receiver.

Public endpoint (no auth cookie, no CSRF token). Authentication is:

  1. The URL path contains a high-entropy token identifying the source.
  2. The X-Sansa-Signature header must match
     hex(HMAC-SHA256(decrypted_secret, raw_body)).

Both are checked before any state is written. Failures return 401 with no
information about which check failed.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.db.session import get_db
from app.services.ingestion import webhook as webhook_service

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/{token}", status_code=status.HTTP_202_ACCEPTED)
async def receive_webhook(
    token: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_sansa_signature: str | None = Header(default=None),
) -> dict[str, str]:
    if not x_sansa_signature:
        raise HTTPException(status_code=401, detail="invalid webhook")

    source = await webhook_service.load_webhook_source(db, token=token)
    if source is None or not source.webhook_secret_encrypted:
        raise HTTPException(status_code=401, detail="invalid webhook")

    try:
        secret = crypto.decrypt(source.webhook_secret_encrypted)
    except crypto.DecryptionError as exc:
        # The source exists but its secret can't be decrypted. This means
        # the encryption key was rotated without re-encrypting. Fail closed
        # rather than accept an unverifiable payload.
        raise HTTPException(
            status_code=500,
            detail="webhook secret unavailable; contact support",
        ) from exc

    raw_body = await request.body()
    if not webhook_service.verify_signature(
        raw_body=raw_body,
        secret=secret,
        provided=x_sansa_signature,
    ):
        raise HTTPException(status_code=401, detail="invalid webhook")

    headers = {k.lower(): v for k, v in request.headers.items()}

    await webhook_service.record_delivery(
        db,
        source=source,
        raw_body=raw_body,
        headers=headers,
    )
    await db.commit()
    return {"status": "accepted"}