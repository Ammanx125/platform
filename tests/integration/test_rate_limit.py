# tests/integration/test_rate_limit.py
import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.core.rate_limit import _create_limiter
from app.main import app, lifespan


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


def test_production_requires_redis() -> None:
    with pytest.raises(ValidationError, match="REDIS_URL is required"):
        Settings.model_validate({
            "database_url": "postgresql+asyncpg://localhost/sansa",
            "jwt_secret": "x" * 32,
            "environment": "production",
            "cookie_secure": True,
            "redis_url": None,
            "google_oauth_client_id": "test-client-id",
            "google_oauth_client_secret": "test-client-secret",
            "email_oauth_redirect_uri": "https://example.com/oauth/callback",
        })


def test_limiter_uses_configured_redis_storage() -> None:
    limiter = _create_limiter("redis://localhost:6379/1")
    assert type(limiter._storage).__name__ == "RedisStorage"
    assert limiter._storage_uri == "redis://localhost:6379/1"


@pytest.mark.asyncio
async def test_production_startup_pings_and_closes_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRedis:
        closed = False

        async def ping(self) -> bool:
            return True

        async def aclose(self) -> None:
            self.closed = True

    fake_redis = FakeRedis()
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr("app.main.Redis.from_url", lambda _: fake_redis)

    async with lifespan(app):
        pass

    assert fake_redis.closed


@pytest.mark.asyncio
async def test_production_startup_fails_when_redis_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnavailableRedis:
        closed = False

        async def ping(self) -> bool:
            raise OSError("connection refused")

        async def aclose(self) -> None:
            self.closed = True

    redis = UnavailableRedis()
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr("app.main.Redis.from_url", lambda _: redis)

    with pytest.raises(RuntimeError, match="Redis rate-limit backend is unavailable"):
        async with lifespan(app):
            pass

    assert redis.closed