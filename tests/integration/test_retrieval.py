# tests/integration/test_retrieval.py
import pytest
from httpx import AsyncClient


def _csrf(client: AsyncClient) -> dict[str, str]:
    t = client.cookies.get("sansa_csrf")
    return {"X-CSRF-Token": t} if t else {}


async def _login(client: AsyncClient, email: str, password: str) -> None:
    await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )


async def _create_doc(client: AsyncClient, title: str, text: str) -> str:
    r = await client.post(
        "/api/v1/knowledge/documents",
        json={"title": title, "content_type": "text", "raw_text": text},
        headers=_csrf(client),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_create_and_search_hybrid(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    await _create_doc(
        client, "Contract A",
        "This contract specifies payment terms with supplier acme bolts. "
        "The supplier must deliver within thirty days.",
    )
    await _create_doc(
        client, "Contract B",
        "This agreement covers the supply of aluminium sheets from beacon "
        "metals. Delivery window is sixty days.",
    )

    r = await client.post(
        "/api/v1/knowledge/search",
        json={"query": "supplier acme", "strategy": "hybrid", "top_k": 5},
        headers=_csrf(client),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["items"]) > 0
    # Top hit should mention acme
    assert "acme" in body["items"][0]["content"].lower()


@pytest.mark.asyncio
async def test_vector_strategy(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])
    await _create_doc(
        client, "V1", "supplier acme delivers steel bolts on time"
    )
    r = await client.post(
        "/api/v1/knowledge/search",
        json={"query": "steel bolts", "strategy": "vector", "top_k": 3},
        headers=_csrf(client),
    )
    assert r.status_code == 200
    assert len(r.json()["items"]) > 0


@pytest.mark.asyncio
async def test_keyword_strategy(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])
    await _create_doc(
        client, "K1", "the quick brown fox jumps over the lazy dog"
    )
    r = await client.post(
        "/api/v1/knowledge/search",
        json={"query": "quick fox", "strategy": "keyword", "top_k": 3},
        headers=_csrf(client),
    )
    assert r.status_code == 200
    assert len(r.json()["items"]) > 0


@pytest.mark.asyncio
async def test_tenant_isolation(
    client: AsyncClient, two_tenants: dict
) -> None:
    # Tenant A creates a document
    await _login(client, two_tenants["email_a"], two_tenants["password"])
    await _create_doc(client, "A-secret", "confidential acme acquisition plan")

    # Tenant B searches for the same terms
    client.cookies.clear()
    await _login(client, two_tenants["email_b"], two_tenants["password"])
    r = await client.post(
        "/api/v1/knowledge/search",
        json={"query": "confidential acquisition", "strategy": "hybrid", "top_k": 5},
        headers=_csrf(client),
    )
    assert r.status_code == 200
    assert r.json()["items"] == []


@pytest.mark.asyncio
async def test_document_lifecycle(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    doc_id = await _create_doc(client, "Temp", "some text content")

    r = await client.get(f"/api/v1/knowledge/documents/{doc_id}")
    assert r.status_code == 200
    assert r.json()["chunk_count"] >= 1

    r = await client.post(
        f"/api/v1/knowledge/documents/{doc_id}/rechunk",
        headers=_csrf(client),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ready"

    r = await client.delete(
        f"/api/v1/knowledge/documents/{doc_id}",
        headers=_csrf(client),
    )
    assert r.status_code == 204

    r = await client.get(f"/api/v1/knowledge/documents/{doc_id}")
    assert r.status_code == 404