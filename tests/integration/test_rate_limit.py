# tests/integration/test_rate_limit.py
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_login_rate_limit(client: AsyncClient) -> None:
    """
    Hammer /auth/login 15 times with bad credentials. The limit is 10/min,
    so at least one should be 429.
    """
    statuses = []
    for _ in range(15):
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@nowhere.example", "password": "wrong123"},
        )
        statuses.append(r.status_code)

    assert 429 in statuses, f"expected a 429 in {statuses}"