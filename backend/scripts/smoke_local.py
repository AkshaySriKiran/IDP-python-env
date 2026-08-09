#!/usr/bin/env python3
"""Quick local Gemini extract smoke test (1–2 pages)."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = os.environ.get("API_BASE", "http://127.0.0.1:8001").rstrip("/")


def load_env() -> None:
    env_path = ROOT / "backend" / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def http_json(method: str, url: str, data: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
        return json.loads(raw.decode("utf-8", errors="replace"))


def main() -> int:
    load_env()
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        print("FAIL: GEMINI_API_KEY missing in backend/.env")
        return 1

    health = http_json("GET", f"{API}/api/health")
    print("health:", health)
    if not health.get("status") == "ok":
        print("FAIL: API not healthy")
        return 1

    # Prefer a known BOGEL manual; fall back to any pdf under Downloads
    candidates = [
        Path("/Users/akshayryali/Downloads/BOGEL O&M Manuals/F-1600HL pump unit.pdf"),
        Path("/Users/akshayryali/Downloads/BOGEL O&M Manuals/1.JC70DB DW User Manual (1).pdf"),
    ]
    pdf = next((p for p in candidates if p.is_file()), None)
    if pdf is None:
        print("FAIL: no test PDF found")
        return 1

    boundary = "----omniparseSmoke"
    model = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash").strip() or "gemini-3.6-flash"
    fields = {
        "engine": "gemini",
        "parse_strategy": "native",
        "gemini_api_key": "",  # server .env should supply it
        "gemini_model": model,
        "equipment_category": "Default",
        "page_start": "1",
        "page_end": "2",
    }
    body = b""
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    file_bytes = pdf.read_bytes()
    body += (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{pdf.name}\"\r\n"
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()

    print(f"uploading {pdf.name} pages 1-2 via {model} ...")
    created = http_json(
        "POST",
        f"{API}/api/extract/jobs",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    job_id = created.get("job_id")
    print("job:", job_id, created.get("status"))
    if not job_id:
        print("FAIL: no job id", created)
        return 1

    for i in range(90):
        time.sleep(2)
        try:
            with urllib.request.urlopen(f"{API}/api/extract/jobs/{job_id}", timeout=60) as resp:
                raw = resp.read()
            job = json.loads(raw.decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", errors="replace")
            print(f"FAIL: poll HTTP {err.code}: {body[:300]}")
            return 1
        status = job.get("status")
        msg = (job.get("message") or "")[:100]
        print(f"[{i}] {status} {msg}")
        if status == "done":
            result = job.get("result") or {}
            m = len(result.get("maintenance") or [])
            s = len(result.get("spare_parts") or [])
            t = len(result.get("troubleshooting") or [])
            print(f"PASS: extraction finished — maintenance={m} spares={s} troubleshooting={t}")
            return 0
        if status == "error":
            print("FAIL:", job.get("error") or job.get("message"))
            return 1

    print("FAIL: timeout waiting for job")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        print("FAIL: HTTP", err.code, body[:400])
        raise SystemExit(1)
    except urllib.error.URLError as err:
        print("FAIL: API not reachable at", API, err)
        raise SystemExit(1)