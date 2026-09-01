from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from ..models import (
    ExtractMeta,
    ExtractOptions,
    ExtractResponse,
    MaintenanceRow,
    RowQuality,
    SparePartRow,
    TroubleshootingRow,
)
from . import fabric_sql, graph_sharepoint

logger = logging.getLogger(__name__)

# Warehouse `error` column is historically VARCHAR(2000). Full dual payloads exceed that
# and cause the entire Fabric save (including audit) to abort. Keep a slim envelope in SQL;
# full payload stays in the local extract_store and relational row tables.
_LOG_ERROR_MAX_CHARS = 1800

_LOG_SELECT_CANDIDATES = [
    "run_id", "filename", "content_hash", "drive_item_id", "etag",
    "overall_score", "maintenance_count", "spare_parts_count", "troubleshooting_count",
    "engine", "parse_strategy", "extracted_at", "error", "envelope_json", "status",
    "document_status", "user_id", "user_email", "approved_by", "approved_at",
    "submitted_by", "assigned_approver",
]


def _log_select_sql(conn: Any, *, top: int = 1) -> str:
    known = fabric_sql._get_table_columns(conn, "Tbl_PM_Extraction_logs")
    cols = [c for c in _LOG_SELECT_CANDIDATES if not known or c.lower() in known]
    if not cols:
        cols = ["run_id", "filename", "content_hash", "error", "extracted_at"]
    return f"SELECT TOP {max(1, int(top))} {', '.join(cols)} FROM Tbl_PM_Extraction_logs"


def _json_dumps(obj: Any) -> str:
    def _default(o: Any):
        if hasattr(o, "isoformat"):
            return o.isoformat()
        return str(o)
    return json.dumps(obj, ensure_ascii=True, default=_default)


def _envelope_json_for_log_column(envelope: dict[str, Any], *, max_chars: int = _LOG_ERROR_MAX_CHARS) -> str:
    """Serialize slim metadata envelope for log columns (payloads live in payloads table)."""
    full = _json_dumps(envelope)
    if len(full) <= max_chars:
        return full
    slim = {
        "_v": envelope.get("_v", 2),
        "_slim": True,
        "run_id": envelope.get("run_id"),
        "content_hash": envelope.get("content_hash"),
        "drive_item_id": envelope.get("drive_item_id"),
        "etag": envelope.get("etag"),
        "filename": envelope.get("filename"),
        "doc_metadata": envelope.get("doc_metadata") or {},
        "document_status": envelope.get("document_status"),
        "approved_by": envelope.get("approved_by"),
        "approved_at": str(envelope.get("approved_at")) if envelope.get("approved_at") is not None else None,
        "submitted_by": envelope.get("submitted_by"),
        "assigned_approver": envelope.get("assigned_approver"),
        "rejection_notes": envelope.get("rejection_notes"),
        "user_id": envelope.get("user_id"),
        "user_email": envelope.get("user_email"),
        "user_role": envelope.get("user_role"),
        "doc_title": envelope.get("doc_title"),
        "oem_manufacturer": envelope.get("oem_manufacturer"),
        "equipment_model": envelope.get("equipment_model"),
        "equipment_type": envelope.get("equipment_type"),
        "document_version": envelope.get("document_version"),
        "publication_date": envelope.get("publication_date"),
        "duration_ms": envelope.get("duration_ms"),
        "pages_total": envelope.get("pages_total"),
        "pages_processed": envelope.get("pages_processed"),
        "payload_in_row_tables": True,
    }
    slim_json = _json_dumps(slim)
    if len(slim_json) <= max_chars:
        return slim_json
    return slim_json[: max_chars - 3] + "..."


def _emit_extract_audit(
    conn: Any,
    *,
    event_type: str,
    run_id: str,
    content_hash: Optional[str] = None,
    filename: Optional[str] = None,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
    user_role: Optional[str] = None,
    from_status: Optional[str] = None,
    to_status: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> None:
    fabric_sql.insert_audit_event(
        conn,
        {
            "event_id": uuid.uuid4().hex,
            "event_type": event_type,
            "run_id": run_id,
            "content_hash": content_hash,
            "filename": filename,
            "user_id": user_id,
            "user_email": user_email,
            "user_role": user_role,
            "from_status": from_status,
            "to_status": to_status,
            "details_json": json.dumps(details or {}, ensure_ascii=True, default=str),
            "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
        },
    )


SPARE_COLS = [
    "run_id", "equipment_title", "subsystem_location", "item_no", "part_name",
    "part_number_code", "drawing_model_no", "oem_standard_body", "part_categorization",
    "quantity", "recommended_stock_qty", "warranty_period", "frequency_of_use",
    "page", "pdf_order", "confidence", "fields_filled_score", "page_match_score",
    "grounding_available", "quality_reasons", "ai_extract_text",
    "status", "reviewed_by", "reviewed_at", "rejection_reason",
]

MAINT_COLS = [
    "run_id", "equipment_title", "subsystem_component", "maintenance_routine",
    "checks_instructions", "date", "maintenance_work_description", "parts_renewed",
    "attended_by", "remarks", "page", "pdf_order", "confidence", "fields_filled_score",
    "page_match_score", "grounding_available", "quality_reasons", "ai_extract_text",
    "status", "reviewed_by", "reviewed_at", "rejection_reason",
]

TROUBLE_COLS = [
    "run_id", "equipment_title", "subsystem_component", "problem", "root_cause_solution",
    "page", "pdf_order", "confidence", "fields_filled_score", "page_match_score",
    "grounding_available", "quality_reasons", "ai_extract_text",
    "status", "reviewed_by", "reviewed_at", "rejection_reason",
]


import os
import threading
from pathlib import Path

DATA_DIR = Path(os.getenv("OMNIPARSE_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
CACHE_DIR = DATA_DIR / "extract_store"
_cache_lock = threading.RLock()
_MEM_CACHE: dict[str, dict[str, Any]] = {}
_LIST_EXTRACTS_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_LIST_EXTRACTS_TTL = 4.0


def invalidate_extracts_list_cache() -> None:
    with _cache_lock:
        _LIST_EXTRACTS_CACHE.clear()


def _store_in_cache(run_id: str, record: dict[str, Any]) -> None:
    with _cache_lock:
        _MEM_CACHE[run_id] = record
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            run_file = CACHE_DIR / f"{run_id}.json"
            run_file.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        except Exception as err:
            logger.debug("Local extract cache write error: %s", err)


def _load_from_cache(run_id: str) -> Optional[dict[str, Any]]:
    with _cache_lock:
        if run_id in _MEM_CACHE:
            return dict(_MEM_CACHE[run_id])
        try:
            run_file = CACHE_DIR / f"{run_id}.json"
            if run_file.exists():
                data = json.loads(run_file.read_text(encoding="utf-8"))
                _MEM_CACHE[run_id] = data
                return data
        except Exception:
            pass
    return None


def _find_in_cache(
    *,
    content_hash: Optional[str] = None,
    filename: Optional[str] = None,
    drive_item_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    with _cache_lock:
        candidates.extend(list(_MEM_CACHE.values()))
        if CACHE_DIR.exists():
            for p in CACHE_DIR.glob("*.json"):
                if p.stem not in _MEM_CACHE:
                    try:
                        d = json.loads(p.read_text(encoding="utf-8"))
                        _MEM_CACHE[p.stem] = d
                        candidates.append(d)
                    except Exception:
                        pass

    norm_hash = (content_hash or "").strip().lower()
    norm_fn = (filename or "").strip().lower()
    norm_item = (drive_item_id or "").strip()

    for rec in reversed(candidates):
        r_hash = str(rec.get("content_hash") or "").strip().lower()
        r_fn = str(rec.get("filename") or "").strip().lower()
        r_item = str(rec.get("drive_item_id") or "").strip()

        if norm_hash and r_hash and r_hash == norm_hash:
            return rec
        if norm_item and norm_item != "LOCAL_UPLOAD" and r_item and r_item == norm_item:
            return rec
        if norm_fn and r_fn and r_fn == norm_fn:
            return rec

    return None


def file_sha256(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def _quality_from_row(d: dict[str, Any]) -> Optional[RowQuality]:
    try:
        reasons: list[str] = []
        raw = d.get("quality_reasons")
        if raw:
            reasons = [x.strip() for x in str(raw).split("|") if x.strip()]
        return RowQuality(
            grounding_score=float(d.get("page_match_score") or 0.5),
            completeness_score=float(d.get("fields_filled_score") or 0.5),
            grounding_available=str(d.get("grounding_available") or "").lower() == "true",
            reasons=reasons,
        )
    except Exception:
        return None


def _row_content_hash(row: dict[str, Any]) -> str:
    rh = str(row.get("content_hash") or "").strip().lower()
    if rh:
        return rh
    env_raw = str(row.get("error") or "")
    if env_raw.startswith("{"):
        try:
            return str(json.loads(env_raw).get("content_hash") or "").strip().lower()
        except Exception:
            pass
    return ""


def find_user_run_by_content_hash(
    content_hash: str,
    *,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Return the current user's most recent extract run for a file hash, if any."""
    norm_hash = (content_hash or "").strip().lower()
    if not norm_hash or (not user_id and not user_email):
        return None
    try:
        rows = list_done_extracts(limit=200, user_id=user_id, user_email=user_email)
    except Exception:
        return None
    for row in rows:
        if _row_content_hash(row) == norm_hash:
            return row
    return None


_REVIEW_QUEUE_STATUSES = {"Pending Review", "Pending Sign-Off", "In Review"}


def resolve_approved_source(
    content_hash: str,
    cached: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Return the canonical globally approved source row for a PDF hash, if any."""
    approved = find_approved_run_by_content_hash(content_hash)
    if approved:
        return approved
    if cached and _clean_status(cached.get("document_status")) == "Approved":
        return cached
    return None


def review_requeue_blocked_message(
    content_hash: Optional[str],
    *,
    new_status: str,
) -> Optional[str]:
    """User-facing reason when a globally approved PDF is sent back to review."""
    if _clean_status(new_status) not in _REVIEW_QUEUE_STATUSES:
        return None
    approved = find_approved_run_by_content_hash(content_hash or "")
    if not approved:
        return None
    who = approved.get("approved_by") or "an approver"
    when = approved.get("approved_at") or ""
    when_s = f" on {when}" if when else ""
    return (
        f"This document was already signed off by {who}{when_s}. "
        "It cannot be submitted for review again."
    )


def find_approved_run_by_content_hash(content_hash: str) -> Optional[dict[str, Any]]:
    """Return any globally approved extract run for a file hash (cross-user canonical)."""
    norm_hash = (content_hash or "").strip().lower()
    if not norm_hash:
        return None

    if fabric_sql.fabric_configured():
        try:
            conn = fabric_sql.connect()
            try:
                from . import fabric_schema

                fabric_schema.ensure_documents_table(conn)
                doc = fabric_schema.get_document_by_hash(conn, norm_hash)
                if doc and _clean_status(doc.get("global_status")) == "Approved" and doc.get("canonical_run_id"):
                    rid = str(doc["canonical_run_id"])
                    cur = conn.cursor()
                    cur.execute(
                        _log_select_sql(conn, top=1) + " WHERE run_id = ?",
                        (rid,),
                    )
                    row = cur.fetchone()
                    if row:
                        cols = [c[0] for c in cur.description]
                        out = _apply_log_envelope(dict(zip(cols, row)))
                        out["document_status"] = "Approved"
                        out["approved_by"] = doc.get("approved_by") or out.get("approved_by")
                        out["approved_at"] = doc.get("approved_at") or out.get("approved_at")
                        cur.close()
                        return out
                    cur.close()

                cur = conn.cursor()
                # Adaptive SELECT — warehouse may not have document_status as a real column.
                sql = (
                    _log_select_sql(conn, top=25)
                    + """
                    WHERE (status IS NULL OR LOWER(status) NOT IN ('error', 'failed', 'cancelled'))
                      AND LOWER(RTRIM(LTRIM(content_hash))) = LOWER(?)
                    ORDER BY extracted_at DESC
                    """
                )
                cur.execute(sql, (norm_hash,))
                cols = [c[0] for c in cur.description]
                for raw in cur.fetchall() or []:
                    row = _apply_log_envelope(dict(zip(cols, raw)))
                    if _clean_status(row.get("document_status")) == "Approved":
                        return row
            finally:
                conn.close()
        except Exception as err:
            logger.warning("Fabric find_approved_run_by_content_hash error: %s", err)

    try:
        for row in list_done_extracts(limit=500):
            if _row_content_hash(row) != norm_hash:
                continue
            if _clean_status(row.get("document_status")) == "Approved":
                return row
    except Exception:
        pass

    with _cache_lock:
        for rec in reversed(list(_MEM_CACHE.values())):
            if _row_content_hash(rec) != norm_hash:
                continue
            if _clean_status(rec.get("document_status")) == "Approved":
                return rec

    return None


def supersede_duplicate_runs(
    canonical_run_id: str,
    *,
    content_hash: Optional[str],
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
) -> int:
    """Archive duplicate pending/in-review rows for the same user+file after approval."""
    norm_hash = (content_hash or "").strip().lower()
    canon = (canonical_run_id or "").strip()
    if not norm_hash or not canon:
        return 0
    n = 0
    try:
        rows = list_done_extracts(limit=200, user_id=user_id, user_email=user_email)
    except Exception:
        return 0
    for row in rows:
        rid = str(row.get("run_id") or "").strip()
        if not rid or rid == canon:
            continue
        rh = str(row.get("content_hash") or "").strip().lower()
        if rh != norm_hash:
            continue
        status = _clean_status(row.get("document_status"))
        if status in {"Approved", "Superseded", "Rejected"}:
            continue
        try:
            update_fabric_review_state(
                rid,
                document_status="Superseded",
                rejection_notes=f"Superseded by approved run {canon}",
                user_id=user_id,
                user_email=user_email,
            )
            n += 1
        except Exception:
            pass
    return n


def find_done_run(
    *,
    content_hash: str,
    drive_item_id: Optional[str] = None,
    filename: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    clean_hash = (content_hash or "").strip().lower()
    clean_fn = (filename or "").strip()
    clean_drive_id = (drive_item_id or "").strip()

    # 1. Primary: Search Microsoft Fabric SQL Lakehouse
    if fabric_sql.fabric_configured():
        try:
            conn = fabric_sql.connect()
            try:
                cur = conn.cursor()
                select_sql = _log_select_sql(conn, top=1)

                # 1a. Match by content_hash (exact, lowercase/trimmed)
                if clean_hash:
                    cur.execute(
                        select_sql
                        + """
                        WHERE (status IS NULL OR LOWER(status) NOT IN ('error', 'failed', 'cancelled'))
                          AND LOWER(RTRIM(LTRIM(content_hash))) = LOWER(?)
                        ORDER BY extracted_at DESC
                        """,
                        (clean_hash,),
                    )
                    row = cur.fetchone()
                    if row:
                        cols = [c[0] for c in cur.description]
                        return _apply_log_envelope(dict(zip(cols, row)))

                # 1b. Match by drive_item_id
                if clean_drive_id and clean_drive_id != "LOCAL_UPLOAD":
                    cur.execute(
                        select_sql
                        + """
                        WHERE (status IS NULL OR LOWER(status) NOT IN ('error', 'failed', 'cancelled'))
                          AND drive_item_id = ?
                        ORDER BY extracted_at DESC
                        """,
                        (clean_drive_id,),
                    )
                    row = cur.fetchone()
                    if row:
                        cols = [c[0] for c in cur.description]
                        return _apply_log_envelope(dict(zip(cols, row)))

                # 1c. Match by filename
                if clean_fn:
                    cur.execute(
                        select_sql
                        + """
                        WHERE (status IS NULL OR LOWER(status) NOT IN ('error', 'failed', 'cancelled'))
                          AND LOWER(RTRIM(LTRIM(filename))) = LOWER(?)
                        ORDER BY extracted_at DESC
                        """,
                        (clean_fn,),
                    )
                    row = cur.fetchone()
                    if row:
                        cols = [c[0] for c in cur.description]
                        return _apply_log_envelope(dict(zip(cols, row)))
            finally:
                conn.close()
        except Exception as err:
            logger.warning("Fabric find_done_run error: %s", err)

    # 2. Resilient fallback: Search unified persistent store
    cached = _find_in_cache(content_hash=clean_hash, filename=clean_fn, drive_item_id=clean_drive_id)
    if cached:
        return cached

    return None


def _norm_email(val: Any) -> str:
    return str(val or "").strip().lower()


def _emails_equal(a: Any, b: Any) -> bool:
    left, right = _norm_email(a), _norm_email(b)
    return bool(left and right and left == right)


def resolve_assigned_approver(row: dict[str, Any]) -> str:
    """Assigned approver on the log row, or the document owner's user-store mapping."""
    direct = _norm_email(row.get("assigned_approver"))
    if direct:
        return direct
    owner = _norm_email(row.get("submitted_by") or row.get("user_email"))
    if not owner:
        return ""
    try:
        from ..auth import store as auth_store
        rec = auth_store.find_by_email(owner)
        if rec:
            return _norm_email(rec.get("assigned_approver"))
    except Exception:
        pass
    return ""


def user_owns_extract(row: dict[str, Any], user: Any) -> bool:
    if not user or not row:
        return False
    uid = str(getattr(user, "id", None) or "").strip()
    email = _norm_email(getattr(user, "email", None))
    r_uid = str(row.get("user_id") or "").strip()
    if uid and r_uid and uid == r_uid:
        return True
    for key in ("user_email", "submitted_by"):
        if email and _emails_equal(row.get(key), email):
            return True
    return False


def user_can_view_extract(row: dict[str, Any], user: Any) -> bool:
    """Owner, assigned approver, or admin. Unassigned approvers cannot view other tenants."""
    if not row:
        return False
    if not user:
        return True
    role = str(getattr(user, "role", None) or "").strip().lower()
    if role == "admin":
        return True
    if user_owns_extract(row, user):
        return True
    if role == "approver":
        assigned = resolve_assigned_approver(row)
        return bool(assigned and _emails_equal(assigned, getattr(user, "email", None)))
    return False


def user_can_sign_off_extract(row: dict[str, Any], user: Any) -> bool:
    if not user or not row:
        return False
    role = str(getattr(user, "role", None) or "").strip().lower()
    if role == "admin":
        return True
    if role != "approver":
        return False
    assigned = resolve_assigned_approver(row)
    return bool(assigned and _emails_equal(assigned, getattr(user, "email", None)))


def _row_matches_user(
    row: dict[str, Any],
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
) -> bool:
    uid = str(user_id or "").strip()
    uemail = _norm_email(user_email)
    if not uid and not uemail:
        return True

    r_uid = str(row.get("user_id") or "").strip()
    r_email = _norm_email(row.get("user_email"))
    r_appr = _norm_email(row.get("approved_by"))
    r_assign = _norm_email(row.get("assigned_approver"))
    r_sub = _norm_email(row.get("submitted_by"))
    r_mod = _norm_email(row.get("last_modified_by"))
    r_engine = str(row.get("engine") or "").lower()
    r_err = str(row.get("error") or "")

    if uid and r_uid and r_uid == uid:
        return True

    if uemail:
        if r_email and r_email == uemail:
            return True
        if r_sub and r_sub == uemail:
            return True
        if r_appr and r_appr == uemail:
            return True
        if r_assign and r_assign == uemail:
            return True
        if r_mod and r_mod == uemail:
            return True
        if f"[user:{uemail}]" in r_engine:
            return True

    if r_err.startswith("{") and r_err.endswith("}"):
        try:
            env = json.loads(r_err)
            env_uid = str(env.get("user_id") or "").strip()
            if uid and env_uid and env_uid == uid:
                return True
            if uemail:
                for key in ("user_email", "submitted_by", "approved_by", "assigned_approver", "last_modified_by"):
                    if _norm_email(env.get(key)) == uemail:
                        return True
        except Exception:
            pass

    return False


def _clean_status(raw_val: Any) -> str:
    s = str(raw_val or "").strip()
    if s in {"Draft", "Pending Review", "In Review", "Pending Sign-Off", "Approved", "Rejected", "Needs Revision", "Superseded"}:
        return s
    if s.lower() in {"approved", "signed off", "signed-off"}:
        return "Approved"
    if s.lower() in {"rejected", "reject"}:
        return "Rejected"
    if s.lower() in {"in review", "in-review", "reviewing"}:
        return "In Review"
    if s.lower() in {"pending sign-off", "pending sign off", "pending-sign-off"}:
        return "Pending Sign-Off"
    if s.lower() in {"needs revision", "revision"}:
        return "Needs Revision"
    return "Pending Review"


def _apply_log_envelope(row: dict[str, Any]) -> dict[str, Any]:
    """Flatten the JSON envelope stored in `envelope_json` / legacy `error` onto the log row."""
    if not row:
        return row
    for k, v in list(row.items()):
        lk = str(k).lower()
        if lk not in row:
            row[lk] = v
    for key in ("envelope_json", "error"):
        raw_env = str(row.get(key) or "").strip()
        if raw_env.startswith("{") and raw_env.endswith("}"):
            try:
                env = json.loads(raw_env)
                if not isinstance(env, dict):
                    continue
                row["document_status"] = env.get("document_status") or row.get("document_status") or "Pending Review"
                row["approved_by"] = env.get("approved_by") or row.get("approved_by")
                row["approved_at"] = env.get("approved_at") or row.get("approved_at")
                row["submitted_by"] = env.get("submitted_by") or row.get("submitted_by")
                row["assigned_approver"] = env.get("assigned_approver") or row.get("assigned_approver")
                row["user_id"] = env.get("user_id") or row.get("user_id")
                row["user_email"] = env.get("user_email") or row.get("user_email")
                row["user_role"] = env.get("user_role") or row.get("user_role")
                row["rejection_notes"] = env.get("rejection_notes") or row.get("rejection_notes")
                if env.get("doc_metadata"):
                    row["doc_metadata"] = env.get("doc_metadata")
                # Prefer first valid JSON blob; envelope_json is Phase B+ canonical.
                if key == "envelope_json" or not str(row.get("envelope_json") or "").startswith("{"):
                    row["_envelope"] = env
                break
            except Exception:
                pass
    if not row.get("document_status"):
        row["document_status"] = "Pending Review"
    else:
        row["document_status"] = _clean_status(row.get("document_status"))
    return row


def _safe_doc_metadata(d: Any) -> Optional[DocumentMetadata]:
    if not isinstance(d, dict):
        return None
    from ..models import DocumentMetadata
    return DocumentMetadata(
        title=str(d.get("title") or "NA"),
        oem_manufacturer=str(d.get("oem_manufacturer") or "NA"),
        equipment_model=str(d.get("equipment_model") or "NA"),
        equipment_type=str(d.get("equipment_type") or "NA"),
        document_version=str(d.get("document_version") or "NA"),
        publication_date=str(d.get("publication_date") or "NA"),
    )


def list_done_extracts(
    *,
    limit: int = 100,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
) -> list[dict[str, Any]]:
    top = max(1, min(int(limit or 100), 500))
    uid = str(user_id or "").strip()
    uemail = str(user_email or "").strip().lower()

    # Fast in-memory TTL cache lookup
    cache_key = f"{top}:{uid}:{uemail}"
    now_ts = time.time()
    with _cache_lock:
        if cache_key in _LIST_EXTRACTS_CACHE:
            c_time, c_rows = _LIST_EXTRACTS_CACHE[cache_key]
            if now_ts - c_time < _LIST_EXTRACTS_TTL:
                return [dict(r) for r in c_rows]

    rows: list[dict[str, Any]] = []

    # 1. Primary: Query Microsoft Fabric SQL Lakehouse
    try:
        conn = fabric_sql.connect()
        try:
            cur = conn.cursor()
            known_cols = fabric_sql._get_table_columns(conn, "Tbl_PM_Extraction_logs")

            # Project lightweight summary columns. Prefer envelope_json (Phase B+);
            # legacy `error` may still hold an envelope on older rows.
            proj_cols = [
                "run_id", "filename", "content_hash", "drive_item_id", "etag",
                "status", "overall_score", "maintenance_count", "spare_parts_count",
                "troubleshooting_count", "engine", "parse_strategy", "extracted_at",
                "doc_title", "oem_manufacturer", "equipment_model", "equipment_type",
                "document_version", "publication_date", "document_status",
                "approved_by", "approved_at", "assigned_approver", "submitted_by",
                "rejection_notes", "user_id", "user_email", "user_role", "duration_ms",
                "envelope_json", "error",
            ]
            valid_proj = [c for c in proj_cols if not known_cols or c in known_cols]
            if valid_proj and "error" not in valid_proj and (not known_cols or "error" in known_cols):
                valid_proj.append("error")
            if valid_proj and "envelope_json" not in valid_proj and (not known_cols or "envelope_json" in known_cols):
                valid_proj.append("envelope_json")
            cols_sql = ", ".join(valid_proj) if valid_proj else "*"

            if uid or uemail:
                clauses: list[str] = []
                params: list[Any] = []

                if uid and (not known_cols or "user_id" in known_cols):
                    clauses.append("user_id = ?")
                    params.append(uid)
                if uemail and (not known_cols or "user_email" in known_cols):
                    clauses.append("LOWER(user_email) = ?")
                    params.append(uemail)
                if uemail and "submitted_by" in known_cols:
                    clauses.append("LOWER(submitted_by) = ?")
                    params.append(uemail)
                if uemail and (not known_cols or "approved_by" in known_cols):
                    clauses.append("LOWER(approved_by) = ?")
                    params.append(uemail)
                if uemail and "assigned_approver" in known_cols:
                    clauses.append("LOWER(assigned_approver) = ?")
                    params.append(uemail)
                if uemail and (not known_cols or "engine" in known_cols):
                    clauses.append("engine LIKE ?")
                    params.append(f"%{uemail}%")
                # Envelope identity (assigned_approver, submitted_by, user_email)
                if uid and (not known_cols or "error" in known_cols):
                    clauses.append("error LIKE ?")
                    params.append(f'%"{uid}"%')
                if uemail and (not known_cols or "error" in known_cols):
                    clauses.append("error LIKE ?")
                    params.append(f'%"{uemail}"%')

                where_sql = f"WHERE ({' OR '.join(clauses)})" if clauses else ""
                sql = f"SELECT TOP {top} {cols_sql} FROM Tbl_PM_Extraction_logs {where_sql} ORDER BY extracted_at DESC"
                cur.execute(sql, tuple(params))
            else:
                cur.execute(
                    f"SELECT TOP {top} {cols_sql} FROM Tbl_PM_Extraction_logs ORDER BY extracted_at DESC"
                )

            cols = [c[0] for c in cur.description]
            f_rows = [_apply_log_envelope(dict(zip(cols, r))) for r in cur.fetchall()]
            rows.extend(f_rows)
        finally:
            conn.close()
    except Exception as err:
        logger.debug("Fabric list_done_extracts notice: %s", err)

    # 2. Resilient: Merge and update latest in-memory / local persistent cache records
    known_run_ids = {str(r.get("run_id") or "") for r in rows if r.get("run_id")}
    candidates: list[dict[str, Any]] = []
    with _cache_lock:
        candidates.extend(list(_MEM_CACHE.values()))
        if CACHE_DIR.exists():
            for p in CACHE_DIR.glob("*.json"):
                if p.stem not in _MEM_CACHE:
                    try:
                        d = json.loads(p.read_text(encoding="utf-8"))
                        _MEM_CACHE[p.stem] = d
                        candidates.append(d)
                    except Exception:
                        pass

    fresh_rows: list[dict[str, Any]] = []
    for c in reversed(candidates):
        cid = str(c.get("run_id") or "")
        if cid:
            existing = next((r for r in rows if str(r.get("run_id") or "") == cid), None)
            if existing is not None:
                existing.update(c)
                _apply_log_envelope(existing)
            elif cid not in known_run_ids:
                fresh_rows.append(_apply_log_envelope(dict(c)))
                known_run_ids.add(cid)

    rows = fresh_rows + rows

    # Apply strict in-memory user verification if scoped
    if uid or uemail:
        rows = [r for r in rows if _row_matches_user(r, user_id=uid, user_email=uemail)]

    final_result = rows[:top]
    with _cache_lock:
        _LIST_EXTRACTS_CACHE[cache_key] = (now_ts, [dict(r) for r in final_result])

    return final_result


def get_done_run(run_id: str) -> Optional[dict[str, Any]]:
    run_id = (run_id or "").strip()
    if not run_id:
        return None
    try:
        conn = fabric_sql.connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT *
                FROM Tbl_PM_Extraction_logs
                WHERE run_id = ?
                """,
                (run_id,),
            )
            row = cur.fetchone()
            if row:
                cols = [c[0] for c in cur.description]
                return _apply_log_envelope(dict(zip(cols, row)))
        finally:
            conn.close()
    except Exception as err:
        logger.debug("Fabric get_done_run notice: %s", err)

    cached = _load_from_cache(run_id)
    return _apply_log_envelope(cached) if cached else None


def _fetch_table(conn: Any, table: str, run_id: str) -> list[dict[str, Any]]:
    if table not in fabric_sql.ALLOWED_TABLES:
        raise ValueError(f"Disallowed table: {table}")
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM {table} WHERE run_id = ?", (run_id,))
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.close()
    return rows


def _extract_review_tags_from_text(text: str) -> dict[str, Any]:
    """Helper to extract review tags from text fields like remarks or quality_reasons."""
    res = {}
    if not text:
        return res
    t = str(text)
    if "[STATUS:" in t:
        try:
            status_part = t.split("[STATUS:", 1)[1].split("]", 1)[0].strip()
            if status_part in {"Approved", "Rejected", "Pending Review", "Draft", "Needs Revision"}:
                res["status"] = status_part
        except Exception:
            pass
    elif t.startswith("[Approved]"):
        res["status"] = "Approved"
    elif t.startswith("[Rejected]"):
        res["status"] = "Rejected"
    return res


def load_extract_from_fabric(
    run_id: str,
    *,
    filename: str,
    overall_score: float | None = None,
    cached_record: Optional[dict[str, Any]] = None,
) -> ExtractResponse:
    from ..models import DocumentMetadata

    spares_raw: list[dict[str, Any]] = []
    maint_raw: list[dict[str, Any]] = []
    trouble_raw: list[dict[str, Any]] = []
    log_meta: dict[str, Any] = dict(cached_record) if cached_record else {}

    cached_fallback = _load_from_cache(run_id)
    if cached_fallback:
        if not log_meta:
            log_meta = dict(cached_fallback)
        if not spares_raw and cached_fallback.get("spare_parts"):
            spares_raw = cached_fallback["spare_parts"]
        if not maint_raw and cached_fallback.get("maintenance"):
            maint_raw = cached_fallback["maintenance"]
        if not trouble_raw and cached_fallback.get("troubleshooting"):
            trouble_raw = cached_fallback["troubleshooting"]
    if cached_fallback:
        if not log_meta:
            log_meta = cached_fallback
        if not spares_raw and cached_fallback.get("spare_parts"):
            spares_raw = cached_fallback["spare_parts"]
        if not maint_raw and cached_fallback.get("maintenance"):
            maint_raw = cached_fallback["maintenance"]
        if not trouble_raw and cached_fallback.get("troubleshooting"):
            trouble_raw = cached_fallback["troubleshooting"]

    # Check for JSON envelope in envelope_json / legacy error and merge with cached_fallback
    envelope = {}
    if cached_fallback:
        envelope.update(cached_fallback)
    for key in ("envelope_json", "error"):
        raw_env = str(log_meta.get(key) or "").strip()
        if raw_env.startswith("{") and raw_env.endswith("}"):
            try:
                parsed = json.loads(raw_env)
                if isinstance(parsed, dict):
                    envelope.update(parsed)
                    break
            except Exception:
                pass

    # Phase C/D: hydrate payloads table when present
    if fabric_sql.fabric_configured() and (
        not envelope.get("raw_payload") or not envelope.get("edited_payload")
        or envelope.get("payload_in_row_tables") or envelope.get("_slim")
    ):
        try:
            conn = fabric_sql.connect()
            try:
                from . import fabric_schema

                payload_row = fabric_schema.load_extract_payloads(conn, run_id)
                if payload_row:
                    if payload_row.get("raw_payload") and not envelope.get("raw_payload"):
                        envelope["raw_payload"] = payload_row["raw_payload"]
                    if payload_row.get("edited_payload"):
                        envelope["edited_payload"] = payload_row["edited_payload"]
                        for coll in ("spare_parts", "maintenance", "troubleshooting"):
                            edited = payload_row["edited_payload"]
                            if isinstance(edited, dict) and isinstance(edited.get(coll), list):
                                envelope[coll] = edited[coll]
            finally:
                conn.close()
        except Exception as err:
            logger.debug("Fabric payload load notice: %s", err)

    # If envelope is empty and Fabric SQL is configured, fetch from Lakehouse
    if not (envelope.get("spare_parts") or envelope.get("raw_payload") or spares_raw or maint_raw or trouble_raw):
        try:
            conn = fabric_sql.connect()
            try:
                spares_raw = _fetch_table(conn, "Tbl_PM_Spare_Parts", run_id)
                maint_raw = _fetch_table(conn, "Tbl_PM_Maintenance", run_id)
                trouble_raw = _fetch_table(conn, "Tbl_PM_Troubleshooting", run_id)
            finally:
                conn.close()
        except Exception as err:
            logger.debug("Fabric load_extract_from_fabric db notice: %s", err)

        if not log_meta:
            log_meta = get_done_run(run_id) or {}
            for key in ("envelope_json", "error"):
                raw_env = str(log_meta.get(key) or "").strip()
                if raw_env.startswith("{") and raw_env.endswith("}"):
                    try:
                        parsed = json.loads(raw_env)
                        if isinstance(parsed, dict):
                            envelope.update(parsed)
                            break
                    except Exception:
                        pass

    # Prioritize edited_payload first, then working arrays, then Lakehouse relational rows, then raw_payload
    edited_p = envelope.get("edited_payload") if isinstance(envelope.get("edited_payload"), dict) else {}
    raw_p = envelope.get("raw_payload") if isinstance(envelope.get("raw_payload"), dict) else {}

    spares_source = (
        edited_p.get("spare_parts")
        if (isinstance(edited_p.get("spare_parts"), list) and len(edited_p.get("spare_parts")) > 0)
        else (
            envelope.get("spare_parts")
            if (isinstance(envelope.get("spare_parts"), list) and len(envelope.get("spare_parts")) > 0)
            else (
                spares_raw
                if spares_raw
                else (
                    raw_p.get("spare_parts")
                    if isinstance(raw_p.get("spare_parts"), list)
                    else []
                )
            )
        )
    )
    maint_source = (
        edited_p.get("maintenance")
        if (isinstance(edited_p.get("maintenance"), list) and len(edited_p.get("maintenance")) > 0)
        else (
            envelope.get("maintenance")
            if (isinstance(envelope.get("maintenance"), list) and len(envelope.get("maintenance")) > 0)
            else (
                maint_raw
                if maint_raw
                else (
                    raw_p.get("maintenance")
                    if isinstance(raw_p.get("maintenance"), list)
                    else []
                )
            )
        )
    )
    trouble_source = (
        edited_p.get("troubleshooting")
        if (isinstance(edited_p.get("troubleshooting"), list) and len(edited_p.get("troubleshooting")) > 0)
        else (
            envelope.get("troubleshooting")
            if (isinstance(envelope.get("troubleshooting"), list) and len(envelope.get("troubleshooting")) > 0)
            else (
                trouble_raw
                if trouble_raw
                else (
                    raw_p.get("troubleshooting")
                    if isinstance(raw_p.get("troubleshooting"), list)
                    else []
                )
            )
        )
    )

    spares: list[SparePartRow] = []
    for d in spares_source:
        if isinstance(d, dict):
            q = _quality_from_row(d) if ("fields_filled_score" in d or "grounding_available" in d) else d.get("quality")
            rev_tags = _extract_review_tags_from_text(d.get("quality_reasons") or "")
            row_status = _clean_status(d.get("status") or rev_tags.get("status"))
            spares.append(
                SparePartRow(
                    id=int(d.get("id") or len(spares) + 1),
                    equipment_title=str(d.get("equipment_title") or "NA"),
                    subsystem_location=str(d.get("subsystem_location") or "NA"),
                    item_no=str(d.get("item_no") or "NA"),
                    part_name=str(d.get("part_name") or "NA"),
                    part_number_code=str(d.get("part_number_code") or "NA"),
                    drawing_model_no=str(d.get("drawing_model_no") or "NA"),
                    oem_standard_body=str(d.get("oem_standard_body") or "NA"),
                    part_categorization=str(d.get("part_categorization") or "NA"),
                    quantity=str(d.get("quantity") or "NA"),
                    recommended_stock_qty=str(d.get("recommended_stock_qty") or "NA"),
                    warranty_period=str(d.get("warranty_period") or "NA"),
                    frequency_of_use=str(d.get("frequency_of_use") or "NA"),
                    page=d.get("page") or "NA",
                    pdf_order=int(d.get("pdf_order") or 0),
                    confidence=float(d.get("confidence") or 1.0),
                    quality=q,
                    status=row_status,
                    reviewed_by=d.get("reviewed_by") or envelope.get("approved_by"),
                    reviewed_at=str(d.get("reviewed_at")) if d.get("reviewed_at") else envelope.get("approved_at"),
                    rejection_reason=d.get("rejection_reason"),
                )
            )
        elif isinstance(d, SparePartRow):
            spares.append(d)

    for i, r in enumerate(spares, 1):
        r.id = i

    maint: list[MaintenanceRow] = []
    for d in maint_source:
        if isinstance(d, dict):
            q = _quality_from_row(d) if ("fields_filled_score" in d or "grounding_available" in d) else d.get("quality")
            rev_tags = _extract_review_tags_from_text(d.get("remarks") or "")
            row_status = _clean_status(d.get("status") or rev_tags.get("status"))
            maint.append(
                MaintenanceRow(
                    id=int(d.get("id") or len(maint) + 1),
                    equipment_title=str(d.get("equipment_title") or "NA"),
                    subsystem_component=str(d.get("subsystem_component") or "NA"),
                    maintenance_routine=str(d.get("maintenance_routine") or "NA"),
                    checks_instructions=str(d.get("checks_instructions") or "NA"),
                    date=str(d.get("date") or "NA"),
                    maintenance_work_description=str(d.get("maintenance_work_description") or "NA"),
                    parts_renewed=str(d.get("parts_renewed") or "NA"),
                    attended_by=str(d.get("attended_by") or "NA"),
                    remarks=str(d.get("remarks") or "NA"),
                    page=d.get("page") or "NA",
                    pdf_order=int(d.get("pdf_order") or 0),
                    confidence=float(d.get("confidence") or 1.0),
                    quality=q,
                    status=row_status,
                    reviewed_by=d.get("reviewed_by") or (d.get("attended_by") if d.get("attended_by") != "NA" else envelope.get("approved_by")),
                    reviewed_at=str(d.get("reviewed_at")) if d.get("reviewed_at") else envelope.get("approved_at"),
                    rejection_reason=d.get("rejection_reason"),
                )
            )
        elif isinstance(d, MaintenanceRow):
            maint.append(d)

    for i, r in enumerate(maint, 1):
        r.id = i

    trouble: list[TroubleshootingRow] = []
    for d in trouble_source:
        if isinstance(d, dict):
            q = _quality_from_row(d) if ("fields_filled_score" in d or "grounding_available" in d) else d.get("quality")
            rev_tags = _extract_review_tags_from_text(d.get("quality_reasons") or "")
            row_status = _clean_status(d.get("status") or rev_tags.get("status"))
            trouble.append(
                TroubleshootingRow(
                    id=int(d.get("id") or len(trouble) + 1),
                    equipment_title=str(d.get("equipment_title") or "NA"),
                    subsystem_component=str(d.get("subsystem_component") or "NA"),
                    problem=str(d.get("problem") or "NA"),
                    root_cause_solution=str(d.get("root_cause_solution") or "NA"),
                    page=d.get("page") or "NA",
                    pdf_order=int(d.get("pdf_order") or 0),
                    confidence=float(d.get("confidence") or 1.0),
                    quality=q,
                    status=row_status,
                    reviewed_by=d.get("reviewed_by") or envelope.get("approved_by"),
                    reviewed_at=str(d.get("reviewed_at")) if d.get("reviewed_at") else envelope.get("approved_at"),
                    rejection_reason=d.get("rejection_reason"),
                )
            )
        elif isinstance(d, TroubleshootingRow):
            trouble.append(d)

    for i, r in enumerate(trouble, 1):
        r.id = i

    score = float(overall_score) if overall_score is not None else (
        float(log_meta.get("overall_score") or 100.0) if (spares or maint or trouble) else 0.0
    )

    # Reconstruct doc metadata prioritizing edited_payload, then envelope, then database columns
    doc_meta = None
    meta_dict = (
        (envelope.get("edited_payload") and isinstance(envelope["edited_payload"].get("doc_metadata"), dict) and envelope["edited_payload"]["doc_metadata"])
        or (isinstance(envelope.get("doc_metadata"), dict) and envelope["doc_metadata"])
        or {}
    )
    doc_title = meta_dict.get("title") or envelope.get("doc_title") or log_meta.get("doc_title")
    doc_oem = meta_dict.get("oem_manufacturer") or envelope.get("oem_manufacturer") or log_meta.get("oem_manufacturer")
    doc_model = meta_dict.get("equipment_model") or envelope.get("equipment_model") or log_meta.get("equipment_model")
    doc_type = meta_dict.get("equipment_type") or envelope.get("equipment_type") or log_meta.get("equipment_type")
    doc_ver = meta_dict.get("document_version") or envelope.get("document_version") or log_meta.get("document_version") or "NA"
    doc_date = meta_dict.get("publication_date") or envelope.get("publication_date") or log_meta.get("publication_date") or "NA"

    if doc_title or doc_oem or doc_model or doc_type:
        doc_meta = DocumentMetadata(
            title=str(doc_title or "NA"),
            oem_manufacturer=str(doc_oem or "NA"),
            equipment_model=str(doc_model or "NA"),
            equipment_type=str(doc_type or "NA"),
            document_version=str(doc_ver or "NA"),
            publication_date=str(doc_date or "NA"),
        )

    doc_status = _clean_status(log_meta.get("document_status") or envelope.get("document_status") or "Pending Review")
    approved_by = log_meta.get("approved_by") or envelope.get("approved_by")
    approved_at = str(log_meta.get("approved_at") or envelope.get("approved_at") or "") or None
    assigned_approver = log_meta.get("assigned_approver") or envelope.get("assigned_approver")
    submitted_by = log_meta.get("submitted_by") or envelope.get("submitted_by") or log_meta.get("user_email") or envelope.get("user_email")
    rejection_reason = log_meta.get("rejection_notes") or envelope.get("rejection_notes")
    resolved_filename = str(envelope.get("filename") or log_meta.get("filename") or filename or "document.pdf")

    # If the whole document is approved, ensure all non-rejected rows reflect Approved status
    if doc_status == "Approved":
        for r in maint + spares + trouble:
            if getattr(r, "status", None) != "Rejected":
                r.status = "Approved"
                r.reviewed_by = r.reviewed_by or approved_by
                r.reviewed_at = r.reviewed_at or approved_at

    # Construct baseline snapshot from raw_payload if available
    baseline_obj = None
    raw_p = envelope.get("raw_payload")
    if isinstance(raw_p, dict) and (raw_p.get("spare_parts") or raw_p.get("maintenance") or raw_p.get("troubleshooting") or raw_p.get("doc_metadata")):
        def _dict_to_sp(d, idx):
            q = _quality_from_row(d) if ("fields_filled_score" in d or "grounding_available" in d) else d.get("quality")
            return SparePartRow(
                id=int(d.get("id") or idx),
                equipment_title=str(d.get("equipment_title") or "NA"),
                subsystem_location=str(d.get("subsystem_location") or "NA"),
                item_no=str(d.get("item_no") or "NA"),
                part_name=str(d.get("part_name") or "NA"),
                part_number_code=str(d.get("part_number_code") or "NA"),
                drawing_model_no=str(d.get("drawing_model_no") or "NA"),
                oem_standard_body=str(d.get("oem_standard_body") or "NA"),
                part_categorization=str(d.get("part_categorization") or "NA"),
                quantity=str(d.get("quantity") or "NA"),
                recommended_stock_qty=str(d.get("recommended_stock_qty") or "NA"),
                warranty_period=str(d.get("warranty_period") or "NA"),
                frequency_of_use=str(d.get("frequency_of_use") or "NA"),
                page=d.get("page") or "NA",
                pdf_order=int(d.get("pdf_order") or idx),
                confidence=float(d.get("confidence") or 1.0),
                quality=q,
                status=_clean_status(d.get("status")),
            )

        def _dict_to_mt(d, idx):
            q = _quality_from_row(d) if ("fields_filled_score" in d or "grounding_available" in d) else d.get("quality")
            return MaintenanceRow(
                id=int(d.get("id") or idx),
                equipment_title=str(d.get("equipment_title") or "NA"),
                subsystem_component=str(d.get("subsystem_component") or "NA"),
                maintenance_routine=str(d.get("maintenance_routine") or "NA"),
                checks_instructions=str(d.get("checks_instructions") or "NA"),
                date=str(d.get("date") or "NA"),
                maintenance_work_description=str(d.get("maintenance_work_description") or "NA"),
                parts_renewed=str(d.get("parts_renewed") or "NA"),
                attended_by=str(d.get("attended_by") or "NA"),
                remarks=str(d.get("remarks") or "NA"),
                page=d.get("page") or "NA",
                pdf_order=int(d.get("pdf_order") or idx),
                confidence=float(d.get("confidence") or 1.0),
                quality=q,
                status=_clean_status(d.get("status")),
            )

        def _dict_to_tr(d, idx):
            q = _quality_from_row(d) if ("fields_filled_score" in d or "grounding_available" in d) else d.get("quality")
            return TroubleshootingRow(
                id=int(d.get("id") or idx),
                equipment_title=str(d.get("equipment_title") or "NA"),
                subsystem_component=str(d.get("subsystem_component") or "NA"),
                problem=str(d.get("problem") or "NA"),
                root_cause_solution=str(d.get("root_cause_solution") or "NA"),
                page=d.get("page") or "NA",
                pdf_order=int(d.get("pdf_order") or idx),
                confidence=float(d.get("confidence") or 1.0),
                quality=q,
                status=_clean_status(d.get("status")),
            )

        b_spares = [_dict_to_sp(d, i+1) for i, d in enumerate(raw_p.get("spare_parts") or [])]
        b_maint = [_dict_to_mt(d, i+1) for i, d in enumerate(raw_p.get("maintenance") or [])]
        b_trouble = [_dict_to_tr(d, i+1) for i, d in enumerate(raw_p.get("troubleshooting") or [])]
        b_doc_meta = _safe_doc_metadata(raw_p.get("doc_metadata"))

        from ..models import BaselineExtraction
        baseline_obj = BaselineExtraction(
            spare_parts=b_spares,
            maintenance=b_maint,
            troubleshooting=b_trouble,
            doc_metadata=b_doc_meta,
            extracted_at=str(raw_p.get("extracted_at") or ""),
        )

    # Check if there are diffs between baseline and current working records
    has_diff = False
    if baseline_obj:
        if len(spares) != len(baseline_obj.spare_parts) or len(maint) != len(baseline_obj.maintenance) or len(trouble) != len(baseline_obj.troubleshooting):
            has_diff = True
        else:
            for a, b in zip(spares, baseline_obj.spare_parts):
                if (a.part_name != b.part_name or a.part_number_code != b.part_number_code or 
                    a.drawing_model_no != b.drawing_model_no or a.quantity != b.quantity or
                    a.part_categorization != b.part_categorization or a.equipment_title != b.equipment_title or
                    a.subsystem_location != b.subsystem_location or a.item_no != b.item_no):
                    has_diff = True
                    break
            if not has_diff:
                for a, b in zip(maint, baseline_obj.maintenance):
                    if (a.checks_instructions != b.checks_instructions or a.maintenance_work_description != b.maintenance_work_description or
                        a.maintenance_routine != b.maintenance_routine or a.parts_renewed != b.parts_renewed or
                        a.equipment_title != b.equipment_title or a.subsystem_component != b.subsystem_component):
                        has_diff = True
                        break
        if not has_diff and baseline_obj.doc_metadata and doc_meta:
            bm = baseline_obj.doc_metadata
            if (doc_meta.title != bm.title or doc_meta.oem_manufacturer != bm.oem_manufacturer or
                doc_meta.equipment_model != bm.equipment_model or doc_meta.document_version != bm.document_version or
                doc_meta.publication_date != bm.publication_date or doc_meta.equipment_type != bm.equipment_type):
                has_diff = True

    return ExtractResponse(
        maintenance=maint,
        spare_parts=spares,
        troubleshooting=trouble,
        pages=[],
        baseline=baseline_obj,
        raw_payload=raw_p if isinstance(raw_p, dict) else None,
        edited_payload=envelope.get("edited_payload") if isinstance(envelope.get("edited_payload"), dict) else None,
        meta=ExtractMeta(
            filename=resolved_filename,
            engine="fabric-cache",
            parse_strategy="cache",
            pages_total=int(log_meta.get("pages_total") or envelope.get("pages_total") or 0),
            pages_processed=int(log_meta.get("pages_processed") or envelope.get("pages_processed") or 0),
            overall_score=score,
            duration_ms=int(log_meta.get("duration_ms") or envelope.get("duration_ms") or 0),
            run_id=run_id,
            content_hash=str(log_meta.get("content_hash") or envelope.get("content_hash") or ""),
            drive_item_id=str(log_meta.get("drive_item_id") or envelope.get("drive_item_id") or "") or None,
            etag=str(log_meta.get("etag") or envelope.get("etag") or "") or None,
            doc_metadata=doc_meta,
            document_status=doc_status,
            approved_by=str(approved_by) if approved_by else None,
            approved_at=str(approved_at) if approved_at else None,
            assigned_approver=str(assigned_approver) if assigned_approver else None,
            submitted_by=str(submitted_by) if submitted_by else None,
            has_diff=has_diff,
        ),
    )


def _qf(row: Any, ai_text: str) -> dict[str, Any]:
    if isinstance(row, dict):
        q = row.get("quality")
        status = row.get("status") or "Pending Review"
        reviewed_by = row.get("reviewed_by")
        reviewed_at = row.get("reviewed_at")
        rejection_reason = row.get("rejection_reason")
        confidence = float(row.get("confidence") or 1.0)
    else:
        q = getattr(row, "quality", None)
        status = getattr(row, "status", "Pending Review") or "Pending Review"
        reviewed_by = getattr(row, "reviewed_by", None)
        reviewed_at = getattr(row, "reviewed_at", None)
        rejection_reason = getattr(row, "rejection_reason", None)
        confidence = float(getattr(row, "confidence", 0) or 1.0)

    # Encode review tags into quality_reasons to ensure persistence even in baseline schema
    reasons_list = []
    if q:
        if isinstance(q, dict):
            reasons_list = list(q.get("reasons") or [])
            completeness = q.get("completeness_score")
            grounding = q.get("grounding_score")
            grounding_avail = q.get("grounding_available")
        else:
            reasons_list = list(q.reasons) if q.reasons else []
            completeness = q.completeness_score
            grounding = q.grounding_score
            grounding_avail = q.grounding_available
    else:
        completeness = None
        grounding = None
        grounding_avail = False

    if status and status != "Pending Review":
        tag = f"[STATUS:{status}]"
        if reviewed_by:
            tag += f"[BY:{reviewed_by}]"
        reasons_list.append(tag)

    return {
        "confidence": confidence,
        "fields_filled_score": float(completeness) if completeness is not None else None,
        "page_match_score": float(grounding) if grounding is not None else None,
        "grounding_available": str(bool(grounding_avail)),
        "quality_reasons": " | ".join(reasons_list) if reasons_list else None,
        "ai_extract_text": ai_text,
        "status": status,
        "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at,
        "rejection_reason": rejection_reason,
    }


def _dump_row_list(rows: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows or []:
        if hasattr(r, "model_dump"):
            out.append(r.model_dump())
        elif isinstance(r, dict):
            out.append(dict(r))
    return out


def _canonical_raw_payload(result: ExtractResponse, now_iso: str, doc_meta_dict: dict[str, Any]) -> dict[str, Any]:
    """Return the immutable AI baseline. Prefer existing raw_payload / baseline over working rows."""
    existing = getattr(result, "raw_payload", None)
    if isinstance(existing, dict) and (
        existing.get("spare_parts") is not None
        or existing.get("maintenance") is not None
        or existing.get("troubleshooting") is not None
        or existing.get("doc_metadata")
    ):
        return {
            "spare_parts": list(existing.get("spare_parts") or []),
            "maintenance": list(existing.get("maintenance") or []),
            "troubleshooting": list(existing.get("troubleshooting") or []),
            "doc_metadata": existing.get("doc_metadata") or doc_meta_dict,
            "extracted_at": existing.get("extracted_at") or now_iso,
        }
    baseline = getattr(result, "baseline", None)
    if baseline is not None:
        b_meta = getattr(baseline, "doc_metadata", None)
        b_meta_dict = b_meta.model_dump() if b_meta and hasattr(b_meta, "model_dump") else (b_meta if isinstance(b_meta, dict) else doc_meta_dict)
        return {
            "spare_parts": _dump_row_list(getattr(baseline, "spare_parts", None)),
            "maintenance": _dump_row_list(getattr(baseline, "maintenance", None)),
            "troubleshooting": _dump_row_list(getattr(baseline, "troubleshooting", None)),
            "doc_metadata": b_meta_dict or doc_meta_dict,
            "extracted_at": getattr(baseline, "extracted_at", None) or now_iso,
        }
    return {
        "spare_parts": _dump_row_list(result.spare_parts),
        "maintenance": _dump_row_list(result.maintenance),
        "troubleshooting": _dump_row_list(result.troubleshooting),
        "doc_metadata": doc_meta_dict,
        "extracted_at": now_iso,
    }


def _clone_rows_pending(rows: Any) -> list:
    cloned = []
    for r in rows or []:
        if hasattr(r, "model_dump"):
            data = r.model_dump()
            data["status"] = "Pending Review"
            data["reviewed_by"] = None
            data["reviewed_at"] = None
            data["rejection_reason"] = None
            try:
                cloned.append(type(r)(**data))
                continue
            except Exception:
                pass
        cloned.append(r)
    return cloned


def _apply_globally_approved_for_new_user(result: ExtractResponse) -> bool:
    """Preserve globally approved state for any uploader (read-only approved view)."""
    if not result or not result.meta:
        return False
    prior_by = getattr(result.meta, "approved_by", None)
    prior_at = getattr(result.meta, "approved_at", None)

    result.meta.document_status = "Approved"
    result.meta.already_approved = True
    result.meta.prior_approved_by = str(prior_by) if prior_by else None
    result.meta.prior_approved_at = str(prior_at) if prior_at else None

    for collection in (result.spare_parts, result.maintenance, result.troubleshooting):
        for r in collection or []:
            if hasattr(r, "status") and _clean_status(getattr(r, "status", None)) != "Rejected":
                r.status = "Approved"
                if prior_by and hasattr(r, "reviewed_by") and not getattr(r, "reviewed_by", None):
                    r.reviewed_by = prior_by
                if prior_at and hasattr(r, "reviewed_at") and not getattr(r, "reviewed_at", None):
                    r.reviewed_at = prior_at

    warnings = list(result.meta.warnings or [])
    who = prior_by or "an approver"
    when = f" on {prior_at}" if prior_at else ""
    notice = (
        f"This document was already signed off by {who}{when}. "
        "Review is complete — no further action required."
    )
    if notice not in warnings:
        warnings.append(notice)
    result.meta.warnings = warnings
    return True


def resolve_global_approved_cache_view(
    result: ExtractResponse,
    log_row: dict[str, Any],
    *,
    keep_run_id: Optional[str] = None,
) -> ExtractResponse:
    """Upgrade a pending duplicate to the globally approved view when one exists."""
    if not result or not result.meta:
        return result
    if _clean_status(getattr(result.meta, "document_status", None)) == "Approved":
        return result

    content_hash = _row_content_hash(log_row)
    if not content_hash:
        return result

    approved_row = find_approved_run_by_content_hash(content_hash)
    if not approved_row or not approved_row.get("run_id"):
        return result

    approved_rid = str(approved_row["run_id"])
    current_rid = str(keep_run_id or log_row.get("run_id") or getattr(result.meta, "run_id", "") or "")
    if approved_rid == current_rid:
        _apply_globally_approved_for_new_user(result)
        return result

    approved_result = load_extract_from_fabric(
        approved_rid,
        filename=str(log_row.get("filename") or "document.pdf"),
        overall_score=(
            float(approved_row["overall_score"])
            if approved_row.get("overall_score") is not None
            else None
        ),
        cached_record=approved_row,
    )
    if not approved_result:
        return result

    _apply_globally_approved_for_new_user(approved_result)
    if current_rid:
        approved_result.meta.run_id = current_rid
    return approved_result


def _isolate_cache_hit_for_new_user(result: ExtractResponse) -> bool:
    """Reset cloned cache hits so Approved status/sign-off metadata is not copied.

    Working rows start from the AI baseline (not another tenant's edits).
    Returns True when the source document was previously signed off.
    """
    if not result or not result.meta:
        return False
    prior_status = _clean_status(getattr(result.meta, "document_status", None))
    if prior_status == "Approved":
        return _apply_globally_approved_for_new_user(result)

    baseline = getattr(result, "baseline", None)
    if baseline is not None:
        result.spare_parts = _clone_rows_pending(getattr(baseline, "spare_parts", None))
        result.maintenance = _clone_rows_pending(getattr(baseline, "maintenance", None))
        result.troubleshooting = _clone_rows_pending(getattr(baseline, "troubleshooting", None))
        if getattr(baseline, "doc_metadata", None):
            result.meta.doc_metadata = baseline.doc_metadata
        result.edited_payload = None
        result.meta.has_diff = False
    else:
        for collection in (result.spare_parts, result.maintenance, result.troubleshooting):
            for r in collection or []:
                if hasattr(r, "status"):
                    r.status = "Pending Review"
                    r.reviewed_by = None
                    r.reviewed_at = None
                    if hasattr(r, "rejection_reason"):
                        r.rejection_reason = None

    result.meta.document_status = "Pending Review"
    result.meta.approved_by = None
    result.meta.approved_at = None
    result.meta.already_approved = False
    result.meta.prior_approved_by = None
    result.meta.prior_approved_at = None
    return False


def save_extract_to_fabric(
    *,
    file_bytes: bytes,
    filename: str,
    result: ExtractResponse,
    content_hash: Optional[str] = None,
    drive_item_id: Optional[str] = None,
    etag: Optional[str] = None,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
    user_role: Optional[str] = None,
    duration_ms: Optional[int] = None,
) -> str:
    content_hash = content_hash or file_sha256(file_bytes)
    run_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    invalidate_extracts_list_cache()

    meta_obj = getattr(result.meta, "doc_metadata", None)
    doc_meta_dict = meta_obj.model_dump() if meta_obj else {}
    doc_status = getattr(result.meta, "document_status", "Pending Review") or "Pending Review"
    approved_by = getattr(result.meta, "approved_by", None)
    approved_at = getattr(result.meta, "approved_at", None)
    rejection_notes = getattr(result.meta, "rejection_notes", None)

    # Determine assigned approver based on user
    assigned_approver = None
    if user_email:
        try:
            from ..auth import store as auth_store
            u_rec = auth_store.find_by_email(user_email)
            if u_rec and u_rec.get("assigned_approver"):
                assigned_approver = u_rec.get("assigned_approver")
        except Exception:
            pass

    # Build canonical JSON baseline snapshot (raw_payload).
    # Preserve the original AI baseline when present; never overwrite it with working edits.
    raw_payload = _canonical_raw_payload(result, now.isoformat(), doc_meta_dict)
    raw_spares = [s.model_dump() for s in result.spare_parts]
    raw_maint = [m.model_dump() for m in result.maintenance]
    raw_trouble = [t.model_dump() for t in result.troubleshooting]

    edited_payload = getattr(result, "edited_payload", None) or {
        "spare_parts": [dict(r) for r in raw_spares],
        "maintenance": [dict(r) for r in raw_maint],
        "troubleshooting": [dict(r) for r in raw_trouble],
        "doc_metadata": doc_meta_dict,
        "last_modified_by": user_email,
        "last_modified_at": now.isoformat(),
    }

    envelope = {
        "_v": 2,
        "run_id": run_id,
        "content_hash": content_hash,
        "drive_item_id": drive_item_id,
        "etag": etag,
        "filename": filename,
        "raw_payload": raw_payload,
        "edited_payload": edited_payload,
        "doc_metadata": doc_meta_dict,
        "document_status": doc_status,
        "approved_by": approved_by,
        "approved_at": approved_at,
        "submitted_by": user_email,
        "assigned_approver": assigned_approver,
        "rejection_notes": rejection_notes,
        "user_id": user_id,
        "user_email": user_email,
        "user_role": user_role,
        "doc_title": getattr(meta_obj, "title", None) if meta_obj else None,
        "oem_manufacturer": getattr(meta_obj, "oem_manufacturer", None) if meta_obj else None,
        "equipment_model": getattr(meta_obj, "equipment_model", None) if meta_obj else None,
        "equipment_type": getattr(meta_obj, "equipment_type", None) if meta_obj else None,
        "document_version": getattr(meta_obj, "document_version", None) if meta_obj else None,
        "publication_date": getattr(meta_obj, "publication_date", None) if meta_obj else None,
        "approved_by": approved_by,
        "approved_at": approved_at,
        "submitted_by": user_email,
        "assigned_approver": assigned_approver,
        "rejection_notes": rejection_notes,
        "user_id": user_id,
        "user_email": user_email,
        "user_role": user_role,
        "duration_ms": duration_ms,
        "pages_total": int(getattr(result.meta, "pages_total", 0) or 0),
        "pages_processed": int(getattr(result.meta, "pages_processed", 0) or 0),
        "spare_parts": [dict(r) for r in raw_spares],
        "maintenance": [dict(r) for r in raw_maint],
        "troubleshooting": [dict(r) for r in raw_trouble],
    }
    # Full envelope for local cache; slim JSON for VARCHAR(2000) Fabric `error` column.
    envelope_json_full = _json_dumps(envelope)
    envelope_json = _envelope_json_for_log_column(envelope)

    raw_engine = str(getattr(result.meta, "engine", "") or "")
    engine_with_user = f"{raw_engine} [user:{user_email}]" if user_email and "[" not in raw_engine else raw_engine
    if len(engine_with_user) > 100:
        engine_with_user = engine_with_user[:97] + "..."

    extract_record = {
        "run_id": run_id,
        "drive_item_id": drive_item_id,
        "etag": etag,
        "content_hash": content_hash,
        "filename": filename,
        "status": "done",
        "document_status": doc_status,
        "overall_score": float(getattr(result.meta, "overall_score", 0) or 0),
        "maintenance_count": len(result.maintenance),
        "spare_parts_count": len(result.spare_parts),
        "troubleshooting_count": len(result.troubleshooting),
        "engine": engine_with_user,
        "parse_strategy": str(getattr(result.meta, "parse_strategy", "") or ""),
        "extracted_at": now.isoformat(),
        "error": envelope_json_full,
        "doc_title": getattr(meta_obj, "title", None) if meta_obj else None,
        "oem_manufacturer": getattr(meta_obj, "oem_manufacturer", None) if meta_obj else None,
        "equipment_model": getattr(meta_obj, "equipment_model", None) if meta_obj else None,
        "equipment_type": getattr(meta_obj, "equipment_type", None) if meta_obj else None,
        "document_version": getattr(meta_obj, "document_version", None) if meta_obj else None,
        "publication_date": getattr(meta_obj, "publication_date", None) if meta_obj else None,
        "approved_by": approved_by,
        "approved_at": approved_at,
        "assigned_approver": assigned_approver,
        "rejection_notes": rejection_notes,
        "user_id": user_id,
        "user_email": user_email,
        "user_role": user_role,
        "duration_ms": duration_ms,
        "raw_payload": raw_payload,
        "edited_payload": edited_payload,
        "spare_parts": [dict(r) for r in raw_spares],
        "maintenance": [dict(r) for r in raw_maint],
        "troubleshooting": [dict(r) for r in raw_trouble],
        "doc_metadata": doc_meta_dict,
    }

    # 1. Store in resilient cache
    _store_in_cache(run_id, extract_record)

    # 2. Attempt Fabric SQL Lakehouse persistence
    if fabric_sql.fabric_configured():
        try:
            conn = fabric_sql.connect()
            try:
                from . import fabric_schema

                fabric_schema.ensure_phase_b_through_e(conn)

                # Phase B+: real columns + envelope_json. Phase D: do not stuff full JSON into error.
                fabric_sql.insert_log(
                    conn,
                    {
                        "run_id": run_id,
                        "drive_item_id": drive_item_id,
                        "etag": etag,
                        "content_hash": content_hash,
                        "filename": filename,
                        "status": "done",
                        "document_status": doc_status,
                        "overall_score": float(getattr(result.meta, "overall_score", 0) or 0),
                        "maintenance_count": len(result.maintenance),
                        "spare_parts_count": len(result.spare_parts),
                        "troubleshooting_count": len(result.troubleshooting),
                        "engine": engine_with_user,
                        "parse_strategy": str(getattr(result.meta, "parse_strategy", "") or ""),
                        "extracted_at": now,
                        "error": None,
                        "envelope_json": envelope_json,
                        "doc_title": getattr(meta_obj, "title", None) if meta_obj else None,
                        "oem_manufacturer": getattr(meta_obj, "oem_manufacturer", None) if meta_obj else None,
                        "equipment_model": getattr(meta_obj, "equipment_model", None) if meta_obj else None,
                        "equipment_type": getattr(meta_obj, "equipment_type", None) if meta_obj else None,
                        "document_version": getattr(meta_obj, "document_version", None) if meta_obj else None,
                        "publication_date": getattr(meta_obj, "publication_date", None) if meta_obj else None,
                        "approved_by": approved_by,
                        "approved_at": approved_at,
                        "assigned_approver": assigned_approver,
                        "submitted_by": user_email,
                        "rejection_notes": rejection_notes,
                        "user_id": user_id,
                        "user_email": user_email,
                        "user_role": user_role,
                        "duration_ms": duration_ms,
                    },
                )

                try:
                    fabric_schema.upsert_extract_payloads(
                        conn,
                        run_id=run_id,
                        content_hash=content_hash,
                        raw_payload=raw_payload,
                        edited_payload=edited_payload,
                    )
                except Exception as pay_err:
                    logger.warning("Fabric payloads upsert notice: %s", pay_err)

                try:
                    global_status = "Approved" if _clean_status(doc_status) == "Approved" else "New"
                    fabric_schema.upsert_document_row(
                        conn,
                        {
                            "content_hash": content_hash,
                            "canonical_run_id": run_id if global_status == "Approved" else None,
                            "filename": filename,
                            "doc_title": getattr(meta_obj, "title", None) if meta_obj else None,
                            "oem_manufacturer": getattr(meta_obj, "oem_manufacturer", None) if meta_obj else None,
                            "equipment_model": getattr(meta_obj, "equipment_model", None) if meta_obj else None,
                            "equipment_type": getattr(meta_obj, "equipment_type", None) if meta_obj else None,
                            "document_version": getattr(meta_obj, "document_version", None) if meta_obj else None,
                            "publication_date": getattr(meta_obj, "publication_date", None) if meta_obj else None,
                            "global_status": global_status,
                            "approved_by": approved_by if global_status == "Approved" else None,
                            "approved_at": str(approved_at) if global_status == "Approved" and approved_at else None,
                        },
                    )
                except Exception as doc_err:
                    logger.warning("Fabric documents upsert notice: %s", doc_err)

                if result.spare_parts:
                    try:
                        spare_rows = []
                        for row in result.spare_parts:
                            d = row.model_dump()
                            d["run_id"] = run_id
                            d["page"] = str(d.get("page") or "")
                            d.update(_qf(row, f"{d.get('part_name','')} {d.get('part_number_code','')} {d.get('drawing_model_no','')}"))
                            spare_rows.append({c: d.get(c) for c in SPARE_COLS})
                        fabric_sql.insert_many(conn, "Tbl_PM_Spare_Parts", SPARE_COLS, spare_rows)
                    except Exception as tbl_err:
                        logger.warning("Fabric spare_parts insert notice: %s", tbl_err)

                if result.maintenance:
                    try:
                        maint_rows = []
                        for row in result.maintenance:
                            d = row.model_dump()
                            d["run_id"] = run_id
                            d["page"] = str(d.get("page") or "")
                            ai = d.get("checks_instructions") or d.get("maintenance_work_description")
                            d.update(_qf(row, str(ai or "")))
                            if not d.get("attended_by") or d.get("attended_by") == "NA":
                                d["attended_by"] = user_email or "NA"
                            r_stat = d.get("status") or "Pending Review"
                            rem = str(d.get("remarks") or "")
                            if r_stat and r_stat != "Pending Review" and f"[{r_stat}]" not in rem:
                                d["remarks"] = f"[{r_stat}] {rem}".strip()
                            maint_rows.append({c: d.get(c) for c in MAINT_COLS})
                        fabric_sql.insert_many(conn, "Tbl_PM_Maintenance", MAINT_COLS, maint_rows)
                    except Exception as tbl_err:
                        logger.warning("Fabric maintenance insert notice: %s", tbl_err)

                if result.troubleshooting:
                    try:
                        trouble_rows = []
                        for row in result.troubleshooting:
                            d = row.model_dump()
                            d["run_id"] = run_id
                            d["page"] = str(d.get("page") or "")
                            d.update(_qf(row, f"{d.get('problem','')} {d.get('root_cause_solution','')}"))
                            trouble_rows.append({c: d.get(c) for c in TROUBLE_COLS})
                        fabric_sql.insert_many(conn, "Tbl_PM_Troubleshooting", TROUBLE_COLS, trouble_rows)
                    except Exception as tbl_err:
                        logger.warning("Fabric troubleshooting insert notice: %s", tbl_err)

                # Audit after log insert (independent of row-table failures)
                try:
                    _emit_extract_audit(
                        conn,
                        event_type="EXTRACT_COMPLETE",
                        run_id=run_id,
                        content_hash=content_hash,
                        filename=filename,
                        user_id=user_id,
                        user_email=user_email,
                        user_role=user_role,
                        from_status=None,
                        to_status=doc_status,
                        details={
                            "content_hash": content_hash,
                            "document_status": doc_status,
                            "overall_score": float(getattr(result.meta, "overall_score", 0) or 0),
                            "maintenance_count": len(result.maintenance),
                            "spare_parts_count": len(result.spare_parts),
                            "troubleshooting_count": len(result.troubleshooting),
                            "envelope_slim": envelope_json != envelope_json_full,
                        },
                    )
                except Exception as audit_err:
                    logger.warning("Audit insert notice: %s", audit_err)
            finally:
                conn.close()
        except Exception as fabric_err:
            logger.warning("Fabric save_extract_to_fabric notice: %s", fabric_err)

    if hasattr(result, "meta") and result.meta:
        result.meta.run_id = run_id
        result.meta.document_status = doc_status
        result.meta.approved_by = approved_by
        result.meta.approved_at = approved_at

    return run_id


def update_fabric_review_state(
    run_id: str,
    *,
    document_status: str = "Pending Review",
    approved_by: Optional[str] = None,
    approved_at: Optional[str] = None,
    rejection_notes: Optional[str] = None,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
    user_role: Optional[str] = None,
    doc_metadata: Optional[dict[str, Any]] = None,
    spare_parts: Optional[list[Any]] = None,
    maintenance: Optional[list[Any]] = None,
    troubleshooting: Optional[list[Any]] = None,
) -> bool:
    """Updates the review envelope, staged records, and status in Fabric & cache for a given extract run."""
    run_id = (run_id or "").strip()
    if not run_id:
        return False

    log_row = get_done_run(run_id) or {}
    previous_status = _clean_status(log_row.get("document_status"))

    # Parse existing envelope if present
    envelope = {}
    raw_env = str(log_row.get("error") or "").strip()
    if raw_env.startswith("{") and raw_env.endswith("}"):
        try:
            envelope = json.loads(raw_env)
        except Exception:
            pass
    if not envelope:
        envelope = dict(log_row)

    if not previous_status:
        previous_status = _clean_status(envelope.get("document_status"))

    content_hash = _row_content_hash(log_row) or str(envelope.get("content_hash") or "").strip().lower() or None

    blocked = review_requeue_blocked_message(content_hash, new_status=document_status)
    if blocked:
        raise ValueError(blocked)

    # Ensure raw_payload is preserved or seeded if missing from legacy records
    if "raw_payload" not in envelope or not envelope["raw_payload"]:
        legacy_sp = envelope.get("spare_parts") or []
        legacy_mt = envelope.get("maintenance") or []
        legacy_tr = envelope.get("troubleshooting") or []
        envelope["raw_payload"] = {
            "spare_parts": [dict(r) for r in legacy_sp],
            "maintenance": [dict(r) for r in legacy_mt],
            "troubleshooting": [dict(r) for r in legacy_tr],
            "doc_metadata": envelope.get("doc_metadata") or {},
            "extracted_at": log_row.get("extracted_at") or log_row.get("created_at") or datetime.now(timezone.utc).isoformat(),
        }

    if doc_metadata:
        envelope["doc_metadata"] = doc_metadata
        if isinstance(doc_metadata, dict):
            if doc_metadata.get("title"): envelope["doc_title"] = doc_metadata.get("title")
            if doc_metadata.get("oem_manufacturer"): envelope["oem_manufacturer"] = doc_metadata.get("oem_manufacturer")
            if doc_metadata.get("equipment_model"): envelope["equipment_model"] = doc_metadata.get("equipment_model")
            if doc_metadata.get("equipment_type"): envelope["equipment_type"] = doc_metadata.get("equipment_type")
            if doc_metadata.get("document_version"): envelope["document_version"] = doc_metadata.get("document_version")
            if doc_metadata.get("publication_date"): envelope["publication_date"] = doc_metadata.get("publication_date")

    envelope["document_status"] = document_status
    envelope["approved_by"] = approved_by
    envelope["approved_at"] = approved_at
    envelope["rejection_notes"] = rejection_notes

    # Preserve original creator / submitter so document never disappears from Editor's My Extracts
    orig_user_id = envelope.get("user_id") or log_row.get("user_id")
    orig_user_email = envelope.get("user_email") or log_row.get("user_email")
    orig_submitted_by = envelope.get("submitted_by") or log_row.get("submitted_by") or orig_user_email
    orig_approver = envelope.get("assigned_approver") or log_row.get("assigned_approver")

    if orig_user_id: envelope["user_id"] = orig_user_id
    if orig_user_email: envelope["user_email"] = orig_user_email
    if orig_submitted_by: envelope["submitted_by"] = orig_submitted_by
    if orig_approver: envelope["assigned_approver"] = orig_approver

    if user_email:
        envelope["last_modified_by"] = user_email
        envelope["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        if not envelope.get("user_email"):
            envelope["user_email"] = orig_user_email or user_email
        if not envelope.get("submitted_by"):
            envelope["submitted_by"] = orig_submitted_by or user_email
        if not envelope.get("assigned_approver"):
            try:
                from ..auth import store as auth_store
                u_rec = auth_store.find_by_email(user_email)
                if u_rec and u_rec.get("assigned_approver"):
                    envelope["assigned_approver"] = u_rec.get("assigned_approver")
            except Exception:
                pass

    # Update ONLY edited_payload (working/reviewed state) and working arrays (NEVER raw_payload)
    edited_sp = [(s.model_dump() if hasattr(s, "model_dump") else dict(s)) for s in spare_parts] if spare_parts is not None else envelope.get("spare_parts") or []
    edited_mt = [(m.model_dump() if hasattr(m, "model_dump") else dict(m)) for m in maintenance] if maintenance is not None else envelope.get("maintenance") or []
    edited_tr = [(t.model_dump() if hasattr(t, "model_dump") else dict(t)) for t in troubleshooting] if troubleshooting is not None else envelope.get("troubleshooting") or []

    envelope["edited_payload"] = {
        "spare_parts": edited_sp,
        "maintenance": edited_mt,
        "troubleshooting": edited_tr,
        "doc_metadata": doc_metadata or envelope.get("doc_metadata") or {},
        "last_modified_by": user_email,
        "last_modified_at": datetime.now(timezone.utc).isoformat(),
    }

    if spare_parts is not None:
        envelope["spare_parts"] = edited_sp
    if maintenance is not None:
        envelope["maintenance"] = edited_mt
    if troubleshooting is not None:
        envelope["troubleshooting"] = edited_tr

    updated_env_json = _json_dumps(envelope)
    fabric_env_json = _envelope_json_for_log_column(envelope)

    # 1. Update resilient cache
    cached_rec = _load_from_cache(run_id) or dict(log_row)
    cached_rec["run_id"] = run_id
    cached_rec["document_status"] = document_status
    cached_rec["approved_by"] = approved_by
    cached_rec["approved_at"] = approved_at
    cached_rec["rejection_notes"] = rejection_notes
    cached_rec["user_id"] = orig_user_id or cached_rec.get("user_id")
    cached_rec["user_email"] = orig_user_email or cached_rec.get("user_email")
    cached_rec["submitted_by"] = orig_submitted_by or cached_rec.get("submitted_by")
    cached_rec["assigned_approver"] = orig_approver or cached_rec.get("assigned_approver")
    cached_rec["last_modified_by"] = user_email
    cached_rec["last_modified_at"] = datetime.now(timezone.utc).isoformat()
    if doc_metadata and isinstance(doc_metadata, dict):
        cached_rec["doc_metadata"] = doc_metadata
        if doc_metadata.get("title"): cached_rec["doc_title"] = doc_metadata.get("title")
        if doc_metadata.get("oem_manufacturer"): cached_rec["oem_manufacturer"] = doc_metadata.get("oem_manufacturer")
        if doc_metadata.get("equipment_model"): cached_rec["equipment_model"] = doc_metadata.get("equipment_model")
        if doc_metadata.get("equipment_type"): cached_rec["equipment_type"] = doc_metadata.get("equipment_type")
        if doc_metadata.get("document_version"): cached_rec["document_version"] = doc_metadata.get("document_version")
        if doc_metadata.get("publication_date"): cached_rec["publication_date"] = doc_metadata.get("publication_date")
    cached_rec["error"] = updated_env_json
    cached_rec["edited_payload"] = envelope["edited_payload"]
    if spare_parts is not None:
        cached_rec["spare_parts"] = edited_sp
    if maintenance is not None:
        cached_rec["maintenance"] = edited_mt
    if troubleshooting is not None:
        cached_rec["troubleshooting"] = edited_tr
    _store_in_cache(run_id, cached_rec)

    # 2. Attempt Fabric SQL update
    if fabric_sql.fabric_configured():
        try:
            conn = fabric_sql.connect()
            try:
                cur = conn.cursor()

                # Update relational tables in Fabric if records are provided
                if spare_parts is not None:
                    try:
                        cur.execute("DELETE FROM Tbl_PM_Spare_Parts WHERE run_id = ?", (run_id,))
                        spare_rows = []
                        for row in spare_parts:
                            d = row.model_dump() if hasattr(row, "model_dump") else dict(row)
                            d["run_id"] = run_id
                            d["page"] = str(d.get("page") or "")
                            d.update(_qf(row, f"{d.get('part_name','')} {d.get('part_number_code','')} {d.get('drawing_model_no','')}"))
                            spare_rows.append({c: d.get(c) for c in SPARE_COLS})
                        if spare_rows:
                            fabric_sql.insert_many(conn, "Tbl_PM_Spare_Parts", SPARE_COLS, spare_rows)
                    except Exception as tbl_err:
                        logger.debug("Failed to update Tbl_PM_Spare_Parts during review-sync: %s", tbl_err)

                if maintenance is not None:
                    try:
                        cur.execute("DELETE FROM Tbl_PM_Maintenance WHERE run_id = ?", (run_id,))
                        maint_rows = []
                        for row in maintenance:
                            d = row.model_dump() if hasattr(row, "model_dump") else dict(row)
                            d["run_id"] = run_id
                            d["page"] = str(d.get("page") or "")
                            ai = d.get("checks_instructions") or d.get("maintenance_work_description")
                            d.update(_qf(row, str(ai or "")))
                            if not d.get("attended_by") or d.get("attended_by") == "NA":
                                d["attended_by"] = user_email or "NA"
                            r_stat = d.get("status") or "Pending Review"
                            rem = str(d.get("remarks") or "")
                            if r_stat and r_stat != "Pending Review" and f"[{r_stat}]" not in rem:
                                d["remarks"] = f"[{r_stat}] {rem}".strip()
                            maint_rows.append({c: d.get(c) for c in MAINT_COLS})
                        if maint_rows:
                            fabric_sql.insert_many(conn, "Tbl_PM_Maintenance", MAINT_COLS, maint_rows)
                    except Exception as tbl_err:
                        logger.debug("Failed to update Tbl_PM_Maintenance during review-sync: %s", tbl_err)

                if troubleshooting is not None:
                    try:
                        cur.execute("DELETE FROM Tbl_PM_Troubleshooting WHERE run_id = ?", (run_id,))
                        trouble_rows = []
                        for row in troubleshooting:
                            d = row.model_dump() if hasattr(row, "model_dump") else dict(row)
                            d["run_id"] = run_id
                            d["page"] = str(d.get("page") or "")
                            d.update(_qf(row, f"{d.get('problem','')} {d.get('root_cause_solution','')}"))
                            trouble_rows.append({c: d.get(c) for c in TROUBLE_COLS})
                        if trouble_rows:
                            fabric_sql.insert_many(conn, "Tbl_PM_Troubleshooting", TROUBLE_COLS, trouble_rows)
                    except Exception as tbl_err:
                        logger.debug("Failed to update Tbl_PM_Troubleshooting during review-sync: %s", tbl_err)

                sp_cnt = len(envelope.get("spare_parts")) if "spare_parts" in envelope else int(log_row.get("spare_parts_count") or 0)
                m_cnt = len(envelope.get("maintenance")) if "maintenance" in envelope else int(log_row.get("maintenance_count") or 0)
                t_cnt = len(envelope.get("troubleshooting")) if "troubleshooting" in envelope else int(log_row.get("troubleshooting_count") or 0)

                meta_in = doc_metadata if isinstance(doc_metadata, dict) else {}
                doc_t = meta_in.get("title") or envelope.get("doc_title") or log_row.get("doc_title")
                doc_o = meta_in.get("oem_manufacturer") or envelope.get("oem_manufacturer") or log_row.get("oem_manufacturer")
                doc_m = meta_in.get("equipment_model") or envelope.get("equipment_model") or log_row.get("equipment_model")
                doc_ty = meta_in.get("equipment_type") or envelope.get("equipment_type") or log_row.get("equipment_type")
                doc_v = meta_in.get("document_version") or envelope.get("document_version") or log_row.get("document_version")
                doc_d = meta_in.get("publication_date") or envelope.get("publication_date") or log_row.get("publication_date")

                known_log = fabric_sql._get_table_columns(conn, "Tbl_PM_Extraction_logs")
                # Build UPDATE from columns that actually exist (legacy WH has only ~14 cols).
                set_parts: list[str] = []
                params: list[Any] = []
                if not known_log or "envelope_json" in known_log:
                    set_parts.append("envelope_json = ?")
                    params.append(fabric_env_json)
                # Phase D: clear legacy error blob on successful review sync when envelope_json exists
                if not known_log or "error" in known_log:
                    set_parts.append("error = ?")
                    params.append(None if (not known_log or "envelope_json" in known_log) else fabric_env_json)
                for col, val in [
                    ("document_status", document_status),
                    ("approved_by", approved_by),
                    ("approved_at", approved_at),
                    ("rejection_notes", rejection_notes),
                    ("submitted_by", orig_submitted_by),
                    ("assigned_approver", orig_approver),
                    ("spare_parts_count", sp_cnt),
                    ("maintenance_count", m_cnt),
                    ("troubleshooting_count", t_cnt),
                    ("doc_title", doc_t),
                    ("oem_manufacturer", doc_o),
                    ("equipment_model", doc_m),
                    ("equipment_type", doc_ty),
                    ("document_version", doc_v),
                    ("publication_date", doc_d),
                ]:
                    if not known_log or col in known_log:
                        set_parts.append(f"{col} = ?")
                        params.append(val)
                if set_parts:
                    params.append(run_id)
                    cur.execute(
                        f"UPDATE Tbl_PM_Extraction_logs SET {', '.join(set_parts)} WHERE run_id = ?",
                        tuple(params),
                    )
                if cur.rowcount == 0:
                    logger.warning("Fabric review-sync UPDATE matched 0 rows for run_id=%s", run_id)

                try:
                    from . import fabric_schema

                    fabric_schema.ensure_phase_b_through_e(conn)
                except Exception as schema_err:
                    logger.warning("Fabric schema ensure notice: %s", schema_err)

                try:
                    from . import fabric_schema

                    fabric_schema.upsert_extract_payloads(
                        conn,
                        run_id=run_id,
                        content_hash=content_hash,
                        raw_payload=envelope.get("raw_payload"),
                        edited_payload=envelope.get("edited_payload"),
                    )
                except Exception as pay_err:
                    logger.warning("Fabric payloads review-sync notice: %s", pay_err)

                try:
                    from . import fabric_schema

                    if _clean_status(document_status) == "Approved" and content_hash:
                        fabric_schema.upsert_document_row(
                            conn,
                            {
                                "content_hash": content_hash,
                                "canonical_run_id": run_id,
                                "filename": str(log_row.get("filename") or ""),
                                "doc_title": doc_t,
                                "oem_manufacturer": doc_o,
                                "equipment_model": doc_m,
                                "equipment_type": doc_ty,
                                "document_version": doc_v,
                                "publication_date": doc_d,
                                "global_status": "Approved",
                                "approved_by": approved_by,
                                "approved_at": approved_at,
                            },
                        )
                except Exception as doc_err:
                    logger.warning("Fabric documents review-sync notice: %s", doc_err)

                conn.commit()
                cur.close()

                # Stream review update audit event
                try:
                    _emit_extract_audit(
                        conn,
                        event_type="REVIEW_SYNC",
                        run_id=run_id,
                        content_hash=content_hash,
                        filename=str(log_row.get("filename") or ""),
                        user_id=user_id,
                        user_email=user_email or approved_by,
                        user_role=user_role,
                        from_status=previous_status or None,
                        to_status=document_status,
                        details={
                            "content_hash": content_hash,
                            "document_status": document_status,
                            "approved_by": approved_by,
                            "rejection_notes": rejection_notes,
                            "spare_parts_count": sp_cnt,
                            "maintenance_count": m_cnt,
                            "troubleshooting_count": t_cnt,
                        },
                    )
                except Exception as audit_err:
                    logger.warning("Review sync audit insert skipped: %s", audit_err)
            finally:
                conn.close()
        except Exception as fabric_err:
            logger.debug("Fabric review sync update notice: %s", fabric_err)

    invalidate_extracts_list_cache()
    if _clean_status(document_status) == "Approved":
        try:
            ch = str(log_row.get("content_hash") or envelope.get("content_hash") or "").strip()
            supersede_duplicate_runs(
                run_id,
                content_hash=ch or None,
                user_id=orig_user_id,
                user_email=orig_user_email or user_email,
            )
        except Exception:
            pass
    return True


async def _notify_already_approved(
    *,
    user_email: Optional[str],
    filename: str,
    result: ExtractResponse,
    run_id: str,
) -> None:
    if not user_email:
        return
    try:
        from ..notifications import create_notification

        title = filename
        doc_md = getattr(result.meta, "doc_metadata", None)
        if doc_md is not None and getattr(doc_md, "title", None):
            title = doc_md.title
        who = getattr(result.meta, "prior_approved_by", None) or getattr(result.meta, "approved_by", None) or "an approver"
        when = getattr(result.meta, "prior_approved_at", None) or getattr(result.meta, "approved_at", None)
        when_s = f" on {when}" if when else ""
        create_notification(
            recipient_email=user_email,
            event_type="already_approved",
            run_id=run_id,
            title=str(title or filename),
            actor_email=getattr(result.meta, "prior_approved_by", None) or getattr(result.meta, "approved_by", None),
            body=(
                f"This document was already signed off by {who}{when_s}. "
                "Review is complete — no further action required."
            ),
        )
    except Exception as nerr:
        logger.debug("Already-approved notification skipped: %s", nerr)


async def _serve_globally_approved_extract(
    *,
    approved_source: dict[str, Any],
    file_bytes: bytes,
    filename: str,
    content_hash: str,
    drive_item_id: Optional[str],
    etag: Optional[str],
    user_id: Optional[str],
    user_email: Optional[str],
    user_role: Optional[str],
    duration_ms: Optional[int],
    cached: Optional[dict[str, Any]],
    on_progress: Optional[Callable[[str, float], None]],
) -> Optional[ExtractResponse]:
    """Load and persist a read-only Approved view for a globally signed-off PDF."""
    source_run_id = str(approved_source["run_id"])
    existing_user_run = None
    if user_id or user_email:
        existing_user_run = await asyncio.to_thread(
            find_user_run_by_content_hash,
            content_hash,
            user_id=user_id,
            user_email=user_email,
        )

    reuse_run_id = None
    if existing_user_run and existing_user_run.get("run_id"):
        reuse_run_id = str(existing_user_run["run_id"])
        if on_progress:
            on_progress("Loading previously signed-off document…", 0.25)
        result = await asyncio.to_thread(
            load_extract_from_fabric,
            reuse_run_id,
            filename=filename,
            overall_score=(
                float(existing_user_run["overall_score"])
                if existing_user_run.get("overall_score") is not None
                else None
            ),
            cached_record=existing_user_run,
        )
        if result:
            result = await asyncio.to_thread(
                resolve_global_approved_cache_view,
                result,
                existing_user_run,
                keep_run_id=reuse_run_id,
            )
    else:
        if on_progress:
            on_progress("This document was already signed off — loading approved record…", 0.2)
        result = await asyncio.to_thread(
            load_extract_from_fabric,
            source_run_id,
            filename=filename,
            overall_score=(
                float(approved_source["overall_score"])
                if approved_source.get("overall_score") is not None
                else None
            ),
            cached_record=approved_source,
        )
        if result:
            _apply_globally_approved_for_new_user(result)

    if not result:
        return None

    if hasattr(result, "meta") and result.meta:
        result.meta.engine = "fabric-cache"

    if on_progress:
        on_progress("Logging approved document to your workspace…", 0.9)
    try:
        new_run_id = await asyncio.to_thread(
            save_extract_to_fabric,
            file_bytes=file_bytes,
            filename=filename,
            result=result,
            content_hash=content_hash,
            drive_item_id=drive_item_id or (cached or {}).get("drive_item_id") or (existing_user_run or {}).get("drive_item_id"),
            etag=etag or (cached or {}).get("etag") or (existing_user_run or {}).get("etag"),
            user_id=user_id,
            user_email=user_email,
            user_role=user_role,
            duration_ms=duration_ms or 0,
        )
        if new_run_id:
            result.meta.run_id = new_run_id
        elif reuse_run_id:
            result.meta.run_id = reuse_run_id
        else:
            result.meta.run_id = source_run_id
        if new_run_id:
            await asyncio.to_thread(
                supersede_duplicate_runs,
                new_run_id,
                content_hash=content_hash,
                user_id=user_id,
                user_email=user_email,
            )
        await _notify_already_approved(
            user_email=user_email,
            filename=filename,
            result=result,
            run_id=str(result.meta.run_id),
        )
    except Exception as save_err:
        logger.warning("Failed to persist globally approved extract: %s", save_err)
        if reuse_run_id and hasattr(result, "meta") and result.meta:
            result.meta.run_id = reuse_run_id

    if on_progress:
        on_progress("Document already approved — no review required", 1.0)
    return result


async def extract_with_fabric_cache(
    file_bytes: bytes,
    filename: str,
    options: ExtractOptions,
    *,
    extract_fn: Callable[..., Any],
    on_progress: Optional[Callable[[str, float], None]] = None,
    drive_item_id: Optional[str] = None,
    etag: Optional[str] = None,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
    user_role: Optional[str] = None,
    duration_ms: Optional[int] = None,
) -> ExtractResponse:
    content_hash = file_sha256(file_bytes)

    try:
        if on_progress:
            on_progress("Checking repository cache…", 0.02)
        cached = await asyncio.to_thread(
            find_done_run,
            content_hash=content_hash,
            drive_item_id=drive_item_id,
            filename=filename,
        )
        approved_source = await asyncio.to_thread(
            resolve_approved_source,
            content_hash,
            cached,
        )
        if approved_source and approved_source.get("run_id"):
            approved_result = await _serve_globally_approved_extract(
                approved_source=approved_source,
                file_bytes=file_bytes,
                filename=filename,
                content_hash=content_hash,
                drive_item_id=drive_item_id,
                etag=etag,
                user_id=user_id,
                user_email=user_email,
                user_role=user_role,
                duration_ms=duration_ms,
                cached=cached,
                on_progress=on_progress,
            )
            if approved_result:
                return approved_result

        if cached and cached.get("run_id"):
            # Reuse the current user's existing history row for this file (no duplicate insert).
            existing_user_run = None
            if user_id or user_email:
                existing_user_run = await asyncio.to_thread(
                    find_user_run_by_content_hash,
                    content_hash,
                    user_id=user_id,
                    user_email=user_email,
                )
            if existing_user_run and existing_user_run.get("run_id"):
                reuse_run_id = str(existing_user_run["run_id"])
                if on_progress:
                    on_progress("Reusing your existing document record…", 0.25)
                result = await asyncio.to_thread(
                    load_extract_from_fabric,
                    reuse_run_id,
                    filename=filename,
                    overall_score=(
                        float(existing_user_run["overall_score"])
                        if existing_user_run.get("overall_score") is not None
                        else None
                    ),
                    cached_record=existing_user_run,
                )
                if result:
                    if hasattr(result, "meta") and result.meta:
                        result.meta.run_id = reuse_run_id
                        result.meta.engine = "fabric-cache"
                    if on_progress:
                        on_progress("Loaded existing document record (no duplicate created)", 1.0)
                    return result

            source_run_id = str(cached["run_id"])
            if on_progress:
                on_progress("Loading extract from cache (deduplicated)…", 0.2)
            result = await asyncio.to_thread(
                load_extract_from_fabric,
                source_run_id,
                filename=filename,
                overall_score=(
                    float(cached["overall_score"])
                    if cached.get("overall_score") is not None
                    else None
                ),
                cached_record=cached,
            )
            if result:
                if hasattr(result, "meta") and result.meta:
                    result.meta.run_id = source_run_id
                    result.meta.engine = "fabric-cache"

                _isolate_cache_hit_for_new_user(result)

                # Log extraction execution event in Fabric Lakehouse & user history
                if on_progress:
                    on_progress("Logging extraction event to Fabric repository…", 0.9)
                try:
                    new_run_id = await asyncio.to_thread(
                        save_extract_to_fabric,
                        file_bytes=file_bytes,
                        filename=filename,
                        result=result,
                        content_hash=content_hash,
                        drive_item_id=drive_item_id or cached.get("drive_item_id"),
                        etag=etag or cached.get("etag"),
                        user_id=user_id,
                        user_email=user_email,
                        user_role=user_role,
                        duration_ms=duration_ms or 0,
                    )
                    if hasattr(result, "meta") and result.meta and new_run_id:
                        result.meta.run_id = new_run_id
                except Exception as save_err:
                    logger.warning("Failed to log deduplicated extract to Fabric: %s", save_err)

                if on_progress:
                    on_progress("Loaded from central repository (deduplicated)", 1.0)
                return result
    except Exception as err:
        logger.warning("Repository cache lookup error: %s", err)

    result = await extract_fn(file_bytes, filename, options, on_progress=on_progress)

    approved_after_extract = await asyncio.to_thread(
        find_approved_run_by_content_hash,
        content_hash,
    )
    if approved_after_extract and approved_after_extract.get("run_id"):
        approved_result = await _serve_globally_approved_extract(
            approved_source=approved_after_extract,
            file_bytes=file_bytes,
            filename=filename,
            content_hash=content_hash,
            drive_item_id=drive_item_id,
            etag=etag,
            user_id=user_id,
            user_email=user_email,
            user_role=user_role,
            duration_ms=duration_ms,
            cached=None,
            on_progress=on_progress,
        )
        if approved_result:
            return approved_result

    if (not drive_item_id or drive_item_id == "LOCAL_UPLOAD") and graph_sharepoint.sharepoint_configured():
        try:
            if on_progress:
                on_progress("Syncing upload to SharePoint…", 0.95)
            sp_res = await asyncio.to_thread(graph_sharepoint.upload_file_to_sharepoint, file_bytes, filename)
            if sp_res and sp_res[0]:
                drive_item_id = sp_res[0]
                etag = sp_res[1]
        except Exception as err:
            logger.warning("SharePoint upload error: %s", err)

    try:
        if on_progress:
            on_progress("Saving extract to repository & persistent cache…", 0.97)
        saved_run_id = await asyncio.to_thread(
            save_extract_to_fabric,
            file_bytes=file_bytes,
            filename=filename,
            result=result,
            content_hash=content_hash,
            drive_item_id=drive_item_id,
            etag=etag,
            user_id=user_id,
            user_email=user_email,
            user_role=user_role,
            duration_ms=duration_ms,
        )
        if hasattr(result, "meta") and result.meta and saved_run_id:
            result.meta.run_id = saved_run_id
    except Exception as err:
        logger.warning("Extract save error: %s", err)
        warnings = list(result.meta.warnings or [])
        warnings.append(f"Extract save notice: {err}")
        result.meta.warnings = warnings

    return result
