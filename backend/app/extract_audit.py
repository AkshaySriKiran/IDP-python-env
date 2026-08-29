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
DATA_DIR = Path(os.getenv("OMNIPARSE_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
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


def _name_from_email(email: Optional[str]) -> Optional[str]:
    value = str(email or "").strip()
    if not value or value.lower() == "anonymous":
        return None
    if "@" in value:
        return value.split("@", 1)[0].strip() or None
    return value


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
    user_name: Optional[str] = None,
    started_at: Optional[float] = None,
    finished_at: Optional[float] = None,
) -> dict[str, Any]:
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
        "user_name": (str(user_name).strip() if user_name else None)
        or _name_from_email(user_email)
        or ("anonymous" if not user_id else None),
        "status": status,
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
        "run_id": meta.get("run_id") or job_id,
        "document_status": meta.get("document_status") or "Pending Review",
        "approved_by": meta.get("approved_by"),
        "approved_at": meta.get("approved_at"),
        "rejection_notes": meta.get("rejection_reason"),
    }


def update_extract_audit_review_state(
    target_id: str,
    *,
    document_status: str,
    approved_by: Optional[str] = None,
    approved_at: Optional[str] = None,
    rejection_notes: Optional[str] = None,
) -> bool:
    target = (target_id or "").strip()
    if not target or not LOCAL_INDEX.exists():
        return False
    updated = False
    with _lock:
        try:
            lines = LOCAL_INDEX.read_text(encoding="utf-8").splitlines()
            new_lines = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("id") == target or rec.get("run_id") == target or rec.get("job_id") == target:
                        rec["document_status"] = document_status
                        if approved_by:
                            rec["approved_by"] = approved_by
                        if approved_at:
                            rec["approved_at"] = approved_at
                        if rejection_notes:
                            rec["rejection_notes"] = rejection_notes
                        updated = True
                    new_lines.append(json.dumps(rec, ensure_ascii=False))
                except Exception:
                    new_lines.append(line)
            if updated:
                LOCAL_INDEX.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        except Exception as err:
            logger.warning("Failed to update local extract audit review state: %s", err)
    return updated


def _append_local(record: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        with LOCAL_INDEX.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def record_extract_outcome(record: dict[str, Any]) -> dict[str, Any]:
    try:
        _append_local(record)
    except Exception as err:
        logger.warning("extract audit local write failed: %s", err)

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
    except Exception:
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
        import boto3

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
    except Exception as err:
        logger.warning("extract audit S3 list failed: %s", err)
        return []


def _row_day(row: dict[str, Any]) -> str:
    for key in ("started_at", "created_at", "finished_at"):
        val = str(row.get(key) or "")
        if len(val) >= 10 and val[4] == "-" and val[7] == "-":
            return val[:10]
    return ""


def resolve_audit_user_name(row: dict[str, Any]) -> str:
    stored = str(row.get("user_name") or "").strip()
    if stored and stored.lower() != "anonymous":
        return stored
    try:
        from .auth import store as auth_store

        user = None
        if row.get("user_id"):
            user = auth_store.find_by_id(str(row["user_id"]))
        if not user and row.get("user_email"):
            user = auth_store.find_by_email(str(row["user_email"]))
        if user:
            display = str(user.get("display_name") or "").strip()
            if display:
                return display
            local = _name_from_email(str(user.get("email") or ""))
            if local:
                return local
    except Exception:
        pass
    return _name_from_email(str(row.get("user_email") or "")) or "anonymous"


def _fabric_row_to_audit(r: dict[str, Any]) -> dict[str, Any]:
    ext_at = r.get("extracted_at")
    if ext_at is not None and not isinstance(ext_at, str):
        try:
            ext_at = ext_at.isoformat()
        except Exception:
            ext_at = str(ext_at)
    run_id = str(r.get("run_id") or "")
    user_email = r.get("user_email") or r.get("approved_by")
    if not user_email and "[user:" in str(r.get("engine") or ""):
        try:
            user_email = str(r["engine"]).split("[user:", 1)[1].split("]", 1)[0].strip()
        except Exception:
            pass
    return {
        "id": run_id,
        "run_id": run_id,
        "created_at": ext_at or _utc_iso(),
        "started_at": ext_at or _utc_iso(),
        "finished_at": ext_at or _utc_iso(),
        "duration_ms": int(r.get("duration_ms") or 0),
        "job_id": run_id,
        "user_id": r.get("user_id"),
        "user_email": user_email,
        "user_name": _name_from_email(user_email),
        "status": "pass" if (r.get("status") or "done") == "done" else str(r.get("status")),
        "document_status": r.get("document_status") or "Pending Review",
        "approved_by": r.get("approved_by"),
        "approved_at": r.get("approved_at"),
        "rejection_notes": r.get("rejection_notes"),
        "filename": str(r.get("filename") or ""),
        "engine": str(r.get("engine") or ""),
        "parse_strategy": str(r.get("parse_strategy") or ""),
        "maintenance_count": int(r.get("maintenance_count") or 0),
        "spare_parts_count": int(r.get("spare_parts_count") or 0),
        "troubleshooting_count": int(r.get("troubleshooting_count") or 0),
        "overall_score": (float(r["overall_score"]) if r.get("overall_score") is not None else None),
        "grounding_pass_rate": (float(r["grounding_pass_rate"]) if r.get("grounding_pass_rate") is not None else None),
        "filter_drop_rate": (float(r["filter_drop_rate"]) if r.get("filter_drop_rate") is not None else None),
        "low_confidence_count": (int(r["low_confidence_count"]) if r.get("low_confidence_count") is not None else None),
        "warnings": [],
    }


def list_extract_audits(
    *,
    limit: int = 50,
    status: Optional[str] = None,
    user_email: Optional[str] = None,
    user_name: Optional[str] = None,
    user_id: Optional[str] = None,
    day: Optional[str] = None,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 50), MAX_LIST))
    scan_limit = MAX_LIST if (user_email or user_name or user_id or day) else limit

    rows: list[dict[str, Any]] = []

    # 1. Primary: Fetch from Microsoft Fabric SQL central repository if configured
    try:
        from .integrations import fabric_sql
        from .integrations.fabric_cache import list_done_extracts
        if fabric_sql.fabric_configured():
            fabric_done = list_done_extracts(
                limit=scan_limit,
                user_id=user_id,
                user_email=user_email,
            )
            rows.extend([_fabric_row_to_audit(f) for f in fabric_done if f.get("run_id")])
    except Exception as err:
        logger.debug("Fabric extraction logs query fallback: %s", err)

    # 2. Secondary: Merge local / S3 logs (avoiding duplicates by run_id/id)
    known_ids = {str(r.get("run_id") or r.get("id")) for r in rows if (r.get("run_id") or r.get("id"))}
    raw_local = _list_s3(limit=scan_limit) if _audit_bucket() else _read_local(limit=scan_limit)
    if not raw_local and _audit_bucket():
        raw_local = _read_local(limit=scan_limit)
    for lr in raw_local:
        lid = str(lr.get("run_id") or lr.get("id") or "")
        if lid not in known_ids:
            rows.append(lr)
            if lid:
                known_ids.add(lid)

    status_f = (status or "").strip().lower()
    email_f = (user_email or "").strip().lower()
    name_f = (user_name or "").strip().lower()
    uid_f = (user_id or "").strip()
    day_f = (day or "").strip()
    if day_f.lower() == "today":
        day_f = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    filtered: list[dict[str, Any]] = []
    for row in rows:
        if status_f and str(row.get("status") or "").lower() != status_f:
            continue
        row_email = str(row.get("user_email") or "").lower()
        row_name = resolve_audit_user_name(row).lower()
        row_local = (_name_from_email(row_email) or "").lower()
        if uid_f:
            row_uid = str(row.get("user_id") or "")
            owned = row_uid == uid_f or (bool(email_f) and email_f in row_email)
            if not owned:
                continue
        elif name_f:
            haystacks = (row_name, row_local, row_email)
            if not any(name_f in h for h in haystacks):
                tokens = [t for t in name_f.split() if t]
                if not (len(tokens) > 1 and all(any(t in h for h in haystacks) for t in tokens)):
                    continue
        elif email_f and email_f not in row_email:
            continue
        if day_f and _row_day(row) != day_f:
            continue
        enriched = {**row, "user_name": resolve_audit_user_name(row)}
        filtered.append(enriched)
        if len(filtered) >= limit:
            break
    return filtered


def get_extract_audit(record_id: str) -> Optional[dict[str, Any]]:
    rid = (record_id or "").strip()
    if not rid:
        return None

    # Check Microsoft Fabric first
    try:
        from .integrations import fabric_sql
        from .integrations.fabric_cache import get_done_run
        if fabric_sql.fabric_configured():
            f_row = get_done_run(rid)
            if f_row:
                raw_env = str(f_row.get("error") or "").strip()
                if raw_env.startswith("{") and raw_env.endswith("}"):
                    try:
                        env = json.loads(raw_env)
                        f_row["document_status"] = env.get("document_status") or "Pending Review"
                        f_row["approved_by"] = env.get("approved_by")
                        f_row["approved_at"] = env.get("approved_at")
                        f_row["rejection_notes"] = env.get("rejection_notes")
                    except Exception:
                        pass
                return _fabric_row_to_audit(f_row)
    except Exception:
        pass

    for row in list_extract_audits(limit=MAX_LIST):
        if str(row.get("id")) == rid or str(row.get("run_id")) == rid:
            return row
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
            if str(row.get("id")) == rid or str(row.get("run_id")) == rid:
                return row
    return None
