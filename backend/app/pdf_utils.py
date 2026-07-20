from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Optional


@dataclass
class PagePayload:
    page_num: int
    text: str
    image_b64: Optional[str] = None
    mime_type: str = "image/jpeg"


def resolve_page_range(total_pages: int, page_start: Optional[int], page_end: Optional[int]) -> tuple[int, int]:
    start = page_start if page_start and page_start > 0 else 1
    end = page_end if page_end and page_end > 0 else total_pages
    start = max(1, min(start, total_pages))
    end = max(1, min(end, total_pages))
    if end < start:
        start, end = end, start
    return start, end


def extract_pdf_pages(
    file_bytes: bytes,
    *,
    parse_strategy: str = "ocr",
    page_start: Optional[int] = None,
    page_end: Optional[int] = None,
) -> list[PagePayload]:
    """Extract text (and optional page images) from a PDF."""
    import fitz  # pymupdf
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(file_bytes))
    total = len(reader.pages)
    if total == 0:
        return []

    start, end = resolve_page_range(total, page_start, page_end)
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    pages: list[PagePayload] = []

    for page_num in range(start, end + 1):
        native_text = ""
        try:
            native_text = (reader.pages[page_num - 1].extract_text() or "").strip()
        except Exception:  # noqa: BLE001
            native_text = ""

        try:
            fitz_text = (doc.load_page(page_num - 1).get_text("text") or "").strip()
            if len(fitz_text) > len(native_text):
                native_text = fitz_text
        except Exception:  # noqa: BLE001
            pass

        use_ocr = parse_strategy == "ocr" or len(native_text) < 40
        image_b64 = None
        if use_ocr:
            page = doc.load_page(page_num - 1)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image_b64 = base64.b64encode(pix.tobytes("jpeg")).decode("ascii")

        pages.append(
            PagePayload(
                page_num=page_num,
                text=native_text if native_text else ("OCR VISION EXTRACTION" if image_b64 else ""),
                image_b64=image_b64,
                mime_type="image/jpeg",
            )
        )

    doc.close()
    return pages


def extract_image_page(file_bytes: bytes, filename: str = "image.jpg") -> PagePayload:
    lower = filename.lower()
    mime = "image/png" if lower.endswith(".png") else "image/jpeg"
    b64 = base64.b64encode(file_bytes).decode("ascii")
    return PagePayload(page_num=1, text="OCR VISION EXTRACTION", image_b64=b64, mime_type=mime)


def extract_txt_chunks(text: str, max_chunk_size: int = 8000) -> list[PagePayload]:
    text = text or ""
    if len(text) <= max_chunk_size:
        return [PagePayload(page_num=1, text=text)]

    chunks: list[str] = []
    i = 0
    while i < len(text):
        end = i + max_chunk_size
        if end < len(text):
            window = text[max(i, end - 500) : end]
            nl = window.rfind("\n")
            if nl != -1:
                end = end - 500 + nl + 1
        chunks.append(text[i:end])
        i = end

    return [PagePayload(page_num=1, text=chunk) for chunk in chunks]
