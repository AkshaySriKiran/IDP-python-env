from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from ..models import (
    ExtractMeta,
    ExtractOptions,
    ExtractResponse,
    MaintenanceRow,
    PageText,
    SparePartRow,
    TroubleshootingRow,
)
from ..pdf_utils import extract_image_page, extract_pdf_pages, extract_txt_chunks
from .gemini import run_gemini_extractor
from .ollama import run_ollama_extractor


def _ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _page_sort_key(row: dict[str, Any]) -> tuple:
    page = row.get("page", "")
    page_num = int(page) if str(page).isdigit() else 10**9
    return (
        page_num,
        str(row.get("equipment_title") or ""),
        str(row.get("part_name") or row.get("subsystem_component") or row.get("problem") or ""),
    )


async def _extract_page(
    page_text: str,
    doc_name: str,
    page_num: int,
    options: ExtractOptions,
    *,
    image_b64: Optional[str] = None,
    mime_type: str = "image/jpeg",
) -> dict[str, list[dict[str, Any]]]:
    empty = {"maintenance": [], "spare_parts": [], "troubleshooting": []}
    if options.engine == "ollama":
        return await run_ollama_extractor(
            page_text,
            doc_name,
            page_num,
            ollama_url=options.ollama_url,
            model=options.ollama_model,
            base64_image=image_b64,
            equipment_category=options.equipment_category,
            learned_patterns=options.learned_patterns,
        ) or empty
    return await run_gemini_extractor(
        page_text,
        doc_name,
        page_num,
        api_key=options.gemini_api_key or "",
        model=options.gemini_model,
        base64_image=image_b64,
        mime_type=mime_type,
        equipment_category=options.equipment_category,
        learned_patterns=options.learned_patterns,
    ) or empty


async def extract_document(
    file_bytes: bytes,
    filename: str,
    options: ExtractOptions,
    *,
    on_progress: Optional[Callable[[str, float], None]] = None,
) -> ExtractResponse:
    ext = _ext(filename)
    warnings: list[str] = []
    pages_out: list[PageText] = []
    all_maint: list[dict[str, Any]] = []
    all_spares: list[dict[str, Any]] = []
    all_trouble: list[dict[str, Any]] = []

    if ext == "pdf":
        # History cards are scanned photos; always OCR + auto-rotate sideways pages.
        is_logbook = (options.equipment_category or "").strip() == "Logbook"
        effective_strategy = "ocr" if is_logbook else options.parse_strategy
        page_payloads = await asyncio.to_thread(
            extract_pdf_pages,
            file_bytes,
            parse_strategy=effective_strategy,
            page_start=options.page_start,
            page_end=options.page_end,
            ocr_auto_rotate=is_logbook,
        )
        from ..pdf_utils import MAX_PDF_PAGES

        if len(page_payloads) >= MAX_PDF_PAGES and not options.page_end:
            warnings.append(
                f"Page limit is {MAX_PDF_PAGES}. Processed {len(page_payloads)} page(s); "
                f"set a From/To range if you need a different slice."
            )
    elif ext == "txt":
        text = file_bytes.decode("utf-8", errors="replace")
        page_payloads = extract_txt_chunks(text)
    elif ext in {"jpg", "jpeg", "png"}:
        page_payloads = [extract_image_page(file_bytes, filename)]
    else:
        raise ValueError(
            f"Unsupported file type for Python API: .{ext or 'unknown'}. "
            "Use PDF, TXT, JPG, or PNG (Word/DOCX still works via the in-browser extractor)."
        )

    if not page_payloads:
        return ExtractResponse(
            maintenance=[],
            spare_parts=[],
            troubleshooting=[],
            pages=[],
            meta=ExtractMeta(
                filename=filename,
                engine=options.engine,
                parse_strategy=options.parse_strategy,
                warnings=["No pages found in document."],
            ),
        )

    for p in page_payloads:
        # Truncate stored page text so a huge-page response stays browser-safe.
        text = p.text or ""
        if len(text) > 3000:
            text = text[:3000] + "…"
        pages_out.append(PageText(pageNum=p.page_num, text=text))

    # Process EVERY page — no TOC / keyword skipping.
    llm_payloads = list(page_payloads)

    if not llm_payloads:
        return ExtractResponse(
            maintenance=[],
            spare_parts=[],
            troubleshooting=[],
            pages=pages_out,
            meta=ExtractMeta(
                filename=filename,
                engine=f"{options.engine}:{options.gemini_model if options.engine == 'gemini' else options.ollama_model}",
                parse_strategy=options.parse_strategy,
                pages_total=len(page_payloads),
                pages_processed=0,
                warnings=warnings + ["No pages found to process."],
            ),
        )

    # Higher concurrency for full-book Gemini runs (still rate-limit friendly).
    concurrency = 8 if options.engine == "gemini" else 1
    sem = asyncio.Semaphore(concurrency)
    processed = 0
    total = len(llm_payloads)

    async def worker(payload):
        nonlocal processed
        text_for_llm = payload.text or ("OCR VISION EXTRACTION" if payload.image_b64 else "")
        if not text_for_llm.strip() and not payload.image_b64:
            processed += 1
            return {"maintenance": [], "spare_parts": [], "troubleshooting": []}
        async with sem:
            try:
                result = await _extract_page(
                    text_for_llm if text_for_llm.strip() else "OCR VISION EXTRACTION",
                    filename,
                    payload.page_num,
                    options,
                    image_b64=payload.image_b64,
                    mime_type=payload.mime_type,
                )
            except Exception as err:  # noqa: BLE001
                warnings.append(f"Page {payload.page_num}: {err}")
                result = {"maintenance": [], "spare_parts": [], "troubleshooting": []}
            processed += 1
            if on_progress:
                on_progress(f"Processed page {processed}/{total}", processed / total)
            return result

    results = await asyncio.gather(*[worker(p) for p in llm_payloads])
    for result in results:
        all_maint.extend(result.get("maintenance") or [])
        all_spares.extend(result.get("spare_parts") or [])
        all_trouble.extend(result.get("troubleshooting") or [])

    all_maint.sort(key=_page_sort_key)
    all_spares.sort(key=_page_sort_key)
    all_trouble.sort(key=_page_sort_key)
    for idx, row in enumerate(all_maint, start=1):
        row["id"] = idx
    for idx, row in enumerate(all_spares, start=1):
        row["id"] = idx
    for idx, row in enumerate(all_trouble, start=1):
        row["id"] = idx

    return ExtractResponse(
        maintenance=[MaintenanceRow(**row) for row in all_maint],
        spare_parts=[SparePartRow(**row) for row in all_spares],
        troubleshooting=[TroubleshootingRow(**row) for row in all_trouble],
        pages=pages_out,
        meta=ExtractMeta(
            filename=filename,
            engine=f"{options.engine}:{options.gemini_model if options.engine == 'gemini' else options.ollama_model}",
            parse_strategy=options.parse_strategy,
            pages_total=len(page_payloads),
            pages_processed=processed,
            maintenance_count=len(all_maint),
            spare_parts_count=len(all_spares),
            troubleshooting_count=len(all_trouble),
            warnings=warnings,
        ),
    )
