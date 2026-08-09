#!/usr/bin/env python3
"""BOGEL O&M training/eval harness — extract + score manuals with Gemini."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent / ".tmp-bogel-train"
MANUALS = Path("/Users/akshayryali/Downloads/BOGEL O&M Manuals")

# Ensure backend package imports work when run as a script.
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Load local training env (GEMINI_API_KEY etc.)
env_path = OUT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

os.environ.setdefault("AUTH_REQUIRED", "false")
os.environ.setdefault("GEMINI_MODEL", "gemini-3.6-flash")

from app.extractors.pipeline import extract_document  # noqa: E402
from app.models import ExtractOptions  # noqa: E402


def filled(v) -> bool:
    s = str(v or "").strip()
    return bool(s) and s.upper() not in {"NA", "N/A", "NONE", "-", "NULL", ""}


def score_result(result, *, expected_hints: dict | None = None) -> dict:
    meta = result.meta.model_dump() if hasattr(result.meta, "model_dump") else dict(result.meta or {})
    maint = [r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in result.maintenance]
    spares = [r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in result.spare_parts]
    trouble = [r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in result.troubleshooting]

    def field_rates(rows, fields):
        if not rows:
            return {f: None for f in fields}
        return {f: round(sum(1 for r in rows if filled(r.get(f))) / len(rows), 3) for f in fields}

    maint_fields = ["equipment_title", "subsystem_component", "maintenance_routine", "checks_instructions"]
    spare_fields = ["equipment_title", "part_name", "part_number_code", "item_no"]
    trouble_fields = ["equipment_title", "subsystem_component", "problem", "root_cause_solution"]

    # Order check for spares with item_no on same page
    order_ok = None
    by_page: dict[int, list] = {}
    for r in spares:
        try:
            p = int(r.get("page"))
        except Exception:
            continue
        by_page.setdefault(p, []).append(r)
    bad_pages = 0
    checked = 0
    for p, rows in by_page.items():
        nums = []
        for r in rows:
            try:
                nums.append(int(str(r.get("item_no")).strip()))
            except Exception:
                nums.append(None)
        if sum(1 for n in nums if n is not None) >= 3:
            checked += 1
            valid = [n for n in nums if n is not None]
            if valid != sorted(valid):
                bad_pages += 1
    if checked:
        order_ok = round(1 - (bad_pages / checked), 3)

    spare_id_rate = None
    if spares:
        spare_id_rate = round(
            sum(
                1
                for r in spares
                if any(filled(r.get(f)) for f in ("part_number_code", "item_no", "drawing_model_no"))
            )
            / len(spares),
            3,
        )

    return {
        "counts": {
            "maintenance": len(maint),
            "spare_parts": len(spares),
            "troubleshooting": len(trouble),
        },
        "overall_score": meta.get("overall_score"),
        "filter_drop_rate": meta.get("filter_drop_rate"),
        "low_confidence_count": meta.get("low_confidence_count"),
        "maint_field_fill": field_rates(maint, maint_fields),
        "spare_field_fill": field_rates(spares, spare_fields),
        "trouble_field_fill": field_rates(trouble, trouble_fields),
        "spare_id_rate": spare_id_rate,
        "spare_item_order_ok": order_ok,
        "warnings": meta.get("warnings") or [],
        "engine": meta.get("engine"),
        "parse_strategy": meta.get("parse_strategy"),
        "pages_processed": meta.get("pages_processed"),
        "expected_hints": expected_hints or {},
        "samples": {
            "maintenance": [
                {k: r.get(k) for k in ("page", "subsystem_component", "maintenance_routine", "checks_instructions")}
                for r in maint[:2]
            ],
            "spare_parts": [
                {k: r.get(k) for k in ("page", "item_no", "part_name", "part_number_code", "pdf_order")}
                for r in spares[:3]
            ],
            "troubleshooting": [
                {k: r.get(k) for k in ("page", "problem", "root_cause_solution")}
                for r in trouble[:2]
            ],
        },
    }


# Training corpus plan: full small/medium docs; targeted slices for huge ones.
JOBS = [
    {
        "file": "DRAWORKS MOTOR MANUAL.pdf",
        "parse_strategy": "native",
        "page_start": 1,
        "page_end": 27,
        "expect": {"maintenance": True, "spare_parts": True, "troubleshooting": False},
    },
    {
        "file": "2.10 User Manual of DCR.pdf",
        "parse_strategy": "native",
        "page_start": 1,
        "page_end": 15,
        "expect": {"maintenance": True, "spare_parts": False, "troubleshooting": True},
    },
    {
        "file": "User Manual of DW Electrical System.pdf",
        "parse_strategy": "native",
        "page_start": 1,
        "page_end": 22,
        "expect": {"maintenance": True, "spare_parts": False, "troubleshooting": True},
    },
    {
        "file": "Breaking resistor manual.pdf",
        "parse_strategy": "ocr",
        "page_start": 1,
        "page_end": 12,
        "expect": {"maintenance": True, "spare_parts": True, "troubleshooting": False},
    },
    {
        "file": "F-1600HL pump unit.pdf",
        "parse_strategy": "native",
        "page_start": 1,
        "page_end": 33,
        "expect": {"maintenance": True, "spare_parts": True, "troubleshooting": False},
    },
    {
        "file": "Odyssey AC manual.pdf",
        "parse_strategy": "native",
        "page_start": 1,
        "page_end": 30,
        "expect": {"maintenance": True, "spare_parts": True, "troubleshooting": False},
    },
    {
        "file": "1.JC70DB DW User Manual (1).pdf",
        "parse_strategy": "native",
        "page_start": 38,
        "page_end": 55,
        "expect": {"maintenance": True, "spare_parts": True, "troubleshooting": True},
    },
    {
        "file": "2.8 User Manual of HMI.pdf",
        "parse_strategy": "native",
        "page_start": 20,
        "page_end": 55,
        "expect": {"maintenance": False, "spare_parts": False, "troubleshooting": False},
        "note": "HMI UI legend pages — alarm screens are not classic trouble matrices",
    },
    {
        "file": "TDS_11SA_ Durga 17 VFD 19028197-MAN Rev. 01 Final.pdf",
        "label": "TDS spare list pages",
        "parse_strategy": "native",
        "page_start": 99,
        "page_end": 105,
        "expect": {"maintenance": False, "spare_parts": True, "troubleshooting": False},
    },
    {
        "file": "TDS_11SA_ Durga 17 VFD 19028197-MAN Rev. 01 Final.pdf",
        "label": "TDS troubleshooting pages",
        "parse_strategy": "native",
        "page_start": 44,
        "page_end": 57,
        "expect": {"maintenance": False, "spare_parts": False, "troubleshooting": True},
    },
    {
        "file": "MI Swaco Shale Shaker Manual.pdf",
        "parse_strategy": "native",
        "page_start": 165,
        "page_end": 200,
        "expect": {"maintenance": True, "spare_parts": True, "troubleshooting": True},
    },
    {
        "file": "MI Swaco Shale Shaker Manual.pdf",
        "label": "MI Swaco parts tables",
        "parse_strategy": "native",
        "page_start": 247,
        "page_end": 265,
        "expect": {"maintenance": False, "spare_parts": True, "troubleshooting": False},
    },
    {
        "file": "MI Swaco Shale Shaker Manual.pdf",
        "label": "MI Swaco RSPL images",
        "parse_strategy": "native",
        "page_start": 185,
        "page_end": 190,
        "expect": {"maintenance": False, "spare_parts": True, "troubleshooting": False},
    },
    {
        "file": "Odyssey AC manual.pdf",
        "parse_strategy": "ocr",
        "page_start": 30,
        "page_end": 51,
        "expect": {"maintenance": True, "spare_parts": False, "troubleshooting": False},
    },
]


async def run_job(job: dict) -> dict:
    path = MANUALS / job["file"]
    label = job.get("label") or job["file"]
    data = path.read_bytes()
    opts = ExtractOptions(
        engine="gemini",
        parse_strategy=job["parse_strategy"],  # type: ignore[arg-type]
        gemini_api_key=os.environ.get("GEMINI_API_KEY"),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-3.6-flash"),
        page_start=job.get("page_start"),
        page_end=job.get("page_end"),
        equipment_category="Default",
    )
    t0 = time.time()
    print(f"\n>>> START {label} pages {job.get('page_start')}-{job.get('page_end')} strategy={job['parse_strategy']}", flush=True)

    def on_progress(msg: str, prog: float) -> None:
        if int(prog * 20) != getattr(on_progress, "_last", -1):
            on_progress._last = int(prog * 20)  # type: ignore[attr-defined]
            print(f"  [{label[:28]}] {prog:.0%} {msg}", flush=True)

    try:
        result = await extract_document(data, path.name, opts, on_progress=on_progress)
        scored = score_result(result, expected_hints=job.get("expect"))
        scored["ok"] = True
        scored["error"] = None
    except Exception as err:  # noqa: BLE001
        scored = {"ok": False, "error": str(err), "counts": {}, "overall_score": None}
        print(f"  ERROR {label}: {err}", flush=True)
    scored["label"] = label
    scored["file"] = job["file"]
    scored["page_range"] = [job.get("page_start"), job.get("page_end")]
    scored["elapsed_s"] = round(time.time() - t0, 1)
    print(
        f"<<< DONE {label} in {scored['elapsed_s']}s "
        f"score={scored.get('overall_score')} counts={scored.get('counts')}",
        flush=True,
    )
    # Persist per-job
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", label)[:80]
    (OUT / f"result_{safe}.json").write_text(json.dumps(scored, indent=2, default=str))
    return scored


async def main() -> None:
    OUT.mkdir(exist_ok=True)
    if not os.environ.get("GEMINI_API_KEY"):
        raise SystemExit("GEMINI_API_KEY missing")
    summary = []
    for job in JOBS:
        summary.append(await run_job(job))
        (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print("\n==== TRAINING SUMMARY ====")
    for s in summary:
        print(
            f"- {s.get('label')}: ok={s.get('ok')} score={s.get('overall_score')} "
            f"m/s/t={s.get('counts')} order={s.get('spare_item_order_ok')} err={s.get('error')}"
        )


if __name__ == "__main__":
    asyncio.run(main())
