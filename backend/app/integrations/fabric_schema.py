"""Fabric WH_IDP schema ensures for Phases B–E (logs columns, documents, payloads, notifications)."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from . import fabric_sql

logger = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _invalidate_columns(table: str) -> None:
    fabric_sql._table_column_cache.pop(table, None)
    fabric_sql._table_column_cache.pop(table.lower(), None)


def _add_column_if_missing(conn: Any, table: str, col: str, col_type: str) -> None:
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            IF NOT EXISTS (
                SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_NAME = '{table}' AND COLUMN_NAME = '{col}'
            )
            BEGIN
                ALTER TABLE {table} ADD {col} {col_type} NULL;
            END
            """
        )
        conn.commit()
        cur.close()
        _invalidate_columns(table)
    except Exception as err:
        logger.warning("add column %s.%s notice: %s", table, col, err)


def ensure_extraction_logs_phase_b(conn: Any) -> None:
    """Phase B: real workflow columns + envelope_json on Tbl_PM_Extraction_logs."""
    for col, col_type in [
        ("document_status", "VARCHAR(64)"),
        ("approved_by", "VARCHAR(255)"),
        ("approved_at", "VARCHAR(64)"),
        ("rejection_notes", "VARCHAR(MAX)"),
        ("submitted_by", "VARCHAR(255)"),
        ("assigned_approver", "VARCHAR(255)"),
        ("user_id", "VARCHAR(64)"),
        ("user_email", "VARCHAR(255)"),
        ("user_role", "VARCHAR(32)"),
        ("duration_ms", "INT"),
        ("doc_title", "VARCHAR(512)"),
        ("oem_manufacturer", "VARCHAR(255)"),
        ("equipment_model", "VARCHAR(255)"),
        ("equipment_type", "VARCHAR(255)"),
        ("document_version", "VARCHAR(64)"),
        ("publication_date", "VARCHAR(64)"),
        ("envelope_json", "VARCHAR(MAX)"),
    ]:
        _add_column_if_missing(conn, "Tbl_PM_Extraction_logs", col, col_type)

    # Prefer VARCHAR(MAX) for legacy error LOB (UTF-8 safe). Best-effort.
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = 'Tbl_PM_Extraction_logs' AND COLUMN_NAME = 'error'
            """
        )
        row = cur.fetchone()
        maxlen = int(row[0]) if row and row[0] is not None else None
        if maxlen is not None and maxlen > 0 and maxlen < 100000:
            try:
                cur.execute("ALTER TABLE Tbl_PM_Extraction_logs ALTER COLUMN error VARCHAR(MAX) NULL")
                conn.commit()
                _invalidate_columns("Tbl_PM_Extraction_logs")
                logger.info("Widened Tbl_PM_Extraction_logs.error to VARCHAR(MAX)")
            except Exception as alter_err:
                logger.warning("Widen error column notice: %s", alter_err)
        cur.close()
    except Exception as err:
        logger.debug("error column inspect notice: %s", err)


def ensure_documents_table(conn: Any) -> None:
    """Phase C: one row per unique PDF (content_hash)."""
    try:
        cur = conn.cursor()
        cur.execute(
            """
            IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'Tbl_PM_Documents')
            BEGIN
                CREATE TABLE Tbl_PM_Documents (
                    content_hash       VARCHAR(64)  NOT NULL,
                    canonical_run_id   VARCHAR(64)  NULL,
                    filename           VARCHAR(512) NULL,
                    doc_title          VARCHAR(512) NULL,
                    oem_manufacturer   VARCHAR(255) NULL,
                    equipment_model    VARCHAR(255) NULL,
                    equipment_type     VARCHAR(255) NULL,
                    document_version   VARCHAR(64)  NULL,
                    publication_date   VARCHAR(64)  NULL,
                    global_status      VARCHAR(64)  NOT NULL,
                    approved_by        VARCHAR(255) NULL,
                    approved_at        VARCHAR(64)  NULL,
                    updated_at         VARCHAR(64)  NOT NULL
                )
            END
            """
        )
        conn.commit()
        cur.close()
        _invalidate_columns("Tbl_PM_Documents")
    except Exception as err:
        logger.warning("ensure_documents_table notice: %s", err)


def ensure_extract_payloads_table(conn: Any) -> None:
    """Phase C: full raw/edited JSON payloads per run_id (off the log row)."""
    try:
        cur = conn.cursor()
        cur.execute(
            """
            IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'Tbl_PM_Extract_Payloads')
            BEGIN
                CREATE TABLE Tbl_PM_Extract_Payloads (
                    run_id           VARCHAR(64)  NOT NULL,
                    content_hash     VARCHAR(64)  NULL,
                    raw_payload      VARCHAR(MAX) NULL,
                    edited_payload   VARCHAR(MAX) NULL,
                    updated_at       VARCHAR(64)  NOT NULL
                )
            END
            """
        )
        conn.commit()
        cur.close()
        _invalidate_columns("Tbl_PM_Extract_Payloads")
    except Exception as err:
        logger.warning("ensure_extract_payloads_table notice: %s", err)


def ensure_notifications_table(conn: Any) -> None:
    """Phase E: persistent in-app notifications in Fabric."""
    try:
        cur = conn.cursor()
        cur.execute(
            """
            IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'Tbl_PM_Notifications')
            BEGIN
                CREATE TABLE Tbl_PM_Notifications (
                    notif_id         VARCHAR(64)  NOT NULL,
                    recipient_email  VARCHAR(255) NOT NULL,
                    event_type       VARCHAR(64)  NOT NULL,
                    run_id           VARCHAR(64)  NULL,
                    title            VARCHAR(512) NULL,
                    body             VARCHAR(MAX) NULL,
                    actor_email      VARCHAR(255) NULL,
                    url              VARCHAR(1024) NULL,
                    is_read          VARCHAR(8)   NOT NULL,
                    created_at       VARCHAR(64)  NOT NULL
                )
            END
            """
        )
        conn.commit()
        cur.close()
        _invalidate_columns("Tbl_PM_Notifications")
    except Exception as err:
        logger.warning("ensure_notifications_table notice: %s", err)


def ensure_phase_b_through_e(conn: Any) -> None:
    """Idempotent ensure for Phases B–E tables/columns."""
    ensure_extraction_logs_phase_b(conn)
    ensure_documents_table(conn)
    ensure_extract_payloads_table(conn)
    ensure_notifications_table(conn)
    fabric_sql.ensure_audit_logs_table(conn)


def upsert_document_row(conn: Any, doc: dict[str, Any]) -> None:
    ensure_documents_table(conn)
    ch = str(doc.get("content_hash") or "").strip().lower()
    if not ch:
        return
    now = _iso_now()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT content_hash FROM Tbl_PM_Documents WHERE LOWER(content_hash) = LOWER(?)",
            (ch,),
        )
        exists = cur.fetchone() is not None
        if exists:
            cur.execute(
                """
                UPDATE Tbl_PM_Documents SET
                    canonical_run_id = COALESCE(?, canonical_run_id),
                    filename = COALESCE(?, filename),
                    doc_title = COALESCE(?, doc_title),
                    oem_manufacturer = COALESCE(?, oem_manufacturer),
                    equipment_model = COALESCE(?, equipment_model),
                    equipment_type = COALESCE(?, equipment_type),
                    document_version = COALESCE(?, document_version),
                    publication_date = COALESCE(?, publication_date),
                    global_status = COALESCE(?, global_status),
                    approved_by = COALESCE(?, approved_by),
                    approved_at = COALESCE(?, approved_at),
                    updated_at = ?
                WHERE LOWER(content_hash) = LOWER(?)
                """,
                (
                    doc.get("canonical_run_id"),
                    doc.get("filename"),
                    doc.get("doc_title"),
                    doc.get("oem_manufacturer"),
                    doc.get("equipment_model"),
                    doc.get("equipment_type"),
                    doc.get("document_version"),
                    doc.get("publication_date"),
                    doc.get("global_status"),
                    doc.get("approved_by"),
                    doc.get("approved_at"),
                    now,
                    ch,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO Tbl_PM_Documents (
                    content_hash, canonical_run_id, filename, doc_title, oem_manufacturer,
                    equipment_model, equipment_type, document_version, publication_date,
                    global_status, approved_by, approved_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ch,
                    doc.get("canonical_run_id"),
                    doc.get("filename"),
                    doc.get("doc_title"),
                    doc.get("oem_manufacturer"),
                    doc.get("equipment_model"),
                    doc.get("equipment_type"),
                    doc.get("document_version"),
                    doc.get("publication_date"),
                    doc.get("global_status") or "New",
                    doc.get("approved_by"),
                    doc.get("approved_at"),
                    now,
                ),
            )
        conn.commit()
    finally:
        cur.close()


def get_document_by_hash(conn: Any, content_hash: str) -> Optional[dict[str, Any]]:
    ensure_documents_table(conn)
    ch = (content_hash or "").strip().lower()
    if not ch:
        return None
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT TOP 1 content_hash, canonical_run_id, filename, doc_title, oem_manufacturer,
                   equipment_model, equipment_type, document_version, publication_date,
                   global_status, approved_by, approved_at, updated_at
            FROM Tbl_PM_Documents
            WHERE LOWER(content_hash) = LOWER(?)
            """,
            (ch,),
        )
        row = cur.fetchone()
        if not row or not cur.description:
            return None
        cols = [d[0].lower() for d in cur.description]
        return dict(zip(cols, row))
    finally:
        cur.close()


def upsert_extract_payloads(
    conn: Any,
    *,
    run_id: str,
    content_hash: Optional[str],
    raw_payload: Any,
    edited_payload: Any,
) -> None:
    ensure_extract_payloads_table(conn)
    rid = (run_id or "").strip()
    if not rid:
        return
    now = _iso_now()
    raw_s = raw_payload if isinstance(raw_payload, str) else json.dumps(raw_payload or {}, ensure_ascii=True, default=str)
    edit_s = edited_payload if isinstance(edited_payload, str) else json.dumps(edited_payload or {}, ensure_ascii=True, default=str)
    cur = conn.cursor()
    try:
        # pyodbc + Fabric UTF-8 collation: bind large JSON as VARCHAR(MAX), not legacy text/ntext.
        try:
            import pyodbc as _pyodbc
            varchar_max = (_pyodbc.SQL_VARCHAR, 0, 0)
            cur.setinputsizes([varchar_max, varchar_max, varchar_max, varchar_max, varchar_max])
        except Exception:
            pass
        cur.execute("SELECT run_id FROM Tbl_PM_Extract_Payloads WHERE run_id = ?", (rid,))
        if cur.fetchone():
            try:
                import pyodbc as _pyodbc
                varchar_max = (_pyodbc.SQL_VARCHAR, 0, 0)
                cur.setinputsizes([varchar_max, varchar_max, varchar_max, varchar_max, varchar_max])
            except Exception:
                pass
            cur.execute(
                """
                UPDATE Tbl_PM_Extract_Payloads
                SET content_hash = COALESCE(?, content_hash),
                    raw_payload = COALESCE(CAST(? AS VARCHAR(MAX)), raw_payload),
                    edited_payload = CAST(? AS VARCHAR(MAX)),
                    updated_at = ?
                WHERE run_id = ?
                """,
                (content_hash, raw_s, edit_s, now, rid),
            )
        else:
            try:
                import pyodbc as _pyodbc
                varchar_max = (_pyodbc.SQL_VARCHAR, 0, 0)
                cur.setinputsizes([varchar_max, varchar_max, varchar_max, varchar_max, varchar_max])
            except Exception:
                pass
            cur.execute(
                """
                INSERT INTO Tbl_PM_Extract_Payloads (run_id, content_hash, raw_payload, edited_payload, updated_at)
                VALUES (?, ?, CAST(? AS VARCHAR(MAX)), CAST(? AS VARCHAR(MAX)), ?)
                """,
                (rid, content_hash, raw_s, edit_s, now),
            )
        conn.commit()
    finally:
        cur.close()


def load_extract_payloads(conn: Any, run_id: str) -> Optional[dict[str, Any]]:
    ensure_extract_payloads_table(conn)
    rid = (run_id or "").strip()
    if not rid:
        return None
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT run_id, content_hash, raw_payload, edited_payload, updated_at "
            "FROM Tbl_PM_Extract_Payloads WHERE run_id = ?",
            (rid,),
        )
        row = cur.fetchone()
        if not row or not cur.description:
            return None
        cols = [d[0].lower() for d in cur.description]
        data = dict(zip(cols, row))
        for key in ("raw_payload", "edited_payload"):
            raw = data.get(key)
            if isinstance(raw, str) and raw.startswith("{"):
                try:
                    data[key] = json.loads(raw)
                except Exception:
                    pass
        return data
    finally:
        cur.close()
