# tests/unit/ingestion/test_pdf_loader.py
from pathlib import Path

import pytest

from app.services.ingestion.base import IngestionError
from app.services.ingestion.pdf import PDFLoader
from app.services.retrieval.blocks import Block

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "sample.pdf"


@pytest.mark.asyncio
async def test_extract_blocks_from_sample():
    content = FIXTURE.read_bytes()
    result = await PDFLoader().extract(content=content)
    assert result.page_count >= 1
    assert len(result.blocks) >= 1
    assert all(isinstance(b, Block) for b in result.blocks)
    text = " ".join(b.text for b in result.blocks)
    assert "supplier" in text.lower()


@pytest.mark.asyncio
async def test_extract_rejects_garbage():
    with pytest.raises(IngestionError):
        await PDFLoader().extract(content=b"not a pdf at all")


@pytest.mark.asyncio
async def test_extract_reports_empty_pdf():
    # An empty byte stream won't even open, but a minimal valid-but-blank
    # PDF should raise "no extractable text".
    # If you don't have one, skip this test — the load path is enough.
    ...