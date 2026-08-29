from __future__ import annotations

import json
from typing import Any


def build_extraction_prompt(
    text: str,
    doc_name: str = "",
    *,
    equipment_category: str = "Default",
    learned_patterns: list[dict[str, Any]] | None = None,
) -> str:
    clean_doc_name = doc_name.rsplit(".", 1)[0] if doc_name and "." in doc_name else (doc_name or "NA")
    is_ocr_vision = "OCR VISION EXTRACTION" in str(text or "").upper()

    if equipment_category == "Logbook":
        prompt = f"""You are an expert transcriber of handwritten field history cards and maintenance logbooks.
Your task is to analyze the image or text below and extract historical maintenance log entries exactly as written.

Pages may be rotated or handwritten — extract every legible maintenance table row.
Group extractions into the "maintenance" list. Return empty arrays [] for "spare_parts" and "troubleshooting".
If a field is missing, populate it with "NA".

Fields required for each maintenance entry:
- "date"
- "maintenance_work_description"
- "parts_renewed"
- "attended_by"
- "remarks"

Return strictly valid JSON with top-level keys: "page_transcription", "maintenance", "spare_parts", "troubleshooting".
"""
    else:
        prompt = f"""You are an expert technical parser of industrial engineering manuals.
Extract the following information from the provided document page:
1. Maintenance routines, intervals, checks, and procedures.
2. Spare parts lists, BOMs, part codes, drawing references, and stock quantities.
3. Troubleshooting tables, symptoms, and root cause / corrective actions.

Group your output into three lists: "maintenance", "spare_parts", and "troubleshooting".
If any specific field is missing or unavailable, use "NA" (do not use null or undefined).
"""
        prompt += """
Catalog & Table Rules:
- Extract EVERY valid row from spare parts tables and RSPL sheets. Do not summarize or truncate.
- Map: Description -> part_name; Part No/Code -> part_number_code; Item/Ref -> drawing_model_no; Item No -> item_no.
- For equipment_title default to: """ + f'"{clean_doc_name}"' + """.
- For subsystem_location, identify the specific sub-assembly.
- For part_categorization, use "Critical Spare", "Consumable", or "Standard Part".
- Preserve reading order from the original document page.

Document Header Metadata (extract if present on this page, otherwise use "NA"):
- Also return optional "doc_metadata" object with:
  "title": Document / Manual Title (or "NA"),
  "oem_manufacturer": OEM / Manufacturer name (or "NA"),
  "equipment_model": Equipment Model / Series number (or "NA"),
  "equipment_type": Equipment classification / category (or "NA"),
  "document_version": Manual revision or version (or "NA"),
  "publication_date": Publication / revision date (or "NA")
"""
        if is_ocr_vision:
            prompt += """
- The page image is authoritative for OCR visual extraction.
- Include a literal "page_transcription" string capturing all visible table cells and identifiers.
"""

    patterns = learned_patterns or []
    if patterns:
        prompt += (
            "\n\nLearned User Patterns:\n"
            f"{json.dumps(patterns, indent=2)}\n"
        )

    prompt += f'\nDocument Text to parse:\n"""\n{text}\n"""'
    return prompt

