from __future__ import annotations

import re
from typing import Any

_HEADER_RE = re.compile(
    r"^(?:no\.?|code|name|qty\.?|quantity|remarks|figure\s*no\.?)$",
    re.I,
)
_ASSEMBLY_RE = re.compile(
    r"^(?:catalogue|catalog|content|contents)\s*\d*\s*(.+)$",
    re.I,
)
_ITEM_NO_RE = re.compile(r"^\d{1,3}$")
_QTY_RE = re.compile(r"^\d{1,4}(?:\s*[x×]\s*\d{1,4})?$")
_CODE_RE = re.compile(
    r"^(?=.*[A-Za-z0-9])[A-Za-z0-9][A-Za-z0-9._/\-×x+()]{4,}$"
)


def _is_header_token(line: str) -> bool:
    return bool(_HEADER_RE.match(line.strip()))


def _looks_like_code(line: str) -> bool:
    s = line.strip()
    if not s or _is_header_token(s) or _ITEM_NO_RE.match(s) or _QTY_RE.match(s):
        return False
    if not _CODE_RE.match(s):
        return False
    return any(ch.isdigit() for ch in s)


def _clean_assembly(title: str) -> str:
    t = re.sub(r"\s+", " ", (title or "").strip())
    t = re.sub(r"^\d+\s*", "", t)
    return t[:180] if t else "NA"


def detect_catalog_assembly(text: str) -> str:
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _ASSEMBLY_RE.match(line)
        if m:
            return _clean_assembly(m.group(1))
        if "(" in line and ")" in line and any(k in line.lower() for k in ("assembly", "module", "device")):
            if len(line) < 120:
                return _clean_assembly(line)
    return "NA"


_NOV_PART_RE = re.compile(r"\b(\d{6,}-\d{2,4})\b")
_NOV_HEADER_RE = re.compile(
    r"(nov\s*part\s*no|recommended\s+spare\s+parts|description\s+of\s+the\s+recommended)",
    re.I,
)
_SECTION_HEADER_RE = re.compile(
    r"^[A-Z][A-Z0-9 /&\-]{3,40}(?:CUBICLE|CABINET|PANEL|DRIVE|SECTION|ASSEMBLY)?$",
)


def _spare_row_dict(
    *,
    equipment: str,
    assembly: str,
    item_no: str,
    part_name: str,
    part_number_code: str,
    drawing_model_no: str,
    quantity: str,
    recommended_stock_qty: str,
    page_num: int,
    pdf_order: int,
) -> dict[str, Any]:
    return {
        "id": 0,
        "equipment_title": equipment,
        "subsystem_location": assembly,
        "item_no": item_no,
        "part_name": part_name,
        "part_number_code": part_number_code,
        "drawing_model_no": drawing_model_no,
        "oem_standard_body": "NA",
        "part_categorization": "Standard Part",
        "quantity": quantity,
        "recommended_stock_qty": recommended_stock_qty,
        "warranty_period": "NA",
        "frequency_of_use": "NA",
        "page": page_num,
        "pdf_order": pdf_order,
        "grounding_score": 1.0,
        "grounding_available": True,
        "_from_catalog_parser": True,
    }


def extract_nov_spare_rows(
    text: str,
    *,
    page_num: int,
    doc_name: str = "NA",
) -> list[dict[str, Any]]:
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    if len(lines) < 8:
        return []

    nov_hits = sum(1 for ln in lines if _NOV_PART_RE.search(ln))
    headerish = bool(_NOV_HEADER_RE.search(text or ""))
    if nov_hits < 3 and not headerish:
        return []
    if nov_hits < 2:
        return []

    equipment = doc_name.rsplit(".", 1)[0] if doc_name and "." in doc_name else (doc_name or "NA")
    assembly = "NA"
    for ln in lines[:30]:
        if _SECTION_HEADER_RE.match(ln) and "PART" not in ln.upper():
            assembly = ln[:180]
            break

    stop_markers = {
        "MATERIALS DATA", "COMMISSIONING", "SPARES", "TWO YEAR OPER.",
        "TWO YEAR O", "EQUIPMENT DESCRIPTION", "DOC. NO:", "SUPPLIER'S NAME", "PAGE",
    }

    rows: list[dict[str, Any]] = []
    i = 0
    n = len(lines)
    last_item = 0

    def _is_stop(tok: str) -> bool:
        up = tok.upper()
        return any(up.startswith(m) for m in stop_markers)

    while i < n:
        tok = lines[i]
        if _is_stop(tok):
            break
        if _SECTION_HEADER_RE.match(tok) and "PART" not in tok.upper() and not _NOV_PART_RE.search(tok):
            assembly = tok[:180]
            i += 1
            continue

        item_no: str | None = None
        start = i
        if _ITEM_NO_RE.match(tok):
            candidate = int(tok)
            if last_item and candidate not in {last_item + 1, last_item + 2} and candidate > last_item + 5:
                i += 1
                continue
            item_no = tok
            start = i + 1
        elif last_item and any(_NOV_PART_RE.search(x) for x in lines[i : i + 5]):
            item_no = str(last_item + 1)
            start = i
        else:
            i += 1
            continue

        window = lines[start : start + 8]
        nov_idx = None
        nov_code = None
        for wi, wtok in enumerate(window):
            if _is_stop(wtok):
                break
            m = _NOV_PART_RE.search(wtok)
            if m:
                nov_idx = wi
                nov_code = m.group(1)
                break
        if nov_idx is None or nov_code is None:
            i += 1
            continue

        before = window[:nov_idx]
        nov_line = window[nov_idx]
        after = window[nov_idx + 1 :]

        drawing = "NA"
        prefix = nov_line[: nov_line.find(nov_code)].strip(" ,;-")
        if prefix and not _ITEM_NO_RE.match(prefix) and not _QTY_RE.match(prefix):
            drawing = prefix

        if before:
            while before and _QTY_RE.match(before[0]) and not any(c.isalpha() for c in before[0]):
                before = before[1:]
            if not before:
                i += 1
                continue
            part_name = before[0]
            if len(before) >= 2 and drawing == "NA":
                maybe_ref = before[-1]
                if (
                    _looks_like_code(maybe_ref)
                    or (maybe_ref.isupper() and maybe_ref.isalpha() and 2 <= len(maybe_ref) <= 16)
                    or (any(ch.isalpha() for ch in maybe_ref) and any(ch.isdigit() for ch in maybe_ref) and len(maybe_ref) <= 40)
                ) and maybe_ref != before[0]:
                    drawing = maybe_ref
                    part_name = " ".join(before[:-1])
                else:
                    part_name = " ".join(before)
            elif len(before) > 1:
                part_name = " ".join(before)
        else:
            part_name = drawing if drawing != "NA" else "NA"

        part_name = re.sub(r"\s+", " ", part_name).strip() or "NA"
        if not any(ch.isalpha() for ch in part_name):
            i += 1
            continue
        if part_name.upper() in {
            "DESCRIPTION", "ITEM NO.", "ITEM NO", "NOV PART NO.",
            "DESCRIPTION OF THE RECOMMENDED SPARE PARTS",
        }:
            i += 1
            continue

        qty = "NA"
        stock = "NA"
        qty_vals: list[str] = []
        for qtok in after:
            if _is_stop(qtok) or (_ITEM_NO_RE.match(qtok) and int(qtok) == last_item + 2):
                break
            if _QTY_RE.match(qtok):
                qty_vals.append(qtok)
            elif any(ch.isalpha() for ch in qtok):
                break
            if len(qty_vals) >= 2:
                break
        if qty_vals:
            qty = qty_vals[0]
            if len(qty_vals) > 1:
                stock = qty_vals[1]

        rows.append(
            _spare_row_dict(
                equipment=equipment if equipment != "NA" else "NA",
                assembly=assembly,
                item_no=item_no,
                part_name=part_name,
                part_number_code=nov_code,
                drawing_model_no=drawing,
                quantity=qty,
                recommended_stock_qty=stock,
                page_num=page_num,
                pdf_order=len(rows) + 1,
            )
        )
        try:
            last_item = int(item_no)
        except Exception:
            last_item += 1
        consumed = (start - i) + nov_idx + 1 + min(len(qty_vals), 2)
        i += max(consumed, 2)

    return rows


def extract_bomco_catalog_spare_rows(
    text: str,
    *,
    page_num: int,
    doc_name: str = "NA",
) -> list[dict[str, Any]]:
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    if len(lines) < 6:
        return []

    header_hits = sum(1 for ln in lines[:40] if _is_header_token(ln))
    if header_hits < 3 and not any(_ASSEMBLY_RE.match(ln) for ln in lines[:20]):
        code_like = sum(1 for ln in lines if _looks_like_code(ln))
        item_like = sum(1 for ln in lines if _ITEM_NO_RE.match(ln))
        if code_like < 3 or item_like < 3:
            return []

    assembly = detect_catalog_assembly(text)
    equipment = doc_name.rsplit(".", 1)[0] if doc_name and "." in doc_name else (doc_name or "NA")
    if not equipment or equipment == "NA":
        equipment = assembly if assembly != "NA" else "NA"

    rows: list[dict[str, Any]] = []
    i = 0
    n = len(lines)
    while i < n:
        if not _ITEM_NO_RE.match(lines[i]):
            i += 1
            continue
        item_no = lines[i]
        if i + 1 >= n or not _looks_like_code(lines[i + 1]):
            i += 1
            continue
        code = lines[i + 1]
        j = i + 2
        name_parts: list[str] = []
        while j < n:
            tok = lines[j]
            if _ITEM_NO_RE.match(tok) and j + 1 < n and _looks_like_code(lines[j + 1]):
                break
            if _is_header_token(tok):
                j += 1
                continue
            if _QTY_RE.match(tok) and name_parts:
                break
            if _looks_like_code(tok) and name_parts:
                break
            name_parts.append(tok)
            j += 1
            if len(name_parts) >= 10:
                break

        qty = "NA"
        if j < n and _QTY_RE.match(lines[j]):
            qty = lines[j]
            j += 1

        remarks_parts: list[str] = []
        while j < n:
            tok = lines[j]
            if _ITEM_NO_RE.match(tok) and j + 1 < n and _looks_like_code(lines[j + 1]):
                break
            if _is_header_token(tok) or _ASSEMBLY_RE.match(tok):
                break
            remarks_parts.append(tok)
            j += 1
            if len(remarks_parts) >= 6:
                break

        part_name = re.sub(r"\s+", " ", " ".join(name_parts)).strip() or "NA"
        if part_name == "NA" and code.upper() in {"CODE", "NAME", "QTY"}:
            i = j if j > i else i + 1
            continue

        rows.append(
            _spare_row_dict(
                equipment=equipment if equipment != "NA" else (assembly if assembly != "NA" else "NA"),
                assembly=assembly,
                item_no=item_no,
                part_name=part_name,
                part_number_code=code,
                drawing_model_no="NA",
                quantity=qty,
                recommended_stock_qty="NA",
                page_num=page_num,
                pdf_order=len(rows) + 1,
            )
        )
        i = j if j > i else i + 1

    return rows


def extract_catalog_spare_rows(
    text: str,
    *,
    page_num: int,
    doc_name: str = "NA",
) -> list[dict[str, Any]]:
    nov_rows = extract_nov_spare_rows(text, page_num=page_num, doc_name=doc_name)
    bomco_rows = extract_bomco_catalog_spare_rows(text, page_num=page_num, doc_name=doc_name)
    if not nov_rows:
        return bomco_rows
    if not bomco_rows:
        return nov_rows
    return nov_rows if len(nov_rows) >= len(bomco_rows) else bomco_rows


def merge_spare_rows(
    llm_rows: list[dict[str, Any]],
    catalog_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not catalog_rows:
        out: list[dict[str, Any]] = []
        for i, row in enumerate(list(llm_rows or []), start=1):
            r = dict(row)
            if not r.get("pdf_order"):
                r["pdf_order"] = i
            out.append(r)
        return out
    if not llm_rows:
        return list(catalog_rows)

    def key(row: dict[str, Any]) -> str:
        code = str(row.get("part_number_code") or "").strip().upper()
        item = str(row.get("item_no") or "").strip()
        name = str(row.get("part_name") or "").strip().upper()
        if code and code != "NA":
            return f"C:{code}"
        if item and item != "NA" and name and name != "NA":
            return f"I:{item}|{name}"
        return f"N:{name}|{item}"

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in catalog_rows:
        k = key(row)
        if k in seen:
            continue
        seen.add(k)
        merged.append(row)
    for row in llm_rows:
        k = key(row)
        if k in seen:
            continue
        seen.add(k)
        r = dict(row)
        if not r.get("pdf_order"):
            r["pdf_order"] = len(merged) + 1
        merged.append(r)
    for i, row in enumerate(merged, start=1):
        row["pdf_order"] = i
    return merged
