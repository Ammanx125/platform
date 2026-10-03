from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.core.exceptions import AuthError
from app.db.models.refresh_token import RefreshToken
from app.db.session import SessionLocal
from app.services.auth import service as auth_service

pytestmark = pytest.mark.concurrency


@pytest.mark.asyncio
async def test_concurrent_refresh_detects_reuse_and_revokes_family(
    concurrency_env: dict,
) -> None:
    async with SessionLocal() as db:
        _, _, initial_token = await auth_service.login(
            db,
            email=concurrency_env["email_a"],
            password=concurrency_env["password"],
            tenant_slug=concurrency_env["tenant_slug_a"],
        )

    barrier = asyncio.Barrier(2)

    async def rotate() -> tuple[str, str | None]:
        async with SessionLocal() as db:
            await barrier.wait()
            try:
                _, _, token = await auth_service.refresh(
                    db, raw_refresh_token=initial_token
                )
                return "rotated", token
            except AuthError as exc:
                return str(exc), None

    outcomes = await asyncio.gather(rotate(), rotate())
    assert sum(outcome == "rotated" for outcome, _ in outcomes) == 1
    assert sum(
        outcome == "refresh token reuse detected" for outcome, _ in outcomes
    ) == 1

    async with SessionLocal() as db:
        tokens = list(
            (
                await db.execute(
                    select(RefreshToken).where(
                        RefreshToken.tenant_id == concurrency_env["tenant_a"],
                        RefreshToken.user_id == concurrency_env["user_a"],
                    )
                )
            ).scalars().all()
        )
    assert len(tokens) == 2
    assert all(token.revoked_at is not None for token in tokens)
