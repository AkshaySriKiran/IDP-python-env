from __future__ import annotations

import asyncio
import re
from typing import Any, Callable, Optional

from ..models import (
    DocumentMetadata,
    ExtractMeta,
    ExtractOptions,
    ExtractResponse,
    MaintenanceRow,
    PageText,
    RowQuality,
    SparePartRow,
    TroubleshootingRow,
)
from ..pdf_utils import convert_docx_to_pdf, extract_image_page, extract_pdf_pages, extract_txt_chunks
from .gemini import run_gemini_extractor
from .ollama import run_ollama_extractor


def _ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _page_sort_key(row: dict[str, Any]) -> tuple:
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


def _missing_fields(row: dict[str, Any], fields: list[str]) -> list[str]:
    return [f for f in fields if not _field_filled(row.get(f))]


def build_quality_reasons(
    row: dict[str, Any],
    *,
    completeness: float,
    grounding: float,
    grounding_available: bool,
    registry: str,
    is_logbook: bool,
) -> list[str]:
    reasons: list[str] = []

    if completeness >= 0.85:
        reasons.append("Completeness OK")
    elif completeness >= 0.5:
        reasons.append("Some required fields incomplete")
    else:
        reasons.append("Many required fields missing")

    if registry == "maintenance":
        if is_logbook:
            missing = _missing_fields(row, ["maintenance_work_description", "attended_by", "date"])
        else:
            missing = _missing_fields(
                row,
                ["equipment_title", "subsystem_component", "maintenance_routine", "checks_instructions"],
            )
    elif registry == "spare_parts":
        missing = _missing_fields(row, ["equipment_title", "part_name"])
        if not any(_field_filled(row.get(f)) for f in ("part_number_code", "item_no", "drawing_model_no")):
            missing.append("part_number_or_drawing")
    else:
        missing = _missing_fields(
            row,
            ["equipment_title", "subsystem_component", "problem", "root_cause_solution"],
        )

    for field in missing[:4]:
        label = field.replace("_", " ")
        reasons.append(f"Missing: {label}")

    if not grounding_available:
        reasons.append("OCR page — confirm in PDF (letter-match not available)")
    elif grounding >= 0.70:
        reasons.append("Grounded in source page")
    elif grounding >= 0.40:
        reasons.append("Weak match to source page")
    else:
        reasons.append("Poor match to source page")

    missing_words = [str(w).strip() for w in (row.get("_missing_from_page") or []) if str(w).strip()]
    if grounding_available and missing_words:
        reasons.append("AI words missing from page: " + ", ".join(missing_words[:10]))

    seen: set[str] = set()
    out: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def score_row_confidence(
    row: dict[str, Any],
    *,
    completeness: float,
    registry: str = "maintenance",
    is_logbook: bool = False,
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
        confidence = completeness

    confidence = round(max(0.0, min(1.0, confidence)), 3)
    reasons = build_quality_reasons(
        row,
        completeness=completeness,
        grounding=grounding,
        grounding_available=grounding_available,
        registry=registry,
        is_logbook=is_logbook,
    )
    quality = RowQuality(
        grounding_score=round(grounding, 3),
        completeness_score=completeness,
        grounding_available=grounding_available,
        reasons=reasons,
    )
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
        conf, quality = score_row_confidence(
            row, completeness=completeness, registry="maintenance", is_logbook=is_logbook
        )
        row["confidence"] = conf
        row["quality"] = quality
    for row in spare_parts:
        completeness = compute_spare_completeness(row)
        conf, quality = score_row_confidence(
            row, completeness=completeness, registry="spare_parts", is_logbook=False
        )
        row["confidence"] = conf
        row["quality"] = quality
    for row in troubleshooting:
        completeness = compute_troubleshooting_completeness(row)
        conf, quality = score_row_confidence(
            row, completeness=completeness, registry="troubleshooting", is_logbook=False
        )
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

    if ext in {"pdf", "docx", "doc"}:
        pdf_bytes = file_bytes
        if ext in {"docx", "doc"}:
            pdf_bytes = await asyncio.to_thread(convert_docx_to_pdf, file_bytes)

        effective_strategy = "ocr" if is_logbook else options.parse_strategy
        page_payloads = await asyncio.to_thread(
            extract_pdf_pages,
            pdf_bytes,
            parse_strategy=effective_strategy,
            page_start=options.page_start,
            page_end=options.page_end,
            ocr_auto_rotate=is_logbook,
        )
        from ..pdf_utils import MAX_PDF_PAGES

        if len(page_payloads) >= MAX_PDF_PAGES and not options.page_end:
            warnings.append(
                f"Page processing bounded at {MAX_PDF_PAGES} pages. Processed {len(page_payloads)} page(s)."
            )
    elif ext == "txt":
        text = file_bytes.decode("utf-8", errors="replace")
        page_payloads = extract_txt_chunks(text)
    elif ext in {"jpg", "jpeg", "png"}:
        page_payloads = [extract_image_page(file_bytes, filename)]
    else:
        raise ValueError(
            f"Unsupported file type: .{ext or 'unknown'}. "
            "Supported formats: PDF, Word (.docx, .doc), TXT, JPG, PNG."
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
                warnings=["No extractable content found in document."],
                overall_score=0.0,
            ),
        )

    for p in page_payloads:
        text = p.text or ""
        if len(text) > 8000:
            text = text[:8000] + "…"
        pages_out.append(PageText(pageNum=p.page_num, text=text))

    llm_payloads = list(page_payloads)
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
                "_page_num": payload.page_num,
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
            except Exception as err:
                warnings.append(f"Page {payload.page_num}: {err}")
                result = {
                    "maintenance": [],
                    "spare_parts": [],
                    "troubleshooting": [],
                    "_page_num": payload.page_num,
                    "_quality_stats": {
                        "candidates_before": 0,
                        "candidates_after": 0,
                        "grounding_available": False,
                    },
                }
            if isinstance(result, dict):
                result.setdefault("_page_num", payload.page_num)
            processed += 1
            if on_progress:
                on_progress(f"Processed page {processed}/{total}", processed / total)
            return result

    results = await asyncio.gather(*[worker(p) for p in llm_payloads])
    page_text_by_num = {p.pageNum: (p.text or "") for p in pages_out}
    for result in results:
        stats = result.get("_quality_stats") or {}
        candidates_before_total += int(stats.get("candidates_before") or 0)
        all_maint.extend(result.get("maintenance") or [])
        all_spares.extend(result.get("spare_parts") or [])
        all_trouble.extend(result.get("troubleshooting") or [])

        page_num = result.get("_page_num")
        transcription = (result.get("_page_transcription") or "").strip()
        grounding_source = (result.get("_grounding_source") or "").strip()
        if page_num is None:
            continue
        merged = grounding_source or transcription
        if not merged:
            continue
        existing = page_text_by_num.get(int(page_num), "")
        existing_clean = re.sub(r"OCR\s*VISION\s*EXTRACTION", " ", existing or "", flags=re.I).strip()
        if existing_clean and existing_clean not in merged:
            merged = f"{existing_clean}\n{merged}".strip()
        if len(merged) > 8000:
            merged = merged[:8000] + "…"
        page_text_by_num[int(page_num)] = merged

    pages_out = [
        PageText(pageNum=p.pageNum, text=page_text_by_num.get(p.pageNum, p.text or ""))
        for p in pages_out
    ]

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

    aggregated_doc_meta = {
        "title": "NA",
        "oem_manufacturer": "NA",
        "equipment_model": "NA",
        "equipment_type": "NA",
        "document_version": "NA",
        "publication_date": "NA",
    }
    clean_doc_name = filename.rsplit(".", 1)[0] if "." in filename else filename
    aggregated_doc_meta["title"] = clean_doc_name

    for result in results:
        meta_cand = result.get("_doc_metadata")
        if isinstance(meta_cand, dict):
            for k in aggregated_doc_meta:
                v = str(meta_cand.get(k) or "").strip()
                if v and v.upper() not in {"NA", "N/A", "NONE", "NULL", "UNDEFINED"}:
                    if aggregated_doc_meta[k] in {"NA", clean_doc_name} or len(v) > len(aggregated_doc_meta[k]):
                        aggregated_doc_meta[k] = v

    doc_metadata_obj = DocumentMetadata(**aggregated_doc_meta)

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
            doc_metadata=doc_metadata_obj,
            document_status="Pending Review",
        ),
    )
