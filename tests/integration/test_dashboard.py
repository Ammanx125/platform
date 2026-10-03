from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.db.models.analytics import Forecast
from app.db.session import SessionLocal


@pytest.mark.asyncio
async def test_overview_shows_real_saved_forecast_and_current_signals(
    client: AsyncClient, two_tenants: dict
) -> None:
    login = await client.post(
        "/api/v1/auth/login",
        json={
            "email": two_tenants["email_a"],
            "password": two_tenants["password"],
        },
    )
    assert login.status_code == 200

    async with SessionLocal() as db:
        db.add(
            Forecast(
                tenant_id=two_tenants["tenant_a"],
                value_concept="monthly_revenue",
                group_key="",
                group_label="",
                source_ids=[str(two_tenants["tenant_a"])],
                horizon=2,
                frequency="daily",
                has_seasonality=False,
                seasonality_period=None,
                model_used="exponential_smoothing",
                candidates_evaluated=[],
                evaluation_metric="mae",
                validation_error=2.5,
                predicted_points=[
                    {"timestamp": "2026-10-04T00:00:00+00:00", "value": 45.0},
                    {"timestamp": "2026-10-05T00:00:00+00:00", "value": 48.0},
                ],
                lower_bound=[],
                upper_bound=[],
                reliability="high",
                status="ok",
                notes={},
                trained_at=datetime(2026, 10, 3, tzinfo=UTC),
            )
        )
        await db.commit()

    response = await client.get("/")

    assert response.status_code == 200
    assert "Business command center" in response.text
    assert "Monthly Revenue" in response.text
    assert "Exponential Smoothing" in response.text
    assert "720.0,12.0" in response.text
    assert "Ask Sansa" in response.text
