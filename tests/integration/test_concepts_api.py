# tests/integration/test_concepts_api.py
import pytest
from httpx import AsyncClient

from app.db.seed_concepts import seed_canonical_concepts
from app.db.seed_packs import seed_industry_packs
from app.db.session import SessionLocal


def _csrf(client: AsyncClient) -> dict[str, str]:
    t = client.cookies.get("sansa_csrf")
    return {"X-CSRF-Token": t} if t else {}


async def _login(client: AsyncClient, email: str, password: str) -> None:
    await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )


@pytest.mark.asyncio
async def test_install_and_query_pack(
    client: AsyncClient, two_tenants: dict
) -> None:
    # Seed packs and concepts
    async with SessionLocal() as db:
        await seed_canonical_concepts(db)
        await seed_industry_packs(db)
        await db.commit()

    await _login(client, two_tenants["email_a"], two_tenants["password"])

    # List available packs
    r = await client.get("/api/v1/knowledge/industry-packs")
    assert r.status_code == 200
    keys = {p["key"] for p in r.json()}
    assert "procurement.v1" in keys

    # Install
    r = await client.post(
        "/api/v1/knowledge/industry-packs/procurement.v1/install",
        headers=_csrf(client),
    )
    assert r.status_code == 201, r.text
    assert r.json()["already_installed"] is False

    # Install again — idempotent
    r = await client.post(
        "/api/v1/knowledge/industry-packs/procurement.v1/install",
        headers=_csrf(client),
    )
    assert r.status_code == 201
    assert r.json()["already_installed"] is True

    # List installed for tenant
    r = await client.get("/api/v1/knowledge/tenants/me/industry-packs")
    assert r.status_code == 200
    assert {p["pack_key"] for p in r.json()} == {"procurement.v1"}

    # Concept with relationships
    r = await client.get("/api/v1/knowledge/concepts/Procurement.Purchase")
    assert r.status_code == 200
    body = r.json()
    assert body["concept"]["key"] == "Procurement.Purchase"
    assert any(
        r["to_key"] == "Procurement.Supplier" and r["kind"] == "belongs_to"
        for r in body["outgoing"]
    )

    # Neighbors
    r = await client.get("/api/v1/knowledge/concepts/Procurement.Purchase/neighbors?depth=1")
    assert r.status_code == 200
    neighbor_keys = {n["key"] for n in r.json()}
    assert "Procurement.Supplier" in neighbor_keys


@pytest.mark.asyncio
async def test_concept_isolation_across_tenants(
    client: AsyncClient, two_tenants: dict
) -> None:
    # Concepts are global — both tenants can read them.
    async with SessionLocal() as db:
        await seed_canonical_concepts(db)
        await db.commit()

    await _login(client, two_tenants["email_a"], two_tenants["password"])
    r = await client.get("/api/v1/knowledge/concepts/Sales.Customer")
    assert r.status_code == 200

    client.cookies.clear()
    await _login(client, two_tenants["email_b"], two_tenants["password"])
    r = await client.get("/api/v1/knowledge/concepts/Sales.Customer")
    assert r.status_code == 200