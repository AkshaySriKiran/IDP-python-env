-- Phases B–E: WH_IDP schema for DocuLoom / OmniParse IDP
-- Safe to re-run. The API also auto-creates these on first write.

-- Phase B: workflow columns + envelope_json on extraction logs
IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='Tbl_PM_Extraction_logs' AND COLUMN_NAME='document_status')
    ALTER TABLE dbo.Tbl_PM_Extraction_logs ADD document_status VARCHAR(64) NULL;
IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='Tbl_PM_Extraction_logs' AND COLUMN_NAME='approved_by')
    ALTER TABLE dbo.Tbl_PM_Extraction_logs ADD approved_by VARCHAR(255) NULL;
IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='Tbl_PM_Extraction_logs' AND COLUMN_NAME='approved_at')
    ALTER TABLE dbo.Tbl_PM_Extraction_logs ADD approved_at VARCHAR(64) NULL;
IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='Tbl_PM_Extraction_logs' AND COLUMN_NAME='rejection_notes')
    ALTER TABLE dbo.Tbl_PM_Extraction_logs ADD rejection_notes VARCHAR(MAX) NULL;
IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='Tbl_PM_Extraction_logs' AND COLUMN_NAME='submitted_by')
    ALTER TABLE dbo.Tbl_PM_Extraction_logs ADD submitted_by VARCHAR(255) NULL;
IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='Tbl_PM_Extraction_logs' AND COLUMN_NAME='assigned_approver')
    ALTER TABLE dbo.Tbl_PM_Extraction_logs ADD assigned_approver VARCHAR(255) NULL;
IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='Tbl_PM_Extraction_logs' AND COLUMN_NAME='user_id')
    ALTER TABLE dbo.Tbl_PM_Extraction_logs ADD user_id VARCHAR(64) NULL;
IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='Tbl_PM_Extraction_logs' AND COLUMN_NAME='user_email')
    ALTER TABLE dbo.Tbl_PM_Extraction_logs ADD user_email VARCHAR(255) NULL;
IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='Tbl_PM_Extraction_logs' AND COLUMN_NAME='user_role')
    ALTER TABLE dbo.Tbl_PM_Extraction_logs ADD user_role VARCHAR(32) NULL;
IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='Tbl_PM_Extraction_logs' AND COLUMN_NAME='envelope_json')
    ALTER TABLE dbo.Tbl_PM_Extraction_logs ADD envelope_json VARCHAR(MAX) NULL;
IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='Tbl_PM_Extraction_logs' AND COLUMN_NAME='doc_title')
    ALTER TABLE dbo.Tbl_PM_Extraction_logs ADD doc_title VARCHAR(512) NULL;

-- Optional: widen legacy error column (Phase D leaves it NULL on success)
-- ALTER TABLE dbo.Tbl_PM_Extraction_logs ALTER COLUMN error VARCHAR(MAX) NULL;

-- Phase C: canonical document registry
IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'Tbl_PM_Documents')
BEGIN
    CREATE TABLE dbo.Tbl_PM_Documents (
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
    );
END;

-- Phase C/D: payloads off the log row
IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'Tbl_PM_Extract_Payloads')
BEGIN
    CREATE TABLE dbo.Tbl_PM_Extract_Payloads (
        run_id           VARCHAR(64)  NOT NULL,
        content_hash     VARCHAR(64)  NULL,
        raw_payload      VARCHAR(MAX) NULL,
        edited_payload   VARCHAR(MAX) NULL,
        updated_at       VARCHAR(64)  NOT NULL
    );
END;

-- Phase E: notifications
IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'Tbl_PM_Notifications')
BEGIN
    CREATE TABLE dbo.Tbl_PM_Notifications (
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
    );
END;
