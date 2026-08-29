from __future__ import annotations

import asyncio
import hashlib
import json
import logging
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

    norm_fn = (filename or "").strip().lower()
    for rec in reversed(candidates):
        r_hash = str(rec.get("content_hash") or "").strip()
        r_fn = str(rec.get("filename") or "").strip().lower()
        r_item = str(rec.get("drive_item_id") or "").strip()

        if content_hash and r_hash and r_hash == content_hash:
            return rec
        if drive_item_id and drive_item_id != "LOCAL_UPLOAD" and r_item and r_item == drive_item_id:
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


def find_done_run(
    *,
    content_hash: str,
    drive_item_id: Optional[str] = None,
    filename: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    # 1. Primary: Search Microsoft Fabric SQL Lakehouse
    if fabric_sql.fabric_configured():
        try:
            conn = fabric_sql.connect()
            try:
                cur = conn.cursor()
                
                # 1a. Match by content_hash
                if content_hash:
                    cur.execute(
                        """
                        SELECT TOP 1 run_id, filename, overall_score, maintenance_count,
                               spare_parts_count, troubleshooting_count, engine, parse_strategy,
                               document_status, user_id, user_email, error
                        FROM Tbl_PM_Extraction_logs
                        WHERE (status IS NULL OR LOWER(status) NOT IN ('error', 'failed', 'cancelled'))
                          AND (content_hash = ? OR error LIKE ?)
                        ORDER BY extracted_at DESC
                        """,
                        (content_hash, f'%"{content_hash}"%'),
                    )
                    row = cur.fetchone()
                    if row:
                        cols = [c[0] for c in cur.description]
                        return dict(zip(cols, row))

                # 1b. Match by drive_item_id
                if drive_item_id and drive_item_id != "LOCAL_UPLOAD":
                    cur.execute(
                        """
                        SELECT TOP 1 run_id, filename, overall_score, maintenance_count,
                               spare_parts_count, troubleshooting_count, engine, parse_strategy,
                               document_status, user_id, user_email, error
                        FROM Tbl_PM_Extraction_logs
                        WHERE (status IS NULL OR LOWER(status) NOT IN ('error', 'failed', 'cancelled'))
                          AND drive_item_id = ?
                        ORDER BY extracted_at DESC
                        """,
                        (drive_item_id,),
                    )
                    row = cur.fetchone()
                    if row:
                        cols = [c[0] for c in cur.description]
                        return dict(zip(cols, row))

                # 1c. Match by filename
                if filename:
                    clean_fn = filename.strip()
                    cur.execute(
                        """
                        SELECT TOP 1 run_id, filename, overall_score, maintenance_count,
                               spare_parts_count, troubleshooting_count, engine, parse_strategy,
                               document_status, user_id, user_email, error
                        FROM Tbl_PM_Extraction_logs
                        WHERE (status IS NULL OR LOWER(status) NOT IN ('error', 'failed', 'cancelled'))
                          AND (LOWER(filename) = LOWER(?) OR error LIKE ?)
                        ORDER BY extracted_at DESC
                        """,
                        (clean_fn, f'%"{clean_fn}"%'),
                    )
                    row = cur.fetchone()
                    if row:
                        cols = [c[0] for c in cur.description]
                        return dict(zip(cols, row))
            finally:
                conn.close()
        except Exception as err:
            logger.warning("Fabric find_done_run error: %s", err)

    # 2. Resilient fallback: Search unified persistent store
    cached = _find_in_cache(content_hash=content_hash, filename=filename, drive_item_id=drive_item_id)
    if cached:
        return cached

    return None


def find_done_run_by_hash(content_hash: str) -> Optional[dict[str, Any]]:
    return find_done_run(content_hash=content_hash)


def _row_matches_user(
    row: dict[str, Any],
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
) -> bool:
    """Verifies whether an extraction log row belongs to the specified user."""
    uid = str(user_id or "").strip()
    uemail = str(user_email or "").strip().lower()
    if not uid and not uemail:
        return True

    r_uid = str(row.get("user_id") or "").strip()
    r_email = str(row.get("user_email") or "").strip().lower()
    r_appr = str(row.get("approved_by") or "").strip().lower()
    r_sub = str(row.get("submitted_by") or "").strip().lower()
    r_engine = str(row.get("engine") or "").lower()
    r_err = str(row.get("error") or "")

    # 1. Match direct user_id
    if uid and r_uid and r_uid == uid:
        return True

    # 2. Match direct user_email or approver/submitter
    if uemail:
        if r_email and (r_email == uemail or uemail in r_email):
            return True
        if r_appr and (r_appr == uemail or uemail in r_appr):
            return True
        if r_sub and (r_sub == uemail or uemail in r_sub):
            return True
        if f"[user:{uemail}]" in r_engine or f"user:{uemail}" in r_engine:
            return True

    # 3. Match within JSON envelope inside error column
    if r_err.startswith("{") and r_err.endswith("}"):
        try:
            env = json.loads(r_err)
            env_uid = str(env.get("user_id") or "").strip()
            env_email = str(env.get("user_email") or "").strip().lower()
            env_mod = str(env.get("last_modified_by") or env.get("submitted_by") or "").strip().lower()
            if uid and env_uid and env_uid == uid:
                return True
            if uemail:
                if env_email and (env_email == uemail or uemail in env_email):
                    return True
                if env_mod and (env_mod == uemail or uemail in env_mod):
                    return True
        except Exception:
            pass

    return False


def list_done_extracts(
    *,
    limit: int = 100,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
) -> list[dict[str, Any]]:
    top = max(1, min(int(limit or 100), 500))
    uid = str(user_id or "").strip()
    uemail = str(user_email or "").strip().lower()
    rows: list[dict[str, Any]] = []
    fabric_success = False

    # 1. Primary: Query Microsoft Fabric SQL
    try:
        conn = fabric_sql.connect()
        try:
            cur = conn.cursor()
            known_cols = fabric_sql._get_table_columns(conn, "Tbl_PM_Extraction_logs")

            # Build parameterized Fabric SQL query
            if uid or uemail:
                clauses: list[str] = []
                params: list[Any] = []

                if uid and "user_id" in known_cols:
                    clauses.append("user_id = ?")
                    params.append(uid)
                if uemail and "user_email" in known_cols:
                    clauses.append("LOWER(user_email) = ?")
                    params.append(uemail)
                if uid:
                    clauses.append("error LIKE ?")
                    params.append(f'%"{uid}"%')
                if uemail:
                    clauses.append("error LIKE ?")
                    params.append(f'%"{uemail}"%')
                    clauses.append("engine LIKE ?")
                    params.append(f"%{uemail}%")

                where_sql = f"WHERE ({' OR '.join(clauses)})"
                sql = f"SELECT TOP {top} * FROM Tbl_PM_Extraction_logs {where_sql} ORDER BY extracted_at DESC"
                cur.execute(sql, tuple(params))
            else:
                cur.execute(
                    f"SELECT TOP {top} * FROM Tbl_PM_Extraction_logs ORDER BY extracted_at DESC"
                )

            cols = [c[0] for c in cur.description]
            f_rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            for r in f_rows:
                raw_env = str(r.get("error") or "").strip()
                if raw_env.startswith("{") and raw_env.endswith("}"):
                    try:
                        env = json.loads(raw_env)
                        r["document_status"] = env.get("document_status") or r.get("document_status") or "Pending Review"
                        r["approved_by"] = env.get("approved_by") or r.get("approved_by")
                        r["approved_at"] = env.get("approved_at") or r.get("approved_at")
                        r["submitted_by"] = env.get("submitted_by") or r.get("submitted_by") or env.get("user_email")
                        r["assigned_approver"] = env.get("assigned_approver") or r.get("assigned_approver")
                        r["rejection_notes"] = env.get("rejection_notes") or r.get("rejection_notes")
                        r["user_id"] = env.get("user_id") or r.get("user_id")
                        r["user_email"] = env.get("user_email") or r.get("user_email")
                        r["user_role"] = env.get("user_role") or r.get("user_role")
                        doc_meta = env.get("doc_metadata") or {}
                        if doc_meta:
                            r["doc_title"] = doc_meta.get("title") or doc_meta.get("equipment_model") or r.get("doc_title")
                            r["oem_manufacturer"] = doc_meta.get("oem_manufacturer") or r.get("oem_manufacturer")
                    except Exception:
                        pass
                if not r.get("document_status"):
                    r["document_status"] = "Pending Review"
            rows.extend(f_rows)
            fabric_success = True
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
            elif cid not in known_run_ids:
                fresh_rows.append(c)
                known_run_ids.add(cid)

    rows = fresh_rows + rows

    # Apply strict in-memory user verification if scoped
    if uid or uemail:
        rows = [r for r in rows if _row_matches_user(r, user_id=uid, user_email=uemail)]

    return rows[:top]


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
                r = dict(zip(cols, row))
                raw_env = str(r.get("error") or "").strip()
                if raw_env.startswith("{") and raw_env.endswith("}"):
                    try:
                        env = json.loads(raw_env)
                        r["document_status"] = env.get("document_status") or r.get("document_status") or "Pending Review"
                        r["approved_by"] = env.get("approved_by") or r.get("approved_by")
                        r["approved_at"] = env.get("approved_at") or r.get("approved_at")
                        r["submitted_by"] = env.get("submitted_by") or r.get("submitted_by") or env.get("user_email")
                        r["assigned_approver"] = env.get("assigned_approver") or r.get("assigned_approver")
                        r["rejection_notes"] = env.get("rejection_notes") or r.get("rejection_notes")
                        r["user_id"] = env.get("user_id") or r.get("user_id")
                        r["user_email"] = env.get("user_email") or r.get("user_email")
                        r["user_role"] = env.get("user_role") or r.get("user_role")
                        doc_meta = env.get("doc_metadata") or {}
                        if doc_meta:
                            r["doc_title"] = doc_meta.get("title") or doc_meta.get("equipment_model") or r.get("doc_title")
                            r["oem_manufacturer"] = doc_meta.get("oem_manufacturer") or r.get("oem_manufacturer")
                    except Exception:
                        pass
                if not r.get("document_status"):
                    r["document_status"] = "Pending Review"
                return r
        finally:
            conn.close()
    except Exception as err:
        logger.debug("Fabric get_done_run notice: %s", err)

    return _load_from_cache(run_id)


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
) -> ExtractResponse:
    from ..models import DocumentMetadata

    spares_raw: list[dict[str, Any]] = []
    maint_raw: list[dict[str, Any]] = []
    trouble_raw: list[dict[str, Any]] = []
    log_meta: dict[str, Any] = {}

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

    log_meta = get_done_run(run_id) or {}

    cached_fallback = _load_from_cache(run_id)
    if cached_fallback:
        if not log_meta:
            log_meta = cached_fallback
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

    # Check for JSON envelope in error or rejection_notes and merge with cached_fallback
    envelope = {}
    if cached_fallback:
        envelope.update(cached_fallback)
    raw_env = str(log_meta.get("error") or "").strip()
    if raw_env.startswith("{") and raw_env.endswith("}"):
        try:
            parsed = json.loads(raw_env)
            if isinstance(parsed, dict):
                envelope.update(parsed)
        except Exception:
            pass

    # If envelope contains staged/edited records from review-sync or raw_payload, prefer them over baseline raw tables
    spares_source = (
        envelope.get("spare_parts")
        if (envelope.get("spare_parts") and isinstance(envelope.get("spare_parts"), list))
        else (
            envelope.get("edited_payload", {}).get("spare_parts")
            if (isinstance(envelope.get("edited_payload"), dict) and isinstance(envelope["edited_payload"].get("spare_parts"), list))
            else (
                envelope.get("raw_payload", {}).get("spare_parts")
                if (isinstance(envelope.get("raw_payload"), dict) and isinstance(envelope["raw_payload"].get("spare_parts"), list))
                else spares_raw
            )
        )
    )
    maint_source = (
        envelope.get("maintenance")
        if (envelope.get("maintenance") and isinstance(envelope.get("maintenance"), list))
        else (
            envelope.get("edited_payload", {}).get("maintenance")
            if (isinstance(envelope.get("edited_payload"), dict) and isinstance(envelope["edited_payload"].get("maintenance"), list))
            else (
                envelope.get("raw_payload", {}).get("maintenance")
                if (isinstance(envelope.get("raw_payload"), dict) and isinstance(envelope["raw_payload"].get("maintenance"), list))
                else maint_raw
            )
        )
    )
    trouble_source = (
        envelope.get("troubleshooting")
        if (envelope.get("troubleshooting") and isinstance(envelope.get("troubleshooting"), list))
        else (
            envelope.get("edited_payload", {}).get("troubleshooting")
            if (isinstance(envelope.get("edited_payload"), dict) and isinstance(envelope["edited_payload"].get("troubleshooting"), list))
            else (
                envelope.get("raw_payload", {}).get("troubleshooting")
                if (isinstance(envelope.get("raw_payload"), dict) and isinstance(envelope["raw_payload"].get("troubleshooting"), list))
                else trouble_raw
            )
        )
    )

    spares: list[SparePartRow] = []
    for d in spares_source:
        if isinstance(d, dict):
            q = _quality_from_row(d) if ("fields_filled_score" in d or "grounding_available" in d) else d.get("quality")
            rev_tags = _extract_review_tags_from_text(d.get("quality_reasons") or "")
            row_status = str(d.get("status") or rev_tags.get("status") or "Pending Review")
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
        else:
            spares.append(d)

    for i, r in enumerate(spares, 1):
        r.id = i

    maint: list[MaintenanceRow] = []
    for d in maint_source:
        if isinstance(d, dict):
            q = _quality_from_row(d) if ("fields_filled_score" in d or "grounding_available" in d) else d.get("quality")
            rev_tags = _extract_review_tags_from_text(d.get("remarks") or "")
            row_status = str(d.get("status") or rev_tags.get("status") or "Pending Review")
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
        else:
            maint.append(d)

    for i, r in enumerate(maint, 1):
        r.id = i

    trouble: list[TroubleshootingRow] = []
    for d in trouble_source:
        if isinstance(d, dict):
            q = _quality_from_row(d) if ("fields_filled_score" in d or "grounding_available" in d) else d.get("quality")
            rev_tags = _extract_review_tags_from_text(d.get("quality_reasons") or "")
            row_status = str(d.get("status") or rev_tags.get("status") or "Pending Review")
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
        else:
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

    doc_status = str(log_meta.get("document_status") or envelope.get("document_status") or "Pending Review")
    approved_by = log_meta.get("approved_by") or envelope.get("approved_by")
    approved_at = str(log_meta.get("approved_at") or envelope.get("approved_at") or "") or None
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
                status=str(d.get("status") or "Pending Review"),
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
                status=str(d.get("status") or "Pending Review"),
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
                status=str(d.get("status") or "Pending Review"),
            )

        b_spares = [_dict_to_sp(d, i+1) for i, d in enumerate(raw_p.get("spare_parts") or [])]
        b_maint = [_dict_to_mt(d, i+1) for i, d in enumerate(raw_p.get("maintenance") or [])]
        b_trouble = [_dict_to_tr(d, i+1) for i, d in enumerate(raw_p.get("troubleshooting") or [])]
        b_meta_dict = raw_p.get("doc_metadata") if isinstance(raw_p.get("doc_metadata"), dict) else None
        b_doc_meta = DocumentMetadata(**b_meta_dict) if b_meta_dict else None

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
            filename=filename,
            engine="fabric-cache",
            parse_strategy="cache",
            pages_total=int(log_meta.get("pages_total") or envelope.get("pages_total") or 0),
            pages_processed=int(log_meta.get("pages_processed") or envelope.get("pages_processed") or 0),
            maintenance_count=len(maint),
            spare_parts_count=len(spares),
            troubleshooting_count=len(trouble),
            warnings=["Loaded from Fabric central repository."],
            overall_score=score,
            run_id=run_id,
            doc_metadata=doc_meta,
            document_status=doc_status,
            approved_by=approved_by,
            approved_at=approved_at,
            rejection_reason=rejection_reason,
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


def save_extract_to_fabric(
    *,
    file_bytes: bytes,
    filename: str,
    result: ExtractResponse,
    content_hash: str | None = None,
    drive_item_id: str | None = None,
    etag: str | None = None,
    user_id: str | None = None,
    user_email: str | None = None,
    user_role: str | None = None,
    duration_ms: int | None = None,
) -> str:
    content_hash = content_hash or file_sha256(file_bytes)
    run_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    meta_obj = getattr(result.meta, "doc_metadata", None)
    doc_meta_dict = meta_obj.model_dump() if meta_obj else {}
    doc_status = str(getattr(result.meta, "document_status", "Pending Review") or "Pending Review")
    approved_by = getattr(result.meta, "approved_by", None)
    approved_at = getattr(result.meta, "approved_at", None)
    rejection_notes = getattr(result.meta, "rejection_reason", None)

    # Resolve assigned approver for this user if configured
    assigned_approver = None
    try:
        from ..auth import store as auth_store
        if user_email:
            u_rec = auth_store.find_by_email(user_email)
            if u_rec:
                assigned_approver = u_rec.get("assigned_approver")
        if not assigned_approver and user_id:
            u_rec = auth_store.find_by_id(user_id)
            if u_rec:
                assigned_approver = u_rec.get("assigned_approver")
    except Exception:
        pass

    # Prepare baseline extraction snapshot (immutable raw AI payload)
    raw_spares = [(s.model_dump() if hasattr(s, "model_dump") else dict(s)) for s in result.spare_parts]
    raw_maint = [(m.model_dump() if hasattr(m, "model_dump") else dict(m)) for m in result.maintenance]
    raw_trouble = [(t.model_dump() if hasattr(t, "model_dump") else dict(t)) for t in result.troubleshooting]

    raw_payload = {
        "spare_parts": [dict(r) for r in raw_spares],
        "maintenance": [dict(r) for r in raw_maint],
        "troubleshooting": [dict(r) for r in raw_trouble],
        "doc_metadata": dict(doc_meta_dict),
        "extracted_at": now.isoformat(),
    }

    edited_payload = {
        "spare_parts": [dict(r) for r in raw_spares],
        "maintenance": [dict(r) for r in raw_maint],
        "troubleshooting": [dict(r) for r in raw_trouble],
        "doc_metadata": dict(doc_meta_dict),
        "last_modified_at": now.isoformat(),
    }

    # Build JSON Envelope for non-breaking Fabric persistence
    envelope = {
        "_v": 2,
        "run_id": run_id,
        "filename": filename,
        "content_hash": content_hash,
        "drive_item_id": drive_item_id,
        "raw_payload": raw_payload,
        "edited_payload": edited_payload,
        "doc_metadata": doc_meta_dict,
        "doc_title": getattr(meta_obj, "title", None) if meta_obj else None,
        "oem_manufacturer": getattr(meta_obj, "oem_manufacturer", None) if meta_obj else None,
        "equipment_model": getattr(meta_obj, "equipment_model", None) if meta_obj else None,
        "equipment_type": getattr(meta_obj, "equipment_type", None) if meta_obj else None,
        "document_version": getattr(meta_obj, "document_version", None) if meta_obj else None,
        "publication_date": getattr(meta_obj, "publication_date", None) if meta_obj else None,
        "document_status": doc_status,
        "approved_by": approved_by,
        "approved_at": approved_at,
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
    envelope_json = json.dumps(envelope, ensure_ascii=False)

    raw_engine = str(getattr(result.meta, "engine", "") or "")
    engine_with_user = f"{raw_engine} [user:{user_email}]" if user_email and "[" not in raw_engine else raw_engine

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
        "error": envelope_json,
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
                fabric_sql.insert_log(
                    conn,
                    {
                        "run_id": run_id,
                        "drive_item_id": drive_item_id,
                        "etag": etag,
                        "content_hash": content_hash,
                        "filename": filename,
                        "status": "done",
                        "overall_score": float(getattr(result.meta, "overall_score", 0) or 0),
                        "maintenance_count": len(result.maintenance),
                        "spare_parts_count": len(result.spare_parts),
                        "troubleshooting_count": len(result.troubleshooting),
                        "engine": engine_with_user,
                        "parse_strategy": str(getattr(result.meta, "parse_strategy", "") or ""),
                        "extracted_at": now,
                        "error": envelope_json,
                        "doc_title": getattr(meta_obj, "title", None) if meta_obj else None,
                        "oem_manufacturer": getattr(meta_obj, "oem_manufacturer", None) if meta_obj else None,
                        "equipment_model": getattr(meta_obj, "equipment_model", None) if meta_obj else None,
                        "equipment_type": getattr(meta_obj, "equipment_type", None) if meta_obj else None,
                        "document_version": getattr(meta_obj, "document_version", None) if meta_obj else None,
                        "publication_date": getattr(meta_obj, "publication_date", None) if meta_obj else None,
                        "document_status": doc_status,
                        "approved_by": approved_by,
                        "approved_at": approved_at,
                        "assigned_approver": assigned_approver,
                        "rejection_notes": rejection_notes,
                        "user_id": user_id,
                        "user_email": user_email,
                        "user_role": user_role,
                        "duration_ms": duration_ms,
                        "pages_total": int(getattr(result.meta, "pages_total", 0) or 0),
                        "pages_processed": int(getattr(result.meta, "pages_processed", 0) or 0),
                        "grounding_pass_rate": float(getattr(result.meta, "grounding_pass_rate", 1.0) or 1.0),
                        "filter_drop_rate": float(getattr(result.meta, "filter_drop_rate", 0.0) or 0.0),
                        "low_confidence_count": int(getattr(result.meta, "low_confidence_count", 0) or 0),
                    },
                )

                spare_rows = []
                for row in result.spare_parts:
                    d = row.model_dump() if hasattr(row, "model_dump") else dict(row)
                    d["run_id"] = run_id
                    d["page"] = str(d.get("page") or "")
                    d.update(_qf(row, f"{d.get('part_name','')} {d.get('part_number_code','')} {d.get('drawing_model_no','')}"))
                    spare_rows.append({c: d.get(c) for c in SPARE_COLS})
                fabric_sql.insert_many(conn, "Tbl_PM_Spare_Parts", SPARE_COLS, spare_rows)

                maint_rows = []
                for row in result.maintenance:
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
                fabric_sql.insert_many(conn, "Tbl_PM_Maintenance", MAINT_COLS, maint_rows)

                trouble_rows = []
                for row in result.troubleshooting:
                    d = row.model_dump() if hasattr(row, "model_dump") else dict(row)
                    d["run_id"] = run_id
                    d["page"] = str(d.get("page") or "")
                    d.update(_qf(row, f"{d.get('problem','')} {d.get('root_cause_solution','')}"))
                    trouble_rows.append({c: d.get(c) for c in TROUBLE_COLS})
                fabric_sql.insert_many(conn, "Tbl_PM_Troubleshooting", TROUBLE_COLS, trouble_rows)

                # Record audit event
                try:
                    fabric_sql.insert_audit_event(
                        conn,
                        {
                            "event_id": uuid.uuid4().hex,
                            "event_type": "EXTRACT_SAVED",
                            "run_id": run_id,
                            "filename": filename,
                            "user_id": user_id,
                            "user_email": user_email,
                            "user_role": user_role,
                            "details_json": json.dumps({
                                "maintenance_count": len(result.maintenance),
                                "spare_parts_count": len(result.spare_parts),
                                "troubleshooting_count": len(result.troubleshooting),
                                "overall_score": getattr(result.meta, "overall_score", 0),
                                "document_status": doc_status,
                            }),
                            "created_at": now,
                        },
                    )
                except Exception as audit_err:
                    logger.debug("Fabric audit log insert skipped: %s", audit_err)
            finally:
                conn.close()
        except Exception as fabric_err:
            logger.warning("Fabric save notice (stored in persistent cache): %s", fabric_err)

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
    if user_email:
        envelope["last_modified_by"] = user_email
        envelope["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        if not envelope.get("user_email"):
            envelope["user_email"] = user_email
        if not envelope.get("submitted_by"):
            envelope["submitted_by"] = user_email
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

    updated_env_json = json.dumps(envelope, ensure_ascii=False)

    # 1. Update resilient cache
    cached_rec = _load_from_cache(run_id) or dict(log_row)
    cached_rec["run_id"] = run_id
    cached_rec["document_status"] = document_status
    cached_rec["approved_by"] = approved_by
    cached_rec["approved_at"] = approved_at
    cached_rec["rejection_notes"] = rejection_notes
    cached_rec["submitted_by"] = envelope.get("submitted_by")
    cached_rec["assigned_approver"] = envelope.get("assigned_approver")
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

                try:
                    cur.execute(
                        """UPDATE Tbl_PM_Extraction_logs 
                           SET error = ?, document_status = ?, approved_by = ?, approved_at = ?, rejection_notes = ?,
                               spare_parts_count = ?, maintenance_count = ?, troubleshooting_count = ?,
                               doc_title = ?, oem_manufacturer = ?, equipment_model = ?, equipment_type = ?,
                               document_version = ?, publication_date = ?
                           WHERE run_id = ?""",
                        (updated_env_json, document_status, approved_by, approved_at, rejection_notes,
                         sp_cnt, m_cnt, t_cnt, doc_t, doc_o, doc_m, doc_ty, doc_v, doc_d, run_id),
                    )
                except Exception:
                    cur.execute(
                        """UPDATE Tbl_PM_Extraction_logs 
                           SET error = ?, document_status = ?, approved_by = ?, approved_at = ?, rejection_notes = ?,
                               spare_parts_count = ?, maintenance_count = ?, troubleshooting_count = ?
                           WHERE run_id = ?""",
                        (updated_env_json, document_status, approved_by, approved_at, rejection_notes, sp_cnt, m_cnt, t_cnt, run_id),
                    )
                if cur.rowcount == 0:
                    try:
                        fabric_sql.upsert_extraction_log(conn, cached_rec)
                    except Exception as ins_err:
                        logger.debug("Failed to upsert extraction log in Fabric: %s", ins_err)
                conn.commit()
                cur.close()

                # Stream review update audit event
                try:
                    now = datetime.now(timezone.utc).replace(tzinfo=None)
                    fabric_sql.insert_audit_event(
                        conn,
                        {
                            "event_id": uuid.uuid4().hex,
                            "event_type": "REVIEW_SYNC",
                            "run_id": run_id,
                            "filename": str(log_row.get("filename") or ""),
                            "user_id": user_id,
                            "user_email": user_email or approved_by,
                            "user_role": user_role,
                            "details_json": json.dumps({
                                "document_status": document_status,
                                "approved_by": approved_by,
                                "rejection_notes": rejection_notes,
                                "spare_parts_count": sp_cnt,
                                "maintenance_count": m_cnt,
                                "troubleshooting_count": t_cnt,
                            }),
                            "created_at": now,
                        },
                    )
                except Exception as audit_err:
                    logger.debug("Review sync audit insert skipped: %s", audit_err)
            finally:
                conn.close()
        except Exception as fabric_err:
            logger.debug("Fabric review sync update notice: %s", fabric_err)

    return True



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
        if cached and cached.get("run_id"):
            cached_run_id = str(cached["run_id"])
            if on_progress:
                on_progress("Loading extract from cache (deduplicated)…", 0.2)
            result = await asyncio.to_thread(
                load_extract_from_fabric,
                cached_run_id,
                filename=filename,
                overall_score=(
                    float(cached["overall_score"])
                    if cached.get("overall_score") is not None
                    else None
                ),
            )
            if result:
                if hasattr(result, "meta") and result.meta:
                    result.meta.run_id = cached_run_id
                    result.meta.engine = "fabric-cache"

                # If this user is not yet associated with the cached run, link/save extract to user's history
                cached_uemail = str(cached.get("user_email") or "").strip().lower()
                if user_email and cached_uemail != user_email.strip().lower():
                    if on_progress:
                        on_progress("Linking extract to user history…", 0.9)
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
                        logger.warning("Failed to link cached extract for user: %s", save_err)

                if on_progress:
                    on_progress("Loaded from central repository (deduplicated)", 1.0)
                return result
    except Exception as err:
        logger.warning("Repository cache lookup error: %s", err)

    result = await extract_fn(file_bytes, filename, options, on_progress=on_progress)

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

