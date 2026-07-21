from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


DEFAULT_KEYWORDS = [
    "replace", "lubricate", "grease", "inspect", "check", "clean", "torque",
    "tighten", "maintenance", "interval", "bearing", "filter", "seal", "gasket",
    "valve", "spare part", "part number", "part no", "drawing", "qty",
    "illustrated parts", "bill of materials", "bom", "troubleshoot", "problem",
    "fault", "cause", "solution", "symptom",
]


@lru_cache
def _default_keywords() -> tuple[str, ...]:
    candidates = [
        Path(__file__).resolve().parents[3] / "equipment_manifest.json",
        Path(__file__).resolve().parents[2] / "equipment_manifest.json",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cats = (data or {}).get("categories") or {}
            default = cats.get("Default") or {}
            kws = default.get("keywords") or []
            if isinstance(kws, list) and kws:
                return tuple(str(k).lower() for k in kws if k)
        except Exception:  # noqa: BLE001
            break
    return tuple(DEFAULT_KEYWORDS)


def is_likely_index_or_toc_page(page_text: str, page_num: int | None = None) -> bool:
    if not page_text:
        return False

    text = str(page_text)
    lower = text.lower()
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    if "table of contents" in lower:
        return True

    dot_leader_count = len(re.findall(r"\.{3,}", text))
    page_ref_count = len(re.findall(r"\bpage\s+\d{1,3}\b", lower))
    contents_word_count = len(re.findall(r"\bcontents?\b", lower))
    index_word_count = len(re.findall(r"\bindex\b", lower))
    numbered_entry_count = len(
        re.findall(r"[A-Za-z][A-Za-z0-9 ,\-\/\(\)]{10,120}(?:\.{2,}\s*|\s{2,})\d{1,3}\b", text)
    )
    section_entry_count = len(
        re.findall(
            r"\b(?:chapter|section|appendix|figure|fig\.?|table)\s*[a-z0-9\.\-]{0,12}\s+[a-z][^.!?\n]{0,80}\s+\d{1,3}\b",
            lower,
        )
    )
    toc_line_count = sum(
        1
        for l in lines
        if re.search(r"(?:\.{2,}\s*)?\d{1,3}$", l) and re.search(r"[a-z]", l, re.I) and len(l) > 8
    )
    heading_like_line_count = sum(
        1 for l in lines if re.match(r"^(?:\d+(?:\.\d+)*)\s+[A-Za-z]", l) and not re.search(r"[.!?]", l)
    )
    short_line_count = sum(1 for l in lines if len(l.split()) <= 14)
    trailing_page_num_line_count = sum(
        1 for l in lines if re.search(r"\b\d{1,3}$", l) and len(l.split()) <= 16
    )
    bare_page_num_count = len(re.findall(r"(?:^|\s)\d{1,3}(?=\s|$)", text))
    sentence_count = len(re.findall(r"[.!?]", text))
    front_matter = isinstance(page_num, int) and page_num <= 8

    if dot_leader_count >= 3:
        return True
    if section_entry_count >= 4:
        return True
    if (contents_word_count > 0 or index_word_count > 0) and numbered_entry_count >= 4:
        return True
    if (page_ref_count + numbered_entry_count) >= 8 and (
        dot_leader_count >= 1 or contents_word_count > 0 or index_word_count > 0
    ):
        return True
    if toc_line_count >= 6 and heading_like_line_count >= 4:
        return True
    if front_matter and trailing_page_num_line_count >= 6 and short_line_count >= 8:
        return True
    if front_matter and bare_page_num_count >= 8 and sentence_count <= 2 and short_line_count >= 6:
        return True
    if front_matter and bare_page_num_count >= 10 and sentence_count <= 3:
        return True
    return False


def should_process_page_with_llm(
    page_text: str,
    page_num: int | None = None,
    *,
    has_image: bool = False,
    keywords: list[str] | None = None,
) -> bool:
    """Skip TOC/index and pages with no maintenance/parts keywords.

    Vision/OCR pages with little native text are always processed.
    """
    text = str(page_text or "")
    if has_image and (not text.strip() or "OCR VISION EXTRACTION" in text.upper()):
        return True
    if not text.strip():
        return False
    if is_likely_index_or_toc_page(text, page_num):
        return False

    lower = text.lower()
    if "table of contents" in lower:
        return False
    if "index" in lower and "part" not in lower:
        return False

    clean = re.sub(r"\s+", " ", lower)
    kws = [k.lower() for k in (keywords or list(_default_keywords())) if k]
    return any(kw in clean for kw in kws)
