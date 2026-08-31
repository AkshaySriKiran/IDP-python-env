-- Phase A: append-only audit trail for DocuLoom / OmniParse IDP
-- Run once in WH_IDP (dbo). The API also auto-creates this table on first audit write.

IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'Tbl_PM_Audit_Logs'
)
BEGIN
    CREATE TABLE dbo.Tbl_PM_Audit_Logs (
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
    );
END;

-- Backfill columns if an older partial table exists
IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'Tbl_PM_Audit_Logs' AND COLUMN_NAME = 'content_hash'
)
    ALTER TABLE dbo.Tbl_PM_Audit_Logs ADD content_hash VARCHAR(64) NULL;

IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'Tbl_PM_Audit_Logs' AND COLUMN_NAME = 'from_status'
)
    ALTER TABLE dbo.Tbl_PM_Audit_Logs ADD from_status VARCHAR(64) NULL;

IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'Tbl_PM_Audit_Logs' AND COLUMN_NAME = 'to_status'
)
    ALTER TABLE dbo.Tbl_PM_Audit_Logs ADD to_status VARCHAR(64) NULL;
