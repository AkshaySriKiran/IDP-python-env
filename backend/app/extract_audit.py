"""Durable audit log of AI extraction runs for Global Admin.

Storage:
  1. Always append to local JSONL under OMNIPARSE_DATA_DIR (dev + fallback).
  2. If EXTRACT_AUDIT_S3_BUCKET is set, also PutObject each record (survives ECS redeploy).
  3. Emit one structured stdout line for CloudWatch Logs.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("omniparse.extract_audit")

_lock = threading.RLock()
DATA_DIR = Path(os.getenv("OMNIPARSE_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
LOCAL_INDEX = DATA_DIR / "extract_audit.jsonl"
MAX_LIST = 200


def _audit_bucket() -> str:
    return (os.getenv("EXTRACT_AUDIT_S3_BUCKET") or "").strip()


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_meta(meta: Any) -> dict[str, Any]:
    if meta is None:
        return {}
    if hasattr(meta, "model_dump"):
        return meta.model_dump()
    if isinstance(meta, dict):
        return dict(meta)
    return {}


def build_audit_record(
    *,
    status: str,
    filename: str,
    options: Any = None,
    result: Any = None,
    error: Optional[str] = None,
    job_id: Optional[str] = None,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
    started_at: Optional[float] = None,
    finished_at: Optional[float] = None,
) -> dict[str, Any]:
    """Build a summary audit record (no API keys, no full row payloads)."""
    now = time.time()
    started = float(started_at or now)
    finished = float(finished_at or now)
    duration_ms = max(0, int((finished - started) * 1000))

    opt = options
    engine = getattr(opt, "engine", None) if opt is not None else None
    parse_strategy = getattr(opt, "parse_strategy", None) if opt is not None else None
    gemini_model = getattr(opt, "gemini_model", None) if opt is not None else None
    ollama_model = getattr(opt, "ollama_model", None) if opt is not None else None
    page_start = getattr(opt, "page_start", None) if opt is not None else None
    page_end = getattr(opt, "page_end", None) if opt is not None else None
    equipment_category = getattr(opt, "equipment_category", None) if opt is not None else None

    if isinstance(opt, dict):
        engine = opt.get("engine", engine)
        parse_strategy = opt.get("parse_strategy", parse_strategy)
        gemini_model = opt.get("gemini_model", gemini_model)
        ollama_model = opt.get("ollama_model", ollama_model)
        page_start = opt.get("page_start", page_start)
        page_end = opt.get("page_end", page_end)
        equipment_category = opt.get("equipment_category", equipment_category)

    meta: dict[str, Any] = {}
    maint_n = spare_n = trouble_n = 0
    if result is not None:
        if hasattr(result, "meta"):
            meta = _safe_meta(result.meta)
            maint_n = len(getattr(result, "maintenance", []) or [])
            spare_n = len(getattr(result, "spare_parts", []) or [])
            trouble_n = len(getattr(result, "troubleshooting", []) or [])
        elif isinstance(result, dict):
            meta = _safe_meta(result.get("meta"))
            maint_n = len(result.get("maintenance") or [])
            spare_n = len(result.get("spare_parts") or [])
            trouble_n = len(result.get("troubleshooting") or [])

    engine_label = meta.get("engine") or (
        f"{engine}:{gemini_model}" if engine == "gemini" and gemini_model else (engine or "NA")
    )

    record_id = uuid.uuid4().hex
    return {
        "id": record_id,
        "created_at": _utc_iso(),
        "started_at": datetime.fromtimestamp(started, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "finished_at": datetime.fromtimestamp(finished, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "duration_ms": duration_ms,
        "job_id": job_id,
        "user_id": user_id,
        "user_email": user_email or ("anonymous" if not user_id else None),
        "status": status,  # done | error
        "error": (str(error)[:2000] if error else None),
        "filename": filename or "document",
        "engine": engine_label,
        "parse_strategy": meta.get("parse_strategy") or parse_strategy or "NA",
        "gemini_model": gemini_model,
        "ollama_model": ollama_model if engine == "ollama" else None,
        "equipment_category": equipment_category or meta.get("equipment_category") or "Default",
        "page_start": page_start,
        "page_end": page_end,
        "pages_total": int(meta.get("pages_total") or 0),
        "pages_processed": int(meta.get("pages_processed") or 0),
        "maintenance_count": int(meta.get("maintenance_count") or maint_n),
        "spare_parts_count": int(meta.get("spare_parts_count") or spare_n),
        "troubleshooting_count": int(meta.get("troubleshooting_count") or trouble_n),
        "overall_score": meta.get("overall_score"),
        "grounding_pass_rate": meta.get("grounding_pass_rate"),
        "filter_drop_rate": meta.get("filter_drop_rate"),
        "low_confidence_count": meta.get("low_confidence_count"),
        "warnings": list(meta.get("warnings") or [])[:50],
    }


def _append_local(record: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        with LOCAL_INDEX.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _put_s3(record: dict[str, Any]) -> Optional[str]:
    bucket = _audit_bucket()
    if not bucket:
        return None
    try:
        import boto3  # type: ignore

        created = str(record.get("created_at") or _utc_iso())
        day = created[:10].replace("-", "/")
        key = f"extract-audit/{day}/{record['id']}.json"
        client = boto3.client("s3", region_name=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or None)
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(record, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )
        return key
    except Exception as err:  # noqa: BLE001
        logger.warning("extract audit S3 write failed: %s", err)
        return None


def record_extract_outcome(record: dict[str, Any]) -> dict[str, Any]:
    """Persist one extraction outcome for admin review."""
    s3_key = _put_s3(record)
    if s3_key:
        record = {**record, "s3_key": s3_key}
    try:
        _append_local(record)
    except Exception as err:  # noqa: BLE001
        logger.warning("extract audit local write failed: %s", err)

    # CloudWatch via container stdout (structured, searchable).
    try:
        print(
            json.dumps(
                {
                    "event": "extract_audit",
                    "id": record.get("id"),
                    "status": record.get("status"),
                    "filename": record.get("filename"),
                    "user_email": record.get("user_email"),
                    "engine": record.get("engine"),
                    "overall_score": record.get("overall_score"),
                    "maintenance_count": record.get("maintenance_count"),
                    "spare_parts_count": record.get("spare_parts_count"),
                    "troubleshooting_count": record.get("troubleshooting_count"),
                    "duration_ms": record.get("duration_ms"),
                    "error": record.get("error"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    except Exception:  # noqa: BLE001
        pass
    return record


def _read_local(limit: int = 50) -> list[dict[str, Any]]:
    if not LOCAL_INDEX.exists():
        return []
    rows: list[dict[str, Any]] = []
    with _lock:
        try:
            lines = LOCAL_INDEX.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(rows) >= limit:
            break
    return rows


def _list_s3(limit: int = 50) -> list[dict[str, Any]]:
    bucket = _audit_bucket()
    if not bucket:
        return []
    try:
        import boto3  # type: ignore

        client = boto3.client("s3", region_name=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or None)
        paginator = client.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=bucket, Prefix="extract-audit/"):
            for obj in page.get("Contents") or []:
                key = obj.get("Key") or ""
                if key.endswith(".json"):
                    keys.append(key)
        keys.sort(reverse=True)
        out: list[dict[str, Any]] = []
        for key in keys[:limit]:
            resp = client.get_object(Bucket=bucket, Key=key)
            body = resp["Body"].read().decode("utf-8")
            out.append(json.loads(body))
        return out
    except Exception as err:  # noqa: BLE001
        logger.warning("extract audit S3 list failed: %s", err)
        return []


def _row_day(row: dict[str, Any]) -> str:
    for key in ("started_at", "created_at", "finished_at"):
        val = str(row.get(key) or "")
        if len(val) >= 10 and val[4] == "-" and val[7] == "-":
            return val[:10]
    return ""


def list_extract_audits(
    *,
    limit: int = 50,
    status: Optional[str] = None,
    user_email: Optional[str] = None,
    user_id: Optional[str] = None,
    day: Optional[str] = None,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 50), MAX_LIST))
    # Scan a wider pool when filtering by user/day so recent global rows don't hide theirs.
    scan_limit = MAX_LIST if (user_email or user_id or day) else limit
    rows = _list_s3(limit=scan_limit) if _audit_bucket() else _read_local(limit=scan_limit)
    if not rows and _audit_bucket():
        # S3 empty / unavailable — fall back to local mirror on this task.
        rows = _read_local(limit=scan_limit)

    status_f = (status or "").strip().lower()
    email_f = (user_email or "").strip().lower()
    uid_f = (user_id or "").strip()
    day_f = (day or "").strip()
    if day_f.lower() == "today":
        day_f = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    filtered: list[dict[str, Any]] = []
    for row in rows:
        if status_f and str(row.get("status") or "").lower() != status_f:
            continue
        row_email = str(row.get("user_email") or "").lower()
        if uid_f:
            row_uid = str(row.get("user_id") or "")
            owned = row_uid == uid_f or (bool(email_f) and email_f in row_email)
            if not owned:
                continue
        elif email_f and email_f not in row_email:
            continue
        if day_f and _row_day(row) != day_f:
            continue
        filtered.append(row)
        if len(filtered) >= limit:
            break
    return filtered


def get_extract_audit(record_id: str) -> Optional[dict[str, Any]]:
    rid = (record_id or "").strip()
    if not rid:
        return None
    for row in list_extract_audits(limit=MAX_LIST):
        if str(row.get("id")) == rid:
            return row
    # Direct S3 day-agnostic search by listing (already covered) — try local full scan.
    if LOCAL_INDEX.exists():
        with _lock:
            try:
                lines = LOCAL_INDEX.read_text(encoding="utf-8").splitlines()
            except OSError:
                lines = []
        for line in reversed(lines):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("id")) == rid:
                return row
    return None
