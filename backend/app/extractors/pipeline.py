from __future__ import annotations

import asyncio
import re
from typing import Any, Callable, Optional

from ..models import (
    ExtractMeta,
    ExtractOptions,
    ExtractResponse,
    MaintenanceRow,
    PageText,
    RowQuality,
    SparePartRow,
    TroubleshootingRow,
)
from ..pdf_utils import extract_image_page, extract_pdf_pages, extract_txt_chunks
from .gemini import run_gemini_extractor
from .ollama import run_ollama_extractor


def _ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _page_sort_key(row: dict[str, Any]) -> tuple:
    """PDF reading order: page, then pdf_order / Item No. — never part name."""
    page_raw = row.get("page", "")
    page_num = 10**9
    if str(page_raw).isdigit():
        page_num = int(page_raw)
    else:
        m = re.search(r"(\d{1,5})", str(page_raw or ""))
        if m:
            page_num = int(m.group(1))

    pdf_order_raw = row.get("pdf_order")
    try:
        pdf_order = int(pdf_order_raw)
        if pdf_order <= 0:
            pdf_order = 10**9
    except (TypeError, ValueError):
        pdf_order = 10**9

    item_raw = str(row.get("item_no") or "").strip()
    try:
        item_num = int(item_raw)
    except ValueError:
        m2 = re.match(r"^(\d{1,6})\b", item_raw)
        item_num = int(m2.group(1)) if m2 else 10**9

    return (page_num, pdf_order, item_num)


def _field_filled(val: Any) -> bool:
    s = str(val or "").strip()
    if not s:
        return False
    return s.upper() not in {"NA", "N/A", "NONE", "-", "NULL", "UNDEFINED"}


def _completeness_ratio(row: dict[str, Any], fields: list[str]) -> float:
    if not fields:
        return 1.0
    filled = sum(1 for f in fields if _field_filled(row.get(f)))
    return round(filled / len(fields), 3)


def compute_maintenance_completeness(row: dict[str, Any], *, is_logbook: bool) -> float:
    if is_logbook:
        # Required work description + optional attended_by / date
        required = 1.0 if _field_filled(row.get("maintenance_work_description")) else 0.0
        optional = [
            1.0 if _field_filled(row.get("attended_by")) else 0.0,
            1.0 if _field_filled(row.get("date")) else 0.0,
        ]
        return round((required * 0.7) + (sum(optional) / max(1, len(optional)) * 0.3), 3)
    return _completeness_ratio(
        row,
        ["equipment_title", "subsystem_component", "maintenance_routine", "checks_instructions"],
    )


def compute_spare_completeness(row: dict[str, Any]) -> float:
    title = 1.0 if _field_filled(row.get("equipment_title")) else 0.0
    name = 1.0 if _field_filled(row.get("part_name")) else 0.0
    has_id = any(
        _field_filled(row.get(f)) for f in ("part_number_code", "item_no", "drawing_model_no")
    )
    return round((title + name + (1.0 if has_id else 0.0)) / 3.0, 3)


def compute_troubleshooting_completeness(row: dict[str, Any]) -> float:
    return _completeness_ratio(
        row,
        ["equipment_title", "subsystem_component", "problem", "root_cause_solution"],
    )


def score_row_confidence(
    row: dict[str, Any],
    *,
    completeness: float,
) -> tuple[float, RowQuality]:
    grounding_available = bool(row.get("grounding_available", False))
    raw_g = row.get("grounding_score")
    try:
        grounding = float(raw_g) if raw_g is not None else 0.5
    except (TypeError, ValueError):
        grounding = 0.5
    grounding = max(0.0, min(1.0, grounding))

    if grounding_available:
        confidence = (0.5 * grounding) + (0.5 * completeness)
    else:
        # OCR / vision pages: grounding often unavailable — weight completeness.
        confidence = (0.2 * grounding) + (0.8 * completeness)

    confidence = round(max(0.0, min(1.0, confidence)), 3)
    quality = RowQuality(grounding_score=round(grounding, 3), completeness_score=completeness)
    return confidence, quality


def apply_row_scores(
    maintenance: list[dict[str, Any]],
    spare_parts: list[dict[str, Any]],
    troubleshooting: list[dict[str, Any]],
    *,
    is_logbook: bool,
) -> None:
    for row in maintenance:
        completeness = compute_maintenance_completeness(row, is_logbook=is_logbook)
        conf, quality = score_row_confidence(row, completeness=completeness)
        row["confidence"] = conf
        row["quality"] = quality
    for row in spare_parts:
        completeness = compute_spare_completeness(row)
        conf, quality = score_row_confidence(row, completeness=completeness)
        row["confidence"] = conf
        row["quality"] = quality
    for row in troubleshooting:
        completeness = compute_troubleshooting_completeness(row)
        conf, quality = score_row_confidence(row, completeness=completeness)
        row["confidence"] = conf
        row["quality"] = quality


def compute_run_quality_meta(
    maintenance: list[dict[str, Any]],
    spare_parts: list[dict[str, Any]],
    troubleshooting: list[dict[str, Any]],
    *,
    candidates_before: int,
    pages_processed: int,
) -> dict[str, Any]:
    all_rows = maintenance + spare_parts + troubleshooting
    total_valid = len(all_rows)
    candidates_before = max(0, int(candidates_before or 0))
    dropped = max(0, candidates_before - total_valid)
    filter_drop_rate = round(dropped / max(1, candidates_before), 3) if candidates_before else 0.0

    confidences = [float(r.get("confidence") or 0.0) for r in all_rows]
    mean_confidence = (sum(confidences) / len(confidences)) if confidences else 0.0
    low_confidence_count = sum(1 for c in confidences if c < 0.70)

    grounded_rows = [r for r in all_rows if r.get("grounding_available")]
    if grounded_rows:
        pass_count = 0
        for r in grounded_rows:
            quality = r.get("quality")
            if isinstance(quality, RowQuality):
                g_score = quality.grounding_score
            elif isinstance(quality, dict):
                g_score = float(quality.get("grounding_score") or 0.0)
            else:
                try:
                    g_score = float(r.get("grounding_score") or 0.0)
                except (TypeError, ValueError):
                    g_score = 0.0
            if g_score >= 0.70:
                pass_count += 1
        grounding_pass_rate = round(pass_count / len(grounded_rows), 3)
    else:
        grounding_pass_rate = 1.0

    pages_with_rows: set[int] = set()
    for r in all_rows:
        page = r.get("page")
        if str(page).isdigit():
            pages_with_rows.add(int(page))
    pages_processed = max(0, int(pages_processed or 0))
    pages_with_rows_ratio = (
        len(pages_with_rows) / pages_processed if pages_processed > 0 else (1.0 if total_valid else 0.0)
    )

    overall_score = round(
        (
            (mean_confidence * 0.55)
            + ((1.0 - filter_drop_rate) * 0.25)
            + (pages_with_rows_ratio * 0.20)
        )
        * 100,
        1,
    )

    return {
        "overall_score": overall_score,
        "grounding_pass_rate": grounding_pass_rate,
        "filter_drop_rate": filter_drop_rate,
        "low_confidence_count": low_confidence_count,
    }


def _row_for_model(row: dict[str, Any]) -> dict[str, Any]:
    """Copy row fields suitable for Pydantic models (drop internal scoring keys)."""
    out = {k: v for k, v in row.items() if not k.startswith("_") and k not in {"grounding_available", "grounding_score"}}
    quality = out.get("quality")
    if isinstance(quality, RowQuality):
        out["quality"] = quality
    return out


async def _extract_page(
    page_text: str,
    doc_name: str,
    page_num: int,
    options: ExtractOptions,
    *,
    image_b64: Optional[str] = None,
    mime_type: str = "image/jpeg",
) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "maintenance": [],
        "spare_parts": [],
        "troubleshooting": [],
        "_quality_stats": {"candidates_before": 0, "candidates_after": 0, "grounding_available": False},
    }
    if options.engine == "ollama":
        return (
            await run_ollama_extractor(
                page_text,
                doc_name,
                page_num,
                ollama_url=options.ollama_url,
                model=options.ollama_model,
                base64_image=image_b64,
                equipment_category=options.equipment_category,
                learned_patterns=options.learned_patterns,
            )
            or empty
        )
    return (
        await run_gemini_extractor(
            page_text,
            doc_name,
            page_num,
            api_key=options.gemini_api_key or "",
            model=options.gemini_model,
            base64_image=image_b64,
            mime_type=mime_type,
            equipment_category=options.equipment_category,
            learned_patterns=options.learned_patterns,
        )
        or empty
    )


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
    candidates_before_total = 0
    is_logbook = (options.equipment_category or "").strip() == "Logbook"

    if ext == "pdf":
        # History cards are scanned photos; always OCR + auto-rotate sideways pages.
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
                overall_score=0.0,
                grounding_pass_rate=0.0,
                filter_drop_rate=0.0,
                low_confidence_count=0,
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
                overall_score=0.0,
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
            return {
                "maintenance": [],
                "spare_parts": [],
                "troubleshooting": [],
                "_quality_stats": {
                    "candidates_before": 0,
                    "candidates_after": 0,
                    "grounding_available": False,
                },
            }
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
                result = {
                    "maintenance": [],
                    "spare_parts": [],
                    "troubleshooting": [],
                    "_quality_stats": {
                        "candidates_before": 0,
                        "candidates_after": 0,
                        "grounding_available": False,
                    },
                }
            processed += 1
            if on_progress:
                on_progress(f"Processed page {processed}/{total}", processed / total)
            return result

    results = await asyncio.gather(*[worker(p) for p in llm_payloads])
    for result in results:
        stats = result.get("_quality_stats") or {}
        candidates_before_total += int(stats.get("candidates_before") or 0)
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

    apply_row_scores(all_maint, all_spares, all_trouble, is_logbook=is_logbook)
    quality_meta = compute_run_quality_meta(
        all_maint,
        all_spares,
        all_trouble,
        candidates_before=candidates_before_total,
        pages_processed=processed,
    )

    return ExtractResponse(
        maintenance=[MaintenanceRow(**_row_for_model(row)) for row in all_maint],
        spare_parts=[SparePartRow(**_row_for_model(row)) for row in all_spares],
        troubleshooting=[TroubleshootingRow(**_row_for_model(row)) for row in all_trouble],
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
            overall_score=quality_meta["overall_score"],
            grounding_pass_rate=quality_meta["grounding_pass_rate"],
            filter_drop_rate=quality_meta["filter_drop_rate"],
            low_confidence_count=quality_meta["low_confidence_count"],
        ),
    )
