from __future__ import annotations

import json
import logging
import os
import struct
import time
import urllib.parse
import urllib.request
from typing import Any
try:
    import pyodbc
except ImportError:
    pyodbc = None  # type: ignore

logger = logging.getLogger(__name__)

SQL_COPT_SS_ACCESS_TOKEN = 1256
_cached_token: tuple[bytes, float] | None = None

# Strict allowlist for table and column names to prevent SQL injection
ALLOWED_TABLES = {
    "Tbl_PM_Extraction_logs",
    "Tbl_PM_Spare_Parts",
    "Tbl_PM_Maintenance",
    "Tbl_PM_Troubleshooting",
    "Tbl_PM_Audit_Logs",
    "Tbl_PM_Users",
    "Tbl_PM_App_Config",
}

ALLOWED_COLUMNS = {
    # Core identifiers & run telemetry
    "run_id", "drive_item_id", "etag", "content_hash", "filename", "status", "overall_score",
    "maintenance_count", "spare_parts_count", "troubleshooting_count", "engine", "parse_strategy",
    "extracted_at", "error",
    # Document Metadata (Scenario 6)
    "doc_title", "oem_manufacturer", "equipment_model", "equipment_type",
    "document_version", "publication_date",
    # Document Review & Sign-Off (Scenario 8)
    "document_status", "approved_by", "approved_at", "rejection_notes",
    # User & Execution Telemetry (Scenario 1 & 3)
    "user_id", "user_email", "user_role", "duration_ms", "pages_total", "pages_processed",
    "model_name", "grounding_pass_rate", "filter_drop_rate", "low_confidence_count",
    # Data columns
    "equipment_title", "subsystem_location", "subsystem_component",
    "item_no", "part_name", "part_number_code", "drawing_model_no", "oem_standard_body",
    "part_categorization", "quantity", "recommended_stock_qty", "warranty_period",
    "frequency_of_use", "page", "pdf_order", "confidence", "fields_filled_score",
    "page_match_score", "grounding_available", "quality_reasons", "ai_extract_text",
    "maintenance_routine", "checks_instructions", "date", "maintenance_work_description",
    "parts_renewed", "attended_by", "remarks", "problem", "root_cause_solution",
    # Row Review & Audit
    "reviewed_by", "reviewed_at", "rejection_reason",
    # Audit log columns
    "event_id", "event_type", "from_status", "to_status", "details_json", "created_at",
    # Users Table columns (Fabric User Directory)
    "email", "display_name", "role", "copilot_daily_limit", "preferred_model",
    "allowed_models", "password_hash", "assigned_approver", "sharepoint_folder", "updated_at",
    # App Config columns
    "config_key", "config_value",
}

_table_column_cache: dict[str, set[str]] = {}


def _get_table_columns(conn: pyodbc.Connection, table: str) -> set[str]:
    if table in _table_column_cache:
        return _table_column_cache[table]
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = ?",
            (table,),
        )
        cols = {row[0].lower() for row in cur.fetchall()}
        cur.close()
        if cols:
            _table_column_cache[table] = cols
            return cols
    except Exception as err:
        logger.debug("Failed to inspect columns for table %s: %s", table, err)
    return set()


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip().strip('"').strip("'")


def fabric_configured() -> bool:
    server = _env("SQL_SERVER") or _env("FABRIC_SQL_SERVER")
    database = _env("SQL_DATABASE") or _env("FABRIC_SQL_DATABASE") or "WH_IDP"
    client_id = _env("AZURE_CLIENT_ID")
    secret = _env("AZURE_CLIENT_SECRET")
    return bool(server and database and client_id and secret)


def _get_azure_token() -> bytes | None:
    global _cached_token
    now = time.time()
    if _cached_token and _cached_token[1] > now + 300:
        return _cached_token[0]

    tenant = _env("AZURE_TENANT_ID")
    client_id = _env("AZURE_CLIENT_ID")
    client_secret = _env("AZURE_CLIENT_SECRET")
    if not (tenant and client_id and client_secret):
        return None

    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
        "scope": "https://database.windows.net//.default",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            token = res.get("access_token")
            expires_in = int(res.get("expires_in") or 3600)
            if not token:
                return None
            token_bytes = token.encode("utf-16-le")
            token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
            _cached_token = (token_struct, now + expires_in)
            return token_struct
    except Exception as err:
        logger.warning("Failed to obtain Azure AD token for Fabric SQL: %s", err)
        return None


def connect() -> Any:
    if pyodbc is None:
        raise RuntimeError("pyodbc module is not installed.")
    server = _env("SQL_SERVER") or _env("FABRIC_SQL_SERVER")
    database = _env("SQL_DATABASE") or _env("FABRIC_SQL_DATABASE") or "WH_IDP"
    driver = _env("SQL_DRIVER") or "ODBC Driver 18 for SQL Server"
    port = _env("SQL_PORT") or "1433"
    encrypt = _env("SQL_ENCRYPT") or "yes"
    trust = _env("SQL_TRUST_SERVER_CERTIFICATE") or "no"

    token_struct = _get_azure_token()
    if token_struct:
        conn_str = (
            f"DRIVER={{{driver}}};"
            f"SERVER={server},{port};"
            f"DATABASE={database};"
            f"Encrypt={encrypt};"
            f"TrustServerCertificate={trust};"
            f"Connection Timeout=30;"
        )
        try:
            return pyodbc.connect(
                conn_str,
                attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct},
                timeout=60,
            )
        except Exception as err:
            logger.warning("Token connection to Fabric failed, falling back to connection string: %s", err)

    auth = _env("SQL_AUTHENTICATION") or "ActiveDirectoryServicePrincipal"
    use_app = _env("SQL_USE_API_APP_CREDENTIALS", "true").lower() in {"1", "true", "yes"}
    uid = _env("SQL_USERNAME") or (_env("AZURE_CLIENT_ID") if use_app else "")
    pwd = _env("SQL_PASSWORD") or (_env("AZURE_CLIENT_SECRET") if use_app else "")
    conn_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER={server},{port};"
        f"DATABASE={database};"
        f"Encrypt={encrypt};"
        f"TrustServerCertificate={trust};"
        f"Authentication={auth};"
        f"UID={uid};"
        f"PWD={pwd};"
        f"LoginTimeout=60;"
        f"Connection Timeout=60;"
    )
    return pyodbc.connect(conn_str, timeout=60)


def insert_log(conn: pyodbc.Connection, row: dict[str, Any]) -> None:
    table = "Tbl_PM_Extraction_logs"
    known_cols = _get_table_columns(conn, table)
    
    # Filter columns to only those allowed AND existing in table (if schema is known)
    valid_cols = [
        c for c in row.keys()
        if c in ALLOWED_COLUMNS and (not known_cols or c.lower() in known_cols)
    ]
    if not valid_cols:
        # Fallback to core baseline columns
        valid_cols = [
            "run_id", "drive_item_id", "etag", "content_hash", "filename", "status",
            "overall_score", "maintenance_count", "spare_parts_count", "troubleshooting_count",
            "engine", "parse_strategy", "extracted_at", "error"
        ]

    col_str = ", ".join(valid_cols)
    placeholders = ", ".join("?" for _ in valid_cols)
    sql = f"INSERT INTO {table} ({col_str}) VALUES ({placeholders})"
    values = [row.get(c) for c in valid_cols]

    cur = conn.cursor()
    cur.execute(sql, values)
    conn.commit()
    cur.close()


def insert_many(
    conn: pyodbc.Connection,
    table: str,
    columns: list[str],
    rows: Iterable[dict[str, Any]],
) -> int:
    if table not in ALLOWED_TABLES:
        raise ValueError(f"Disallowed table name: '{table}'")
    
    known_cols = _get_table_columns(conn, table)
    active_cols = [
        c for c in columns
        if c in ALLOWED_COLUMNS and (not known_cols or c.lower() in known_cols)
    ]
    if not active_cols:
        active_cols = [c for c in columns if c in ALLOWED_COLUMNS]

    rows = list(rows)
    if not rows:
        return 0
    placeholders = ",".join("?" for _ in active_cols)
    col_sql = ",".join(active_cols)
    sql = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})"
    cur = conn.cursor()
    try:
        cur.fast_executemany = True
    except Exception:
        pass
    cur.executemany(sql, [tuple(r.get(c) for c in active_cols) for r in rows])
    conn.commit()
    cur.close()
    return len(rows)


def ensure_audit_logs_table(conn: pyodbc.Connection) -> None:
    """Ensures Tbl_PM_Audit_Logs exists with Phase A audit columns in Fabric SQL Warehouse."""
    try:
        cur = conn.cursor()
        cur.execute("""
        IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'Tbl_PM_Audit_Logs')
        BEGIN
            CREATE TABLE Tbl_PM_Audit_Logs (
                event_id       VARCHAR(64)  NOT NULL,
                event_type     VARCHAR(64)  NOT NULL,
                run_id         VARCHAR(64)  NULL,
                content_hash   VARCHAR(64)  NULL,
                filename       VARCHAR(512) NULL,
                user_id        VARCHAR(64)  NULL,
                user_email     VARCHAR(255) NULL,
                user_role      VARCHAR(32)  NULL,
                from_status    VARCHAR(64)  NULL,
                to_status      VARCHAR(64)  NULL,
                details_json   VARCHAR(MAX) NULL,
                created_at     VARCHAR(64)  NOT NULL
            )
        END
        """)
        conn.commit()
        cur.close()
    except Exception as err:
        logger.warning("ensure_audit_logs_table create table notice: %s", err)

    for col, col_type in [
        ("content_hash", "VARCHAR(64)"),
        ("from_status", "VARCHAR(64)"),
        ("to_status", "VARCHAR(64)"),
    ]:
        try:
            cur = conn.cursor()
            cur.execute(f"""
            IF NOT EXISTS (
                SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_NAME = 'Tbl_PM_Audit_Logs' AND COLUMN_NAME = '{col}'
            )
            BEGIN
                ALTER TABLE Tbl_PM_Audit_Logs ADD {col} {col_type} NULL;
            END
            """)
            conn.commit()
            cur.close()
        except Exception as err:
            logger.warning("ensure_audit_logs_table add %s notice: %s", col, err)


def _audit_created_at(value: Any) -> str:
    if value is None:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def insert_audit_event(conn: pyodbc.Connection, event: dict[str, Any]) -> None:
    """Append one row to Tbl_PM_Audit_Logs (creates table if missing)."""
    ensure_audit_logs_table(conn)
    row = dict(event)
    if "created_at" in row:
        row["created_at"] = _audit_created_at(row["created_at"])
    cols = [c for c in row.keys() if c in ALLOWED_COLUMNS]
    if not cols:
        logger.warning("insert_audit_event skipped: no allowed columns in %s", list(event.keys()))
        return
    try:
        insert_many(conn, "Tbl_PM_Audit_Logs", cols, [row])
        logger.info(
            "Audit event %s run_id=%s content_hash=%s",
            row.get("event_type"),
            row.get("run_id"),
            row.get("content_hash"),
        )
    except Exception as err:
        logger.warning("insert_audit_event failed: %s", err)
        raise


def ensure_users_table(conn: pyodbc.Connection) -> None:
    """Ensures Tbl_PM_Users exists and has all required columns in Fabric SQL Warehouse."""
    try:
        cur = conn.cursor()
        cur.execute("""
        IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'Tbl_PM_Users')
        BEGIN
            CREATE TABLE Tbl_PM_Users (
                user_id VARCHAR(64) NOT NULL,
                email VARCHAR(255) NOT NULL,
                display_name VARCHAR(255) NULL,
                role VARCHAR(32) NOT NULL,
                status VARCHAR(32) NOT NULL,
                copilot_daily_limit INT NOT NULL,
                preferred_model VARCHAR(64) NULL,
                allowed_models VARCHAR(512) NULL,
                password_hash VARCHAR(255) NULL,
                assigned_approver VARCHAR(255) NULL,
                sharepoint_folder VARCHAR(512) NULL,
                created_at VARCHAR(64) NOT NULL,
                updated_at VARCHAR(64) NOT NULL
            )
        END
        """)
        conn.commit()
        cur.close()
    except Exception as err:
        logger.warning("ensure_users_table create table notice: %s", err)

    for col, col_type in [("assigned_approver", "VARCHAR(255)"), ("sharepoint_folder", "VARCHAR(512)")]:
        try:
            cur = conn.cursor()
            cur.execute(f"""
            IF NOT EXISTS (
                SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_NAME = 'Tbl_PM_Users' AND COLUMN_NAME = '{col}'
            )
            BEGIN
                ALTER TABLE Tbl_PM_Users ADD {col} {col_type} NULL;
            END
            """)
            conn.commit()
            cur.close()
        except Exception as err:
            logger.warning("ensure_users_table add %s notice: %s", col, err)


def get_user_by_email(conn: pyodbc.Connection, email: str) -> Optional[dict[str, Any]]:
    ensure_users_table(conn)
    email_clean = email.strip().lower()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM Tbl_PM_Users WHERE LOWER(email) = ?", (email_clean,))
        row = cur.fetchone()
        if not row or not cur.description:
            return None
        cols = [d[0].lower() for d in cur.description]
        rdict = dict(zip(cols, row))
        allowed_str = rdict.get("allowed_models") or ""
        allowed_list = [m.strip() for m in str(allowed_str).split(",") if m.strip()] if allowed_str else []
        uid = str(rdict.get("user_id") or rdict.get("id") or "")
        return {
            "id": uid,
            "user_id": uid,
            "email": str(rdict.get("email") or "").strip().lower(),
            "display_name": str(rdict.get("display_name") or ""),
            "role": str(rdict.get("role") or "editor"),
            "status": str(rdict.get("status") or "active"),
            "copilot_daily_limit": int(rdict.get("copilot_daily_limit") or 5),
            "preferred_model": str(rdict.get("preferred_model") or "gemini-3.6-flash"),
            "allowed_models": allowed_list,
            "password_hash": str(rdict.get("password_hash") or ""),
            "assigned_approver": str(rdict.get("assigned_approver")) if rdict.get("assigned_approver") else None,
            "sharepoint_folder": str(rdict.get("sharepoint_folder")) if rdict.get("sharepoint_folder") else None,
            "created_at": str(rdict.get("created_at") or ""),
            "updated_at": str(rdict.get("updated_at") or ""),
        }
    finally:
        cur.close()


def get_user_by_id(conn: pyodbc.Connection, user_id: str) -> Optional[dict[str, Any]]:
    ensure_users_table(conn)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM Tbl_PM_Users WHERE user_id = ?", (str(user_id).strip(),))
        row = cur.fetchone()
        if not row or not cur.description:
            return None
        cols = [d[0].lower() for d in cur.description]
        rdict = dict(zip(cols, row))
        allowed_str = rdict.get("allowed_models") or ""
        allowed_list = [m.strip() for m in str(allowed_str).split(",") if m.strip()] if allowed_str else []
        uid = str(rdict.get("user_id") or rdict.get("id") or "")
        return {
            "id": uid,
            "user_id": uid,
            "email": str(rdict.get("email") or "").strip().lower(),
            "display_name": str(rdict.get("display_name") or ""),
            "role": str(rdict.get("role") or "editor"),
            "status": str(rdict.get("status") or "active"),
            "copilot_daily_limit": int(rdict.get("copilot_daily_limit") or 5),
            "preferred_model": str(rdict.get("preferred_model") or "gemini-3.6-flash"),
            "allowed_models": allowed_list,
            "password_hash": str(rdict.get("password_hash") or ""),
            "assigned_approver": str(rdict.get("assigned_approver")) if rdict.get("assigned_approver") else None,
            "sharepoint_folder": str(rdict.get("sharepoint_folder")) if rdict.get("sharepoint_folder") else None,
            "created_at": str(rdict.get("created_at") or ""),
            "updated_at": str(rdict.get("updated_at") or ""),
        }
    finally:
        cur.close()


def list_users_from_fabric(conn: pyodbc.Connection) -> list[dict[str, Any]]:
    ensure_users_table(conn)
    cur = conn.cursor()
    users = []
    try:
        cur.execute("SELECT * FROM Tbl_PM_Users")
        if not cur.description:
            logger.info("list_users_from_fabric: cur.description is empty")
            return []
        cols = [d[0].lower() for d in cur.description]
        rows = cur.fetchall()
        logger.info("list_users_from_fabric: fetched %d rows, cols=%s", len(rows), cols)
        for row in rows:
            if not row:
                continue
            rdict = dict(zip(cols, row))
            allowed_str = rdict.get("allowed_models") or ""
            allowed_list = [m.strip() for m in str(allowed_str).split(",") if m.strip()] if allowed_str else []
            uid = str(rdict.get("user_id") or rdict.get("id") or "")
            users.append({
                "id": uid,
                "user_id": uid,
                "email": str(rdict.get("email") or "").strip().lower(),
                "display_name": str(rdict.get("display_name") or ""),
                "role": str(rdict.get("role") or "editor"),
                "status": str(rdict.get("status") or "active"),
                "copilot_daily_limit": int(rdict.get("copilot_daily_limit") or 5),
                "preferred_model": str(rdict.get("preferred_model") or "gemini-3.6-flash"),
                "allowed_models": allowed_list,
                "password_hash": str(rdict.get("password_hash") or ""),
                "assigned_approver": str(rdict.get("assigned_approver")) if rdict.get("assigned_approver") else None,
                "sharepoint_folder": str(rdict.get("sharepoint_folder")) if rdict.get("sharepoint_folder") else None,
                "created_at": str(rdict.get("created_at") or ""),
                "updated_at": str(rdict.get("updated_at") or ""),
            })
        return users
    except Exception as err:
        logger.warning("list_users_from_fabric query error: %s", err, exc_info=True)
        raise
    finally:
        cur.close()


def upsert_user_in_fabric(conn: pyodbc.Connection, user: dict[str, Any]) -> bool:
    ensure_users_table(conn)
    user_id = str(user.get("id") or user.get("user_id") or "")
    email = str(user.get("email") or "").strip().lower()
    if not email:
        return False
    
    allowed = user.get("allowed_models")
    if isinstance(allowed, list):
        allowed_str = ",".join(allowed)
    else:
        allowed_str = str(allowed or "")

    existing = get_user_by_email(conn, email)
    cur = conn.cursor()
    try:
        if existing:
            sql = """
            UPDATE Tbl_PM_Users
            SET display_name = ?, role = ?, status = ?, copilot_daily_limit = ?,
                preferred_model = ?, allowed_models = ?, assigned_approver = ?,
                sharepoint_folder = ?, updated_at = ?
            WHERE LOWER(email) = ?
            """
            cur.execute(
                sql,
                (
                    user.get("display_name") or existing.get("display_name") or "",
                    user.get("role") or existing.get("role") or "editor",
                    user.get("status") or existing.get("status") or "active",
                    int(user.get("copilot_daily_limit") or existing.get("copilot_daily_limit") or 5),
                    user.get("preferred_model") or existing.get("preferred_model") or "gemini-3.6-flash",
                    allowed_str or existing.get("allowed_models") or "gemini-3.6-flash",
                    user.get("assigned_approver") if "assigned_approver" in user else existing.get("assigned_approver"),
                    user.get("sharepoint_folder") if "sharepoint_folder" in user else existing.get("sharepoint_folder"),
                    user.get("updated_at") or "",
                    email,
                ),
            )
        else:
            sql = """
            INSERT INTO Tbl_PM_Users (
                user_id, email, display_name, role, status, copilot_daily_limit,
                preferred_model, allowed_models, password_hash, assigned_approver,
                sharepoint_folder, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            cur.execute(
                sql,
                (
                    user_id,
                    email,
                    user.get("display_name") or "",
                    user.get("role") or "editor",
                    user.get("status") or "active",
                    int(user.get("copilot_daily_limit") or 5),
                    user.get("preferred_model") or "gemini-3.6-flash",
                    allowed_str,
                    user.get("password_hash") or "",
                    user.get("assigned_approver"),
                    user.get("sharepoint_folder"),
                    user.get("created_at") or "",
                    user.get("updated_at") or "",
                ),
            )
        conn.commit()
        return True
    finally:
        cur.close()


def ensure_app_config_table(conn: pyodbc.Connection) -> None:
    """Ensures Tbl_PM_App_Config exists in Fabric SQL Warehouse."""
    sql = """
    IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'Tbl_PM_App_Config')
    BEGIN
        CREATE TABLE Tbl_PM_App_Config (
            config_key VARCHAR(64) NOT NULL,
            config_value VARCHAR(4000) NOT NULL,
            updated_at VARCHAR(64) NOT NULL
        )
    END
    """
    try:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()
        cur.close()
    except Exception as err:
        logger.debug("ensure_app_config_table notice: %s", err)


def get_app_config(conn: pyodbc.Connection, key: str) -> Optional[str]:
    ensure_app_config_table(conn)
    sql = "SELECT config_value FROM Tbl_PM_App_Config WHERE config_key = ?"
    cur = conn.cursor()
    try:
        cur.execute(sql, (str(key).strip(),))
        row = cur.fetchone()
        if row:
            return str(row[0])
        return None
    finally:
        cur.close()


def set_app_config(conn: pyodbc.Connection, key: str, value: str) -> bool:
    ensure_app_config_table(conn)
    key_clean = str(key).strip()
    val_clean = str(value)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    
    existing = get_app_config(conn, key_clean)
    cur = conn.cursor()
    try:
        if existing is not None:
            sql = "UPDATE Tbl_PM_App_Config SET config_value = ?, updated_at = ? WHERE config_key = ?"
            cur.execute(sql, (val_clean, now, key_clean))
        else:
            sql = "INSERT INTO Tbl_PM_App_Config (config_key, config_value, updated_at) VALUES (?, ?, ?)"
            cur.execute(sql, (key_clean, val_clean, now))
        conn.commit()
        return True
    finally:
        cur.close()



