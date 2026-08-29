from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Optional

# Maximum pages allowed in a single one-shot processing batch
MAX_PDF_PAGES = 500


@dataclass
class PagePayload:
    page_num: int
    text: str
    image_b64: Optional[str] = None
    mime_type: str = "image/jpeg"


def _page_needs_vision(native_text: str, page) -> bool:
    """Decide when sparse/garbled native text requires sending a rendered page image to the LLM."""
    t = (native_text or "").strip()
    if len(t) < 40:
        return True
    low = t.lower()
    if "intentionally left blank" in low:
        return True
    if len(t) < 180 and any(
        k in low
        for k in (
            "recommended spare parts",
            "rspl",
            "bill of material",
            "bill of materials",
            "spare parts list",
        )
    ):
        return True
    if len(t) >= 200:
        weird = sum(1 for ch in t if ord(ch) > 127 and not (ch.isalpha() or ch.isspace()))
        if weird / max(len(t), 1) > 0.2:
            return True
    if len(t) < 120:
        try:
            if page.get_images(full=True):
                return True
        except Exception:
            pass
    return False


def resolve_page_range(total_pages: int, page_start: Optional[int], page_end: Optional[int]) -> tuple[int, int]:
    start = page_start if page_start and page_start > 0 else 1
    end = page_end if page_end and page_end > 0 else total_pages
    start = max(1, min(start, total_pages))
    end = max(1, min(end, total_pages))
    if end < start:
        start, end = end, start

    if (end - start + 1) > MAX_PDF_PAGES:
        end = start + MAX_PDF_PAGES - 1
        end = min(end, total_pages)
    return start, end


def extract_pdf_pages(
    file_bytes: bytes,
    *,
    parse_strategy: str = "ocr",
    page_start: Optional[int] = None,
    page_end: Optional[int] = None,
    ocr_auto_rotate: bool = False,
) -> list[PagePayload]:
    """Extract text and optional page images from a PDF with resource controls."""
    import fitz  # pymupdf
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(file_bytes))
    total = len(reader.pages)
    if total == 0:
        return []

    if total > MAX_PDF_PAGES and not page_start and not page_end:
        page_start, page_end = 1, MAX_PDF_PAGES

    start, end = resolve_page_range(total, page_start, page_end)
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    pages: list[PagePayload] = []

    for page_num in range(start, end + 1):
        native_text = ""
        try:
            native_text = (reader.pages[page_num - 1].extract_text() or "").strip()
        except Exception:
            native_text = ""

        try:
            fitz_text = (doc.load_page(page_num - 1).get_text("text") or "").strip()
            if len(fitz_text) > len(native_text):
                native_text = fitz_text
        except Exception:
            pass

        page = doc.load_page(page_num - 1)
        use_ocr = parse_strategy == "ocr" or _page_needs_vision(native_text, page)
        if parse_strategy == "ocr" and len(native_text) >= 80 and not ocr_auto_rotate:
            use_ocr = False
        if ocr_auto_rotate:
            use_ocr = True

        image_b64 = None
        if use_ocr:
            rotate = 0
            if ocr_auto_rotate and len(native_text) < 40 and page.rect.height > page.rect.width:
                rotate = 90
            mat = fitz.Matrix(2, 2).prerotate(rotate) if rotate else fitz.Matrix(2, 2)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            image_b64 = base64.b64encode(pix.tobytes("jpeg")).decode("ascii")

        page_text = native_text
        if image_b64 and (not page_text or _page_needs_vision(native_text, page)):
            if "OCR VISION EXTRACTION" not in page_text.upper():
                page_text = (
                    f"{page_text}\n\nOCR VISION EXTRACTION".strip()
                    if page_text
                    else "OCR VISION EXTRACTION"
                )

        pages.append(
            PagePayload(
                page_num=page_num,
                text=page_text,
                image_b64=image_b64,
                mime_type="image/jpeg",
            )
        )

    doc.close()
    return pages


def convert_docx_to_pdf(file_bytes: bytes) -> bytes:
    import fitz  # pymupdf

    try:
        doc = fitz.open(stream=file_bytes, filetype="docx")
        pdf_bytes = doc.convert_to_pdf()
        doc.close()
        return pdf_bytes
    except Exception as err:
        raise ValueError(f"Failed to convert document to PDF: {err}") from err


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
