from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .catalog_tables import extract_catalog_spare_rows, merge_spare_rows


def sanitize_val(val: Any) -> str:
    if val is None:
        return "NA"
    s = str(val).strip()
    if not s or s.lower() in {"null", "undefined", "na"}:
        return "NA"
    return s


@lru_cache
def _load_manifest() -> dict[str, Any]:
    # Prefer repo-root equipment_manifest.json (../.. from this file → backend/, then parent)
    candidates = [
        Path(__file__).resolve().parents[3] / "equipment_manifest.json",
        Path(__file__).resolve().parents[2] / "equipment_manifest.json",
    ]
    for path in candidates:
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                return {}
    return {}


def normalize_extraction(output: dict[str, Any]) -> dict[str, Any]:
    manifest = _load_manifest()
    mappings = (manifest or {}).get("normalization_mappings") or {}
    if not mappings:
        return output

    def normalize_routine(routine: str) -> str:
        if not routine or routine == "NA":
            return "NA"
        lower = str(routine).lower()
        for mapping in mappings.get("maintenance_routines") or []:
            if any(m in lower for m in mapping.get("matches") or []):
                return mapping.get("enum") or routine
        return routine

    def normalize_freq(freq: str) -> str:
        if not freq or freq == "NA":
            return "NA"
        lower = str(freq).lower()
        for mapping in mappings.get("spare_parts_frequency") or []:
            if any(m in lower for m in mapping.get("matches") or []):
                return mapping.get("enum") or freq
        return freq

    for row in output.get("maintenance") or []:
        if "maintenance_routine" in row:
            row["maintenance_routine"] = normalize_routine(row.get("maintenance_routine") or "NA")
    for row in output.get("spare_parts") or []:
        if "frequency_of_use" in row:
            row["frequency_of_use"] = normalize_freq(row.get("frequency_of_use") or "NA")
    return output


def looks_like_procurement_or_index_meta(text: str) -> bool:
    s = str(text or "").lower().strip()
    if not s:
        return False
    meta_token_hits = len(
        re.findall(
            r"\b(project|order|serial|manufactur|nameplate|code|index|material|required|identification|reference)\b",
            s,
        )
    )
    part_token_hits = len(
        re.findall(
            r"\b(gasket|seal|bearing|plate|bolt|nut|screw|filter|valve|ring|liner|pump|shaft|gear|coupling|hose)\b",
            s,
        )
    )
    has_action_verb = bool(
        re.search(r"\b(inspect|check|replace|clean|lubricate|tighten|remove|install|test|flush)\b", s)
    )
    ends_with_page_num = bool(re.search(r"(?:\.{2,}\s*)?\d{1,3}$", s))
    if meta_token_hits >= 2 and part_token_hits == 0 and not has_action_verb:
        return True
    if meta_token_hits >= 3 and not has_action_verb:
        return True
    if ends_with_page_num and meta_token_hits >= 1 and not has_action_verb:
        return True
    return False


def extract_content_tokens(text: str) -> list[str]:
    stop = {
        "the", "and", "for", "with", "from", "into", "that", "this", "then", "than",
        "are", "was", "were", "have", "has", "had", "will", "shall", "should", "can",
        "must", "not", "all", "any", "page", "unit", "system", "check", "inspect",
    }
    tokens = re.sub(r"[^a-z0-9\s]", " ", str(text or "").lower()).split()
    out: list[str] = []
    seen: set[str] = set()
    for t in tokens:
        if not t or t in stop:
            continue
        if len(t) < 4 and not t.isdigit():
            continue
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def grounding_ratio(candidate_text: str, source_text: str) -> float:
    """Continuous 0–1 grounding score from token overlap + phrase match."""
    source = str(source_text or "").lower()
    if not source.strip():
        return 0.0
    tokens = extract_content_tokens(candidate_text)
    if not tokens:
        return 0.0

    matched = [t for t in tokens if t in source]
    token_ratio = len(matched) / max(1, len(tokens))

    words = [w for w in re.sub(r"[^a-z0-9\s]", " ", str(candidate_text or "").lower()).split() if len(w) >= 3]
    phrase_ok = False
    if len(words) >= 3:
        for i in range(0, len(words) - 2):
            trigram = f"{words[i]} {words[i + 1]} {words[i + 2]}".strip()
            if len(trigram) >= 10 and trigram in source:
                phrase_ok = True
                break
    if not phrase_ok and len(words) >= 2:
        for i in range(0, len(words) - 1):
            bigram = f"{words[i]} {words[i + 1]}".strip()
            if len(bigram) >= 12 and bigram in source:
                phrase_ok = True
                break

    # Phrase match is required for hard pass; fold it into a continuous score.
    phrase_component = 1.0 if phrase_ok else 0.0
    score = (token_ratio * 0.65) + (phrase_component * 0.35)
    return round(max(0.0, min(1.0, score)), 3)


def is_text_grounded_in_source(candidate_text: str, source_text: str) -> bool:
    """Boolean filter using the same thresholds as before (token + phrase)."""
    source = str(source_text or "").lower()
    if not source.strip():
        return False
    tokens = extract_content_tokens(candidate_text)
    if not tokens:
        return False

    matched = [t for t in tokens if t in source]
    is_short = len(tokens) <= 8
    threshold = max(3, int(len(tokens) * 0.7 + 0.999)) if is_short else max(2, int(len(tokens) * 0.5 + 0.999))
    token_ok = len(matched) >= threshold

    words = [w for w in re.sub(r"[^a-z0-9\s]", " ", str(candidate_text or "").lower()).split() if len(w) >= 3]
    phrase_ok = False
    if len(words) >= 3:
        for i in range(0, len(words) - 2):
            trigram = f"{words[i]} {words[i + 1]} {words[i + 2]}".strip()
            if len(trigram) >= 10 and trigram in source:
                phrase_ok = True
                break
    if not phrase_ok and len(words) >= 2:
        for i in range(0, len(words) - 1):
            bigram = f"{words[i]} {words[i + 1]}".strip()
            if len(bigram) >= 12 and bigram in source:
                phrase_ok = True
                break
    return token_ok and phrase_ok


def _maintenance_grounding_text(row: dict[str, Any], *, equipment_category: str) -> str:
    if equipment_category == "Logbook":
        return str(row.get("maintenance_work_description") or "")
    return str(row.get("checks_instructions") or "")


def _spare_grounding_text(row: dict[str, Any]) -> str:
    return f"{row.get('part_name')} {row.get('part_number_code')} {row.get('drawing_model_no')}"


def _trouble_grounding_text(row: dict[str, Any]) -> str:
    return f"{row.get('problem')} {row.get('root_cause_solution')}"


def _attach_grounding_unavailable(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        row["grounding_score"] = 0.5
        row["grounding_available"] = False


def _filter_grounded_with_scores(
    rows: list[dict[str, Any]],
    *,
    source: str,
    text_fn,
) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for row in rows:
        candidate = text_fn(row)
        ratio = grounding_ratio(candidate, source)
        if is_text_grounded_in_source(candidate, source):
            row["grounding_score"] = ratio
            row["grounding_available"] = True
            kept.append(row)
    return kept


def is_clean_maintenance_row(row: dict[str, Any], *, equipment_category: str) -> bool:
    if equipment_category == "Logbook":
        return sanitize_val(row.get("maintenance_work_description")) != "NA"
    comp = sanitize_val(row.get("subsystem_component"))
    if comp == "NA":
        return False
    checks = sanitize_val(row.get("checks_instructions"))
    if checks == "NA":
        return False
    if looks_like_procurement_or_index_meta(checks):
        return False
    return True


def is_clean_spare_parts_row(row: dict[str, Any]) -> bool:
    name = sanitize_val(row.get("part_name"))
    code = sanitize_val(row.get("part_number_code"))
    dwg = sanitize_val(row.get("drawing_model_no"))
    if name == "NA" and code == "NA" and dwg == "NA":
        return False
    lower_code = code.lower()
    lower_dwg = dwg.lower()
    has_strong_code = code != "NA" and any(ch.isdigit() for ch in code) and "na" not in lower_code
    has_drawing_ref = dwg != "NA" and "na" not in lower_dwg
    if looks_like_procurement_or_index_meta(name) and not has_strong_code and not has_drawing_ref:
        return False
    return True


def extract_first_json_object(raw_text: str) -> str | None:
    text = str(raw_text or "").strip()
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.I)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]


def repair_truncated_json(raw_json: str) -> str:
    s = str(raw_json or "").strip()
    if not s:
        return s
    s = s.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    s = re.sub(r',\s*"[^"\n]*$', "", s)
    s = re.sub(r",\s*\{[\s\S]*$", "", s)
    s = re.sub(r':\s*"[^"\n]*$', ': "NA"', s)
    s = re.sub(r":\s*-?\d+(\.\d+)?\s*$", ": 0", s)
    s = re.sub(r",\s*$", "", s)
    s = re.sub(r",\s*([\]}])", r"\1", s)

    in_string = False
    escaping = False
    stack: list[str] = []
    for ch in s:
        if in_string:
            if escaping:
                escaping = False
            elif ch == "\\":
                escaping = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in {"}", "]"} and stack and stack[-1] == ch:
            stack.pop()

    if in_string:
        s += '"'
    s = re.sub(r':\s*"$', ': "NA"', s)
    s = re.sub(r",\s*$", "", s)
    s = re.sub(r",\s*([\]}])", r"\1", s)
    while stack:
        s += stack.pop()
    return s


def parse_model_json_response(raw_response_text: str) -> dict[str, Any]:
    clean = str(raw_response_text or "").strip()
    candidates: list[str] = []
    first = extract_first_json_object(clean)
    if first:
        candidates.append(first)
    if clean and clean not in candidates:
        candidates.append(clean)
    repaired: list[str] = []
    for c in list(candidates):
        fixed = repair_truncated_json(c)
        if fixed and fixed not in candidates and fixed not in repaired:
            repaired.append(fixed)
    candidates.extend(repaired)

    last_err: Exception | None = None
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception as err:  # noqa: BLE001
            last_err = err
    raise last_err or ValueError("Unable to parse model JSON response")


def process_raw_model_response(
    raw_response_text: str,
    doc_name: str,
    page_num: int,
    *,
    has_image: bool,
    source_text: str = "",
    equipment_category: str = "Default",
) -> dict[str, list[dict[str, Any]]]:
    clean_doc_name = doc_name.rsplit(".", 1)[0] if doc_name and "." in doc_name else (doc_name or "NA")
    result_json = parse_model_json_response(raw_response_text)
    output: dict[str, list[dict[str, Any]]] = {
        "maintenance": [],
        "spare_parts": [],
        "troubleshooting": [],
    }

    if isinstance(result_json.get("maintenance"), list):
        for item in result_json["maintenance"]:
            if not isinstance(item, dict):
                continue
            if equipment_category == "Logbook":
                output["maintenance"].append(
                    {
                        "id": 0,
                        "date": sanitize_val(item.get("date")),
                        "maintenance_work_description": sanitize_val(item.get("maintenance_work_description")),
                        "parts_renewed": sanitize_val(item.get("parts_renewed")),
                        "attended_by": sanitize_val(item.get("attended_by")),
                        "remarks": sanitize_val(item.get("remarks")),
                        "equipment_title": "NA",
                        "subsystem_component": "NA",
                        "maintenance_routine": "NA",
                        "checks_instructions": "NA",
                        "page": page_num,
                        "pdf_order": len(output["maintenance"]) + 1,
                    }
                )
            else:
                title = sanitize_val(item.get("equipment_title"))
                if title == "NA":
                    title = clean_doc_name
                output["maintenance"].append(
                    {
                        "id": 0,
                        "equipment_title": title,
                        "subsystem_component": sanitize_val(item.get("subsystem_component")),
                        "maintenance_routine": sanitize_val(item.get("maintenance_routine")),
                        "checks_instructions": sanitize_val(item.get("checks_instructions")),
                        "date": "NA",
                        "maintenance_work_description": "NA",
                        "parts_renewed": "NA",
                        "attended_by": "NA",
                        "remarks": "NA",
                        "page": page_num,
                        "pdf_order": len(output["maintenance"]) + 1,
                    }
                )

    if isinstance(result_json.get("spare_parts"), list):
        for item in result_json["spare_parts"]:
            if not isinstance(item, dict):
                continue
            title = sanitize_val(item.get("equipment_title"))
            if title == "NA":
                title = clean_doc_name
            freq = sanitize_val(item.get("frequency_of_use"))
            if freq == "NA" and item.get("periodic_use"):
                freq = sanitize_val(item.get("periodic_use"))
            output["spare_parts"].append(
                {
                    "id": 0,
                    "equipment_title": title,
                    "subsystem_location": sanitize_val(item.get("subsystem_location")),
                    "item_no": sanitize_val(item.get("item_no")),
                    "part_name": sanitize_val(item.get("part_name")),
                    "part_number_code": sanitize_val(item.get("part_number_code")),
                    "drawing_model_no": sanitize_val(item.get("drawing_model_no")),
                    "oem_standard_body": sanitize_val(item.get("oem_standard_body")),
                    "part_categorization": sanitize_val(item.get("part_categorization")),
                    "quantity": sanitize_val(item.get("quantity")),
                    "recommended_stock_qty": sanitize_val(item.get("recommended_stock_qty")),
                    "warranty_period": sanitize_val(item.get("warranty_period")),
                    "frequency_of_use": freq,
                    "page": page_num,
                    "pdf_order": len(output["spare_parts"]) + 1,
                }
            )

    if isinstance(result_json.get("troubleshooting"), list):
        for item in result_json["troubleshooting"]:
            if not isinstance(item, dict):
                continue
            title = sanitize_val(item.get("equipment_title"))
            if title == "NA":
                title = clean_doc_name
            output["troubleshooting"].append(
                {
                    "id": 0,
                    "equipment_title": title,
                    "subsystem_component": sanitize_val(item.get("subsystem_component")),
                    "problem": sanitize_val(item.get("problem")),
                    "root_cause_solution": sanitize_val(item.get("root_cause_solution")),
                    "page": page_num,
                    "pdf_order": len(output["troubleshooting"]) + 1,
                }
            )

    candidates_before = (
        len(output["maintenance"]) + len(output["spare_parts"]) + len(output["troubleshooting"])
    )

    output["maintenance"] = [
        r for r in output["maintenance"] if is_clean_maintenance_row(r, equipment_category=equipment_category)
    ]
    output["spare_parts"] = [r for r in output["spare_parts"] if is_clean_spare_parts_row(r)]
    output["troubleshooting"] = [
        r
        for r in output["troubleshooting"]
        if r.get("problem") != "NA"
        and r.get("root_cause_solution") != "NA"
        and len(str(r.get("problem") or "")) > 5
        and len(str(r.get("root_cause_solution") or "")) > 5
        and "..." not in str(r.get("problem") or "").lower()
        and ". . ." not in str(r.get("problem") or "").lower()
    ]

    grounding_available = False
    if not has_image:
        source = str(source_text or "")
        if source.strip():
            grounding_available = True
            output["maintenance"] = _filter_grounded_with_scores(
                output["maintenance"],
                source=source,
                text_fn=lambda r: _maintenance_grounding_text(r, equipment_category=equipment_category),
            )
            output["spare_parts"] = _filter_grounded_with_scores(
                output["spare_parts"],
                source=source,
                text_fn=_spare_grounding_text,
            )
            output["troubleshooting"] = _filter_grounded_with_scores(
                output["troubleshooting"],
                source=source,
                text_fn=_trouble_grounding_text,
            )
        else:
            _attach_grounding_unavailable(output["maintenance"])
            _attach_grounding_unavailable(output["spare_parts"])
            _attach_grounding_unavailable(output["troubleshooting"])
    else:
        _attach_grounding_unavailable(output["maintenance"])
        _attach_grounding_unavailable(output["spare_parts"])
        _attach_grounding_unavailable(output["troubleshooting"])

    # Dense OEM catalog tables beside diagrams are often under-sampled by the LLM.
    # Deterministically recover every No./Code/Name/Qty row from page text.
    if equipment_category != "Logbook" and str(source_text or "").strip():
        catalog_rows = extract_catalog_spare_rows(
            source_text,
            page_num=page_num,
            doc_name=clean_doc_name,
        )
        if catalog_rows:
            before_spares = len(output["spare_parts"])
            output["spare_parts"] = merge_spare_rows(output["spare_parts"], catalog_rows)
            # Count newly recovered catalog rows toward quality stats.
            candidates_before += max(0, len(output["spare_parts"]) - before_spares)

    candidates_after = (
        len(output["maintenance"]) + len(output["spare_parts"]) + len(output["troubleshooting"])
    )
    # Stamp stable within-page order for any rows still missing pdf_order.
    for i, row in enumerate(output["maintenance"], start=1):
        if not row.get("pdf_order"):
            row["pdf_order"] = i
    for i, row in enumerate(output["spare_parts"], start=1):
        if not row.get("pdf_order"):
            row["pdf_order"] = i
    for i, row in enumerate(output["troubleshooting"], start=1):
        if not row.get("pdf_order"):
            row["pdf_order"] = i

    output["_quality_stats"] = {
        "candidates_before": candidates_before,
        "candidates_after": candidates_after,
        "grounding_available": grounding_available,
    }

    return output
