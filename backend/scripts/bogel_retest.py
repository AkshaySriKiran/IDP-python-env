#!/usr/bin/env python3
"""Focused retest after BOGEL training fixes."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent / ".tmp-bogel-train"
MANUALS = Path("/Users/akshayryali/Downloads/BOGEL O&M Manuals")
sys.path.insert(0, str(ROOT))

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
from scripts.bogel_train_eval import score_result  # noqa: E402

JOBS = [
    {
        "file": "TDS_11SA_ Durga 17 VFD 19028197-MAN Rev. 01 Final.pdf",
        "label": "TDS spare list pages",
        "parse_strategy": "native",
        "page_start": 99,
        "page_end": 105,
        "expect": {"spare_parts": True},
    },
    {
        "file": "1.JC70DB DW User Manual (1).pdf",
        "label": "JC70DB maint/trouble/spares",
        "parse_strategy": "native",
        "page_start": 38,
        "page_end": 55,
        "expect": {"maintenance": True, "spare_parts": True, "troubleshooting": True},
    },
    {
        "file": "MI Swaco Shale Shaker Manual.pdf",
        "label": "MI Swaco RSPL images",
        "parse_strategy": "native",
        "page_start": 185,
        "page_end": 190,
        "expect": {"spare_parts": True},
    },
    {
        "file": "Odyssey AC manual.pdf",
        "label": "Odyssey OCR maint",
        "parse_strategy": "ocr",
        "page_start": 35,
        "page_end": 51,
        "expect": {"maintenance": True},
    },
    {
        "file": "F-1600HL pump unit.pdf",
        "label": "F-1600HL catalog pages",
        "parse_strategy": "native",
        "page_start": 23,
        "page_end": 26,
        "expect": {"spare_parts": True},
    },
]


async def run_job(job: dict) -> dict:
    path = MANUALS / job["file"]
    label = job.get("label") or job["file"]
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
    print(f"\n>>> START {label} {job.get('page_start')}-{job.get('page_end')}", flush=True)

    def on_progress(msg: str, prog: float) -> None:
        if int(prog * 10) != getattr(on_progress, "_last", -1):
            on_progress._last = int(prog * 10)  # type: ignore[attr-defined]
            print(f"  [{label[:28]}] {prog:.0%} {msg}", flush=True)

    try:
        result = await extract_document(path.read_bytes(), path.name, opts, on_progress=on_progress)
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
        f"<<< DONE {label} in {scored['elapsed_s']}s score={scored.get('overall_score')} "
        f"counts={scored.get('counts')}",
        flush=True,
    )
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", label)[:80]
    (OUT / f"retest_{safe}.json").write_text(json.dumps(scored, indent=2, default=str))
    return scored


async def main() -> None:
    OUT.mkdir(exist_ok=True)
    summary = []
    for job in JOBS:
        summary.append(await run_job(job))
        (OUT / "retest_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print("\n==== RETEST SUMMARY ====")
    for s in summary:
        print(f"- {s.get('label')}: score={s.get('overall_score')} counts={s.get('counts')} err={s.get('error')}")


if __name__ == "__main__":
    asyncio.run(main())
