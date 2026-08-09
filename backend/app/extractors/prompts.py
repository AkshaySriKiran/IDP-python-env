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
Your task is to analyze the image or text below and extract historical maintenance log entries exactly as they are written.

These documents are often photographed HISTORY CARDs (Top Drive / Drawworks electrical, etc.).
Pages may be sideways or rotated — still read every handwritten table row you can see.

Group your extractions into the "maintenance" list. Return an empty array [] for "spare_parts" and "troubleshooting".
If a field is missing, not specified, or not available in the text, you MUST populate it with the string "NA".

You MUST strictly use the following 5 keys for every entry:
- "date"
- "maintenance_work_description"
- "parts_renewed"
- "attended_by"
- "remarks"

Response MUST be strictly valid JSON (and only JSON, with no other text before or after).
CRITICAL: Even if the page looks like a cover page, or the table is messy and handwritten, DO NOT return empty arrays unless the page truly has no log rows (title/cover only)! Extract handwritten notes, signatures, or dates into the "maintenance" list when present.

CRITICAL INSTRUCTION: DO NOT use the values from the example output. If a field is missing or not found in the text, you MUST output "NA".

Example Output Structure:
{{
  "maintenance": [
    {{
      "date": "15 Jan 2023",
      "maintenance_work_description": "Repl. Oil Pump",
      "parts_renewed": "Oil Pump Assy",
      "attended_by": "J. P. H.",
      "remarks": "Tested OK"
    }}
  ],
  "spare_parts": [],
  "troubleshooting": []
}}
"""
    else:
        prompt = f"""You are an expert technical parser of industrial engineering manuals.
Your task is to analyze the text page content below and extract:
1. Maintenance routines, checks, and instructions.
2. Spare parts and components referenced in drawings or lists.
3. Troubleshooting tables, problems, and root-cause/solutions.

Group your extractions into three distinct JSON lists: "maintenance", "spare_parts", and "troubleshooting".
CRITICAL INSTRUCTION: If a field is missing, not specified, or not available in the text, you MUST populate it with the string "NA". Do not use null, undefined, or empty values.
"""
        prompt += """
DENSE TABLE / CATALOG RULES (CRITICAL — apply on every page):
- Read EVERY filled row in spare-parts tables. Do not sample, summarize, or stop after a few examples.
- Include dense electrical/mechanical rows (fuses, breakers, relays, fans, kits, contactors, etc.).
- Map: Description → part_name; NOV Part No / Part No / Code → part_number_code; Item/Ref No → drawing_model_no; Item No → item_no.
- Cubicle/section headers (e.g. INCOMER CUBICLE, DRIVE CUBICLE) go into subsystem_location for following rows.
- Commissioning / recommended quantities can go into quantity / recommended_stock_qty when present.
- Prefer completeness over brevity. Output as many spare_parts objects as there are real table rows on this page.
"""
        if is_ocr_vision:
            prompt += """
OCR / SCANNED PAGE RULES (CRITICAL):
- The page image is authoritative when native text is sparse, blank, or garbled.
- Read every visible row from RSPL / BOM / parts-list drawings in the image.
"""
        prompt += f"""
Rules for "maintenance" tasks:
- Extract real maintenance tasks, checks, inspection routines, adjustments, or replacements.
- Clean instructions to remove page headers or random numbers. Pay special attention to tables and bulleted checklists, ensuring each item is extracted accurately.
- For "equipment_title", default to "{clean_doc_name}" if the text does not mention a specific equipment.
- For "subsystem_component", you MUST identify a specific, physical sub-system or component. If a checklist implies the component, use that for all its items. If no specific component can be identified, DO NOT extract the task.
- For "maintenance_routine", extract the interval.
- For "checks_instructions", write the procedure or actions in a concise manner.

Rules for "spare_parts":
- Extract items that represent real spare parts, consumables, hardware, or components.
- CRITICAL for catalog / bill-of-materials tables (especially pages with an assembly diagram + No./Code/Name/Qty table): extract EVERY numbered row on the page. Do not sample, summarize, or stop after a few examples.
- Emit spare_parts objects in the same top-to-bottom order as the PDF table (Item 1, then 2, then 3, …). Never alphabetize by part name.
- If the table continues from a previous page (item numbers resume at 15, 16, …), still extract every remaining row.
- DO NOT extract ordering metadata, procurement fields, or identification labels as parts.
- Reject list labels or ordering metadata unless there is clear evidence of an actual physical part (for example a concrete component name with valid part/drawing reference context).
- For "equipment_title", use the document equipment name (default "{clean_doc_name}"), not a section number like "5.2.1 …".
- For "subsystem_location", identify the specific assembly or sub-system the part belongs to. If the table title explicitly mentions the assembly name (e.g. "Adjusting screw assembly", "Spray Module"), use it here.
- For "part_name", extract the descriptive name of the component or part.
- For "part_categorization", use "Critical Spare", "Consumable", or "Standard Part".
- For "quantity", extract the number of units.
- For "part_number_code": The manufacturer's part number or code. This is often an alphanumeric string (e.g. "H910-416", "30123290", "BC100113-0704"), not necessarily a long numeric code. Scan the entire row/segment for it, including columns labeled "P/N", "Part No.", "Code", "Figure No.", "Number", or similar.
- For "drawing_model_no": The engineering drawing, reference/location designator (e.g. "U1", "TB2"), or model designator number, if present in the row.
- For "oem_standard_body": The OEM name, manufacturer, or governing standard/body (e.g. "ANSI", "ISO", "DIN") referenced for the part, if present.
- For "recommended_stock_qty", extract stock recommendation levels if present.
- For "warranty_period", extract the warranty duration if mentioned (e.g. "12 months", "1 year").
- For "frequency_of_use", extract how frequently this part is used or should be replaced/inspected.
- IMPORTANT: Every field above must be actively searched for within the row's full text before defaulting to "NA". Only use "NA" when the information is truly absent from that row, not simply because it doesn't fit the example format below.

Rules for "troubleshooting" tasks:
- Extract troubleshooting matrices/tables AND fault-diagnosis sections that pair a symptom/problem with cause and remedy.
- DO NOT extract Table of Contents headers, HMI button/label descriptions, or normal operating paragraphs as problems.
- A valid problem MUST include how to diagnose or fix it (root cause, remedy, elimination method, or numbered inspection steps). If only a symptom is mentioned with no fix path, skip it.
- Alarm UI legends like "gray = normal, red = fault" are NOT troubleshooting rows unless a specific fault and corrective action are stated.
- For "equipment_title", default to "{clean_doc_name}" if not specified.
- For "subsystem_component", identify the specific sub-system.
- For "problem", extract the symptom, fault, or issue described.
- For "root_cause_solution", extract the combined root cause and solution / elimination method.

Response MUST be strictly valid JSON (and only JSON, with no other text before or after).
CRITICAL EXCEPTION: Do NOT return empty arrays if you see actual part names accompanied by alphanumeric codes. You MUST extract them.

CRITICAL INSTRUCTION: DO NOT use the values from the example output. If a field is missing or not found in the text, you MUST output "NA".

Example Output Structure:
{{
  "maintenance": [
    {{
      "equipment_title": "EXAMPLE_EQUIPMENT_DO_NOT_COPY",
      "subsystem_component": "Main Brake Caliper",
      "maintenance_routine": "Daily",
      "checks_instructions": "Inspect for oil leaks."
    }}
  ],
  "spare_parts": [
    {{
      "equipment_title": "EXAMPLE_EQUIPMENT_DO_NOT_COPY",
      "subsystem_location": "Regulator",
      "item_no": "1",
      "part_name": "EXAMPLE_PART_NAME_DO_NOT_COPY",
      "part_number_code": "EXAMPLE_CODE",
      "drawing_model_no": "EXAMPLE_DRAWING_OR_REF_DO_NOT_COPY",
      "oem_standard_body": "EXAMPLE_OEM_OR_STANDARD_DO_NOT_COPY",
      "part_categorization": "Consumable",
      "quantity": "1",
      "recommended_stock_qty": "EXAMPLE_STOCK_QTY_DO_NOT_COPY",
      "warranty_period": "EXAMPLE_WARRANTY_DO_NOT_COPY",
      "frequency_of_use": "EXAMPLE_FREQUENCY_DO_NOT_COPY"
    }}
  ],
  "troubleshooting": [
    {{
      "equipment_title": "EXAMPLE_EQUIPMENT_DO_NOT_COPY",
      "subsystem_component": "Regulator Valve",
      "problem": "Valve does not open",
      "root_cause_solution": "Air lock in line. Bleed air from the system."
    }}
  ]
}}
"""

    patterns = learned_patterns or []
    if patterns:
        prompt += (
            "\n\nCRITICAL LEARNING EXAMPLES:\n"
            "The user has manually corrected past extractions. "
            "You MUST strongly weigh these learned patterns when deciding how to extract and format data:\n"
            f"{json.dumps(patterns, indent=2)}"
        )

    prompt += f'\n\nText to parse:\n"""\n{text}\n"""'
    return prompt
