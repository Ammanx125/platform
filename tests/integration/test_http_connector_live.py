# tests/integration/test_http_connector_live.py
# Not part of the normal suite — mark as live/opt-in.
import pytest

from app.services.ingestion.http import HTTPConnector


class _FakeSource:
    def __init__(self, config):
        self.config = config
        self.source_type = "http"


@pytest.mark.asyncio
async def test_fetch_https_public_endpoint() -> None:
    """
    Opt-in test: hits a real HTTPS endpoint to verify the SSRF-safe transport
    works with TLS SNI. Run with: pytest -m live
    """
    pytest.skip("opt-in; run manually to verify transport")
    conn = HTTPConnector()
    src = _FakeSource({
        "url": "https://httpbin.org/json",
        "format": "json",
        "json_path": "$.slideshow.slides",
    })
    result = await conn.ingest(source=src)
    assert len(result.rows) > 0