# tests/unit/ingestion/test_http_ssrf.py
import pytest

from app.services.ingestion.base import IngestionError
from app.services.ingestion.http import _resolve_and_check


@pytest.mark.parametrize("host", [
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
])
def test_blocks_loopback(host: str) -> None:
    with pytest.raises(IngestionError):
        _resolve_and_check(host)


def test_blocks_metadata_ip() -> None:
    with pytest.raises(IngestionError):
        _resolve_and_check("169.254.169.254")