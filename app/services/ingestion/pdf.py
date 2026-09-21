# app/services/ingestion/pdf.py
"""
PDF extraction.

Not a PullConnector. PDFs produce a Document (with blocks), not StagedRows.
This module provides the extraction primitive — given PDF bytes, return
blocks and metadata. The pipeline that calls it lives in ingestion/service.py.

Uses pdfplumber (MIT). Chosen over pymupdf (AGPL) because Sansa is a SaaS
platform and AGPL's network-use clause is a real obligation we don't want.

Design:
  - Per-page extraction. Pages are the natural structural boundary and
    become blocks with page metadata.
  - Within a page, group text by paragraph using pdfplumber's word
    positions and vertical gaps. A gap larger than ~1.5x the median line
    height indicates a paragraph break.
  - Headings are detected heuristically: a line substantially larger than
    the body text, or shorter than a threshold with no terminal punctuation.
    This is approximate; PDFs vary wildly in how they encode structure.
  - Image-only pages (scanned PDFs) are detected and reported as errors.
    OCR is out of scope.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

import pdfplumber
from pdfplumber.page import Page

from app.core.config import settings
from app.services.ingestion.base import IngestionError
from app.services.retrieval.blocks import Block


@dataclass
class PDFExtractionResult:
    blocks: list[Block]
    page_count: int
    title: str | None
    errors: list[dict[str, Any]] = field(default_factory=list)
    doc_metadata: dict[str, Any] = field(default_factory=dict)


# --- heuristics ------------------------------------------------------------

# A gap between lines greater than this multiple of the median line height
# is treated as a paragraph break.
_PARAGRAPH_GAP_MULTIPLIER = 1.5

# A line whose font size exceeds body size by this factor is a heading.
_HEADING_SIZE_MULTIPLIER = 1.2

# A line shorter than this many characters, ending without terminal
# punctuation, with no following line at the same indent, is heading-ish.
_HEADING_MAX_CHARS = 80


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _lines_from_page(page: Page) -> list[dict[str, Any]]:
    """
    Extract text lines with position and font information.

    pdfplumber gives us words with bounding boxes. We group words into lines
    by their vertical center, then compute per-line: text, y-position, and
    the dominant font size.
    """
    words = page.extract_words(
        extra_attrs=["size", "fontname"],
        use_text_flow=False,
        keep_blank_chars=False,
    )
    if not words:
        return []

    # Group words into lines. Words on the same line have overlapping
    # vertical ranges. Use a tolerance of 2 points.
    lines_by_y: dict[float, list[dict[str, Any]]] = {}
    for w in words:
        y_center = (w["top"] + w["bottom"]) / 2.0
        # Find an existing line bucket within tolerance.
        placed = False
        for key in lines_by_y:
            if abs(key - y_center) <= 2.0:
                lines_by_y[key].append(w)
                placed = True
                break
        if not placed:
            lines_by_y[y_center] = [w]

    result: list[dict[str, Any]] = []
    for y in sorted(lines_by_y.keys()):
        group = sorted(lines_by_y[y], key=lambda w: w["x0"])
        text = " ".join(w["text"] for w in group).strip()
        if not text:
            continue
        sizes = [float(w.get("size", 0.0)) for w in group if w.get("size")]
        dominant_size = _median(sizes) if sizes else 0.0
        top = min(w["top"] for w in group)
        bottom = max(w["bottom"] for w in group)
        result.append({
            "text": text,
            "y": y,
            "top": top,
            "bottom": bottom,
            "height": bottom - top,
            "size": dominant_size,
        })
    return result


def _group_into_paragraphs(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Group consecutive lines into paragraph-ish units.

    A new paragraph starts when:
      - the vertical gap from the previous line exceeds the threshold, or
      - the current line's font size differs meaningfully from the previous
        (heading boundary).
    """
    if not lines:
        return []

    heights = [ln["height"] for ln in lines if ln["height"] > 0]
    median_height = _median(heights) or 10.0
    gap_threshold = median_height * _PARAGRAPH_GAP_MULTIPLIER

    paragraphs: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = [lines[0]]
    for prev, cur in zip(lines, lines[1:], strict=False):
        gap = cur["top"] - prev["bottom"]
        size_changed = (
            abs(cur["size"] - prev["size"]) > 1.0
            if cur["size"] and prev["size"]
            else False
        )
        if gap > gap_threshold or size_changed:
            paragraphs.append({
                "lines": current,
                "text": " ".join(ln["text"] for ln in current).strip(),
                "size": _median([ln["size"] for ln in current if ln["size"]]) or 0.0,
            })
            current = [cur]
        else:
            current.append(cur)
    if current:
        paragraphs.append({
            "lines": current,
            "text": " ".join(ln["text"] for ln in current).strip(),
            "size": _median([ln["size"] for ln in current if ln["size"]]) or 0.0,
        })
    return paragraphs


def _classify_paragraph(
    para: dict[str, Any],
    *,
    body_size: float,
) -> str:
    """
    Decide if a paragraph is a heading or body text.

    Headings: notably larger font than body, or short (< _HEADING_MAX_CHARS)
    without terminal punctuation. This is a heuristic and will misfire on
    some documents; the block's `kind` is advisory, not load-bearing for
    correctness (the chunker will still respect paragraph boundaries).
    """
    text = para["text"]
    size = para["size"]

    if size and body_size and size >= body_size * _HEADING_SIZE_MULTIPLIER:
        return "heading"
    if (
        len(text) <= _HEADING_MAX_CHARS
        and text
        and text[-1] not in ".!?:;,"
        and not text.endswith(")")
    ):
        # Short, no terminal punctuation — likely a heading.
        return "heading"
    return "paragraph"


class PDFLoader:
    """
    Extracts text and structure from a PDF.

    Usage:
        result = await PDFLoader().extract(content=pdf_bytes)
        # result.blocks, result.page_count, result.errors

    Never raises for content-level problems (empty PDF, scanned pages) —
    those come back in result.errors. Raises IngestionError only for
    unrecoverable problems: unparseable PDF, encrypted PDF, too many pages.
    """

    async def extract(self, *, content: bytes) -> PDFExtractionResult:
        # pdfplumber is synchronous; this whole method runs on the caller's
        # thread. The worker calls it via asyncio.to_thread if we care about
        # blocking; for now, keep it synchronous — the pipeline call happens
        # inside the worker, not the request handler.
        try:
            pdf = pdfplumber.open(io.BytesIO(content))
        except Exception as exc:  # noqa: BLE001
            raise IngestionError(f"could not open PDF: {exc}") from exc

        try:
            if pdf.doc.encryption is not None:
                raise IngestionError(
                    "encrypted PDFs are not supported; remove the password first"
                )

            page_count = len(pdf.pages)
            if page_count == 0:
                raise IngestionError("PDF has no pages")
            if page_count > settings.pdf_max_pages:
                raise IngestionError(
                    f"PDF has {page_count} pages, max is {settings.pdf_max_pages}"
                )

            blocks: list[Block] = []
            errors: list[dict[str, Any]] = []
            total_chars = 0
            body_size_estimate = 0.0

            for page_index, page in enumerate(pdf.pages):
                try:
                    lines = _lines_from_page(page)
                except Exception as exc:  # noqa: BLE001
                    errors.append({
                        "page": page_index + 1,
                        "error": f"page extraction failed: {exc}",
                    })
                    continue

                page_chars = sum(len(ln["text"]) for ln in lines)
                total_chars += page_chars

                if page_chars < settings.pdf_min_chars_per_page:
                    errors.append({
                        "page": page_index + 1,
                        "error": "page has no extractable text (image-only?)",
                    })
                    continue

                if not lines:
                    continue

                # Body size = median of all line sizes on this page.
                if not body_size_estimate:
                    page_sizes = [ln["size"] for ln in lines if ln["size"]]
                    body_size_estimate = _median(page_sizes)

                paragraphs = _group_into_paragraphs(lines)
                for p in paragraphs:
                    kind = _classify_paragraph(p, body_size=body_size_estimate)
                    meta: dict[str, Any] = {"page": page_index + 1}
                    if kind == "heading":
                        meta["level"] = 1  # PDFs rarely expose nesting; default 1
                    blocks.append(Block(text=p["text"], kind=kind, metadata=meta))

                # Page boundary marker for the chunker's continuity logic.
                if page_index < page_count - 1:
                    blocks.append(Block(text="", kind="page_break", metadata={"page": page_index + 1}))

            if total_chars == 0:
                raise IngestionError(
                    "PDF contains no extractable text; OCR is not supported"
                )

            title = None
            try:
                info = pdf.metadata or {}
                title = info.get("Title") or None
            except Exception:  # noqa: BLE001
                pass

            return PDFExtractionResult(
                blocks=blocks,
                page_count=page_count,
                title=title,
                errors=errors,
                doc_metadata={
                    "page_count": page_count,
                    "extracted_chars": total_chars,
                },
            )
        finally:
            try:
                pdf.close()
            except Exception:  # noqa: BLE001
                pass