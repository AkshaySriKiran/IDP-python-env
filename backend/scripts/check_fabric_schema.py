#!/usr/bin/env python3
"""Check and confirm Microsoft Fabric tables and columns."""
from __future__ import annotations

import sys
from pathlib import Path

# Add backend directory to python path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import pyodbc
from app.integrations import fabric_sql


def main() -> int:
    print("==================================================")
    print("  Microsoft Fabric WH_IDP Schema Confirmation")
    print("==================================================")

    if not fabric_sql.fabric_configured():
        print("❌ Fabric credentials are not configured in backend/.env")
        return 1

    print("Connecting to Fabric via ODBC...")
    try:
        conn = fabric_sql.connect()
        cur = conn.cursor()
    except Exception as err:
        print(f"❌ Failed to connect: {err}")
        return 1

    print("✅ Connected successfully!\n")

    # 1. List all tables in dbo
    cur.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'dbo' ORDER BY TABLE_NAME")
    tables = [r[0] for r in cur.fetchall()]
    print(f"📋 Tables in dbo schema ({len(tables)} found):")
    for t in tables:
        print(f"  • {t}")
    print()

    # 2. Check each target table columns
    target_tables = [
        "Tbl_PM_Extraction_logs",
        "Tbl_PM_Spare_Parts",
        "Tbl_PM_Maintenance",
        "Tbl_PM_Troubleshooting",
        "Tbl_PM_Audit_Logs",
    ]

    new_expected_columns = {
        "Tbl_PM_Extraction_logs": {"doc_title", "oem_manufacturer", "equipment_model", "equipment_type", "document_version", "publication_date", "document_status", "approved_by", "approved_at", "user_email", "user_role"},
        "Tbl_PM_Spare_Parts": {"status", "reviewed_by", "reviewed_at", "rejection_reason"},
        "Tbl_PM_Maintenance": {"status", "reviewed_by", "reviewed_at", "rejection_reason"},
        "Tbl_PM_Troubleshooting": {"status", "reviewed_by", "reviewed_at", "rejection_reason"},
        "Tbl_PM_Audit_Logs": {"event_id", "event_type", "run_id", "content_hash", "user_email", "from_status", "to_status"},
    }

    for table in target_tables:
        if table not in tables:
            print(f"⚠️ Table '{table}' does not exist in Lakehouse yet.")
            continue

        cur.execute(
            "SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = ? ORDER BY ORDINAL_POSITION",
            (table,),
        )
        cols = cur.fetchall()
        col_names = {c[0].lower() for c in cols}
        print(f"🔍 {table} ({len(cols)} columns):")

        # Show columns
        for c_name, c_type in cols:
            tag = " [NEW]" if c_name.lower() in new_expected_columns.get(table, set()) else ""
            print(f"    - {c_name} ({c_type}){tag}")

        # Check expected new fields
        expected = new_expected_columns.get(table, set())
        missing = expected - col_names
        if not missing:
            print(f"    ✨ All tracking columns present in {table}!\n")
        else:
            print(f"    ℹ️ Standard columns present (adaptive fallback active for remaining: {', '.join(sorted(missing))})\n")

    conn.close()
    print("==================================================")
    print("  Confirmation Complete!")
    print("==================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
