import unittest
from unittest.mock import patch, MagicMock
from app.auth.schemas import UserPublic
from app.auth import store
from app.auth.deps import require_admin, require_approver, require_editor
from app.models import (
    DocumentMetadata,
    ExtractMeta,
    ExtractResponse,
    MaintenanceRow,
    SparePartRow,
    TroubleshootingRow,
)
from app.extractors.parse import process_raw_model_response
from fastapi import HTTPException

try:
    import pytest
except ImportError:
    class _PytestShim:
        @staticmethod
        def raises(expected_exception):
            import contextlib
            @contextlib.contextmanager
            def _cm():
                try:
                    yield
                except expected_exception:
                    pass
                else:
                    raise AssertionError(f"Expected {expected_exception} but nothing was raised")
            return _cm()
    pytest = _PytestShim()


class TestEnterpriseEnhancements(unittest.TestCase):
    def test_document_metadata_model(self):
        meta = DocumentMetadata(
            title="Centrifugal Pump Manual",
            oem_manufacturer="Grundfos",
            equipment_model="CRN 32-4",
            equipment_type="Centrifugal Pump",
            document_version="Rev 3.0",
            publication_date="2023-11",
        )
        assert meta.title == "Centrifugal Pump Manual"
        assert meta.oem_manufacturer == "Grundfos"
        assert meta.equipment_model == "CRN 32-4"
        assert meta.equipment_type == "Centrifugal Pump"
        assert meta.document_version == "Rev 3.0"
        assert meta.publication_date == "2023-11"

    def test_row_review_lifecycle_defaults_and_transitions(self):
        row = MaintenanceRow(
            id=1,
            equipment_title="Main Pump",
            subsystem_component="Impeller",
            maintenance_routine="Monthly",
            checks_instructions="Inspect wear ring clearance",
        )
        assert row.status == "Pending Review"
        assert row.reviewed_by is None
        assert row.rejection_reason is None

        # Approval transition
        row.status = "Approved"
        row.reviewed_by = "lead.engineer@company.com"
        row.reviewed_at = "2026-08-21T10:00:00Z"
        assert row.status == "Approved"
        assert row.reviewed_by == "lead.engineer@company.com"

        # Rejection transition
        row.status = "Rejected"
        row.rejection_reason = "Unverifiable on source page"
        assert row.status == "Rejected"
        assert row.rejection_reason == "Unverifiable on source page"

    def test_doc_metadata_parsing_from_model_json(self):
        raw_json = """
        {
          "doc_metadata": {
            "title": "Air Compressor Technical Manual",
            "oem_manufacturer": "Atlas Copco",
            "equipment_model": "GA 90 VSD",
            "equipment_type": "Rotary Screw Compressor",
            "document_version": "Ed. 04",
            "publication_date": "2022-05"
          },
          "maintenance": [
            {
              "equipment_title": "GA 90 VSD",
              "subsystem_component": "Oil Filter",
              "maintenance_routine": "Every 4000 Hours",
              "checks_instructions": "Replace oil filter element and seal ring"
            }
          ],
          "spare_parts": [],
          "troubleshooting": []
        }
        """
        output = process_raw_model_response(
            raw_response_text=raw_json,
            doc_name="Atlas_Copco_GA90.pdf",
            page_num=1,
            has_image=False,
            source_text="Atlas Copco GA 90 VSD Rotary Screw Compressor Manual Ed. 04. Maintenance: Oil Filter Every 4000 Hours Replace oil filter element and seal ring",
        )

        assert "_doc_metadata" in output
        doc_meta = output["_doc_metadata"]
        assert doc_meta["oem_manufacturer"] == "Atlas Copco"
        assert doc_meta["equipment_model"] == "GA 90 VSD"
        assert len(output["maintenance"]) == 1

    def test_role_dependencies(self):
        admin_user = UserPublic(
            id="u1",
            email="admin@corp.com",
            role="admin",
            status="active",
            preferred_model="gemini-3.6-flash",
        )
        approver_user = UserPublic(
            id="u2",
            email="approver@corp.com",
            role="approver",
            status="active",
            preferred_model="gemini-3.6-flash",
        )
        editor_user = UserPublic(
            id="u3",
            email="editor@corp.com",
            role="editor",
            status="active",
            preferred_model="gemini-3.6-flash",
        )
        viewer_user = UserPublic(
            id="u4",
            email="viewer@corp.com",
            role="viewer",
            status="active",
            preferred_model="gemini-3.6-flash",
        )

        # require_admin
        import asyncio
        assert asyncio.run(require_admin(admin_user)) == admin_user
        with pytest.raises(HTTPException):
            asyncio.run(require_admin(approver_user))

        # require_approver
        assert asyncio.run(require_approver(admin_user)) == admin_user
        assert asyncio.run(require_approver(approver_user)) == approver_user
        with pytest.raises(HTTPException):
            asyncio.run(require_approver(editor_user))
        with pytest.raises(HTTPException):
            asyncio.run(require_approver(viewer_user))

        # require_editor
        assert asyncio.run(require_editor(admin_user)) == admin_user
        assert asyncio.run(require_editor(approver_user)) == approver_user
        assert asyncio.run(require_editor(editor_user)) == editor_user
        with pytest.raises(HTTPException):
            asyncio.run(require_editor(viewer_user))

    def test_fabric_sql_allowed_identifiers(self):
        from app.integrations.fabric_sql import ALLOWED_TABLES, ALLOWED_COLUMNS
        assert "Tbl_PM_Audit_Logs" in ALLOWED_TABLES
        assert "Tbl_PM_Extraction_logs" in ALLOWED_TABLES
        assert "Tbl_PM_Documents" in ALLOWED_TABLES
        assert "Tbl_PM_Extract_Payloads" in ALLOWED_TABLES
        assert "Tbl_PM_Notifications" in ALLOWED_TABLES
        assert "doc_title" in ALLOWED_COLUMNS
        assert "oem_manufacturer" in ALLOWED_COLUMNS
        assert "document_status" in ALLOWED_COLUMNS
        assert "approved_by" in ALLOWED_COLUMNS
        assert "reviewed_by" in ALLOWED_COLUMNS
        assert "rejection_reason" in ALLOWED_COLUMNS
        assert "user_role" in ALLOWED_COLUMNS
        assert "envelope_json" in ALLOWED_COLUMNS
        assert "submitted_by" in ALLOWED_COLUMNS
        assert "canonical_run_id" in ALLOWED_COLUMNS
        assert "notif_id" in ALLOWED_COLUMNS

    def test_ensure_audit_logs_table_creates_and_inserts(self):
        from unittest.mock import MagicMock, patch
        from app.integrations.fabric_sql import ensure_audit_logs_table, insert_audit_event

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        ensure_audit_logs_table(mock_conn)
        self.assertGreaterEqual(mock_cur.execute.call_count, 1)
        mock_conn.commit.assert_called()

        with patch("app.integrations.fabric_sql.insert_many") as mock_insert:
            insert_audit_event(
                mock_conn,
                {
                    "event_id": "evt-1",
                    "event_type": "EXTRACT_COMPLETE",
                    "run_id": "run-1",
                    "content_hash": "abc123",
                    "to_status": "Pending Review",
                    "created_at": "2026-08-31T12:00:00Z",
                },
            )
            mock_insert.assert_called_once()
            args = mock_insert.call_args[0]
            self.assertEqual(args[1], "Tbl_PM_Audit_Logs")
            self.assertEqual(args[3][0]["content_hash"], "abc123")
            self.assertEqual(args[3][0]["to_status"], "Pending Review")

    def test_fabric_extract_response_rehydration(self):
        from app.integrations.fabric_cache import load_extract_from_fabric
        from unittest.mock import patch, MagicMock

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        # Mock Tbl_PM_Spare_Parts
        mock_cur.description = [("equipment_title",), ("part_name",), ("part_number_code",), ("status",), ("reviewed_by",), ("rejection_reason",)]
        mock_cur.fetchall.side_effect = [
            # spares
            [("Compressor", "Filter Element", "FE-100", "Approved", "approver@corp.com", None)],
            # maint
            [],
            # trouble
            [],
        ]

        with patch("app.integrations.fabric_sql.connect", return_value=mock_conn), \
             patch("app.integrations.fabric_cache.get_done_run", return_value={
                 "run_id": "run-123",
                 "doc_title": "Atlas Copco Compressor Manual",
                 "oem_manufacturer": "Atlas Copco",
                 "equipment_model": "GA 90",
                 "document_status": "Approved",
                 "approved_by": "lead.approver@corp.com",
                 "overall_score": 95.0,
             }):
            res = load_extract_from_fabric("run-123", filename="test.pdf")
            assert res.meta.doc_metadata is not None
            assert res.meta.doc_metadata.title == "Atlas Copco Compressor Manual"
            assert res.meta.doc_metadata.oem_manufacturer == "Atlas Copco"
            assert res.meta.document_status == "Approved"
            assert res.meta.approved_by == "lead.approver@corp.com"
            assert len(res.spare_parts) == 1
            assert res.spare_parts[0].status == "Approved"
            assert res.spare_parts[0].reviewed_by == "approver@corp.com"

    def test_fabric_envelope_rehydration_baseline_schema(self):
        """Tests that when Fabric only has baseline schema columns, JSON envelope in error is parsed correctly."""
        import json
        from app.integrations.fabric_cache import load_extract_from_fabric
        from unittest.mock import patch, MagicMock

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        # Baseline schema with no status or reviewed_by column
        mock_cur.description = [("equipment_title",), ("subsystem_component",), ("maintenance_routine",), ("attended_by",), ("remarks",), ("quality_reasons",)]
        mock_cur.fetchall.side_effect = [
            # spares
            [],
            # maint (with review tag in remarks)
            [("Pump", "Motor", "Check alignment", "lead.tech@corp.com", "[Approved] Normal wear", "[STATUS:Approved]")],
            # trouble
            [],
        ]

        envelope_data = {
            "_v": 2,
            "doc_metadata": {
                "title": "Centrifugal Pump Manual",
                "oem_manufacturer": "Grundfos",
                "equipment_model": "CRN 32",
                "equipment_type": "Centrifugal Pump",
            },
            "document_status": "Approved",
            "approved_by": "chief.engineer@corp.com",
            "approved_at": "2026-08-25T11:00:00Z",
            "user_email": "operator@corp.com",
        }

        with patch("app.integrations.fabric_sql.connect", return_value=mock_conn), \
             patch("app.integrations.fabric_cache.get_done_run", return_value={
                 "run_id": "run-456",
                 "error": json.dumps(envelope_data),
                 "overall_score": 90.0,
             }):
            res = load_extract_from_fabric("run-456", filename="pump.pdf")
            assert res.meta.doc_metadata is not None
            assert res.meta.doc_metadata.title == "Centrifugal Pump Manual"
            assert res.meta.doc_metadata.oem_manufacturer == "Grundfos"
            assert res.meta.document_status == "Approved"
            assert res.meta.approved_by == "chief.engineer@corp.com"
            assert len(res.maintenance) == 1
            assert res.maintenance[0].status == "Approved"
            assert res.maintenance[0].reviewed_by == "lead.tech@corp.com"

    def test_fabric_review_sync_function(self):
        import json
        from app.integrations.fabric_cache import update_fabric_review_state
        from unittest.mock import patch, MagicMock

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        existing_run = {
            "run_id": "run-789",
            "filename": "manual.pdf",
            "error": json.dumps({"doc_metadata": {"title": "Generator Manual"}}),
        }

        with patch("app.integrations.fabric_sql.fabric_configured", return_value=True), \
             patch("app.integrations.fabric_sql.connect", return_value=mock_conn), \
             patch("app.integrations.fabric_cache.get_done_run", return_value=existing_run), \
             patch("app.integrations.fabric_sql.insert_audit_event") as mock_audit:
            ok = update_fabric_review_state(
                "run-789",
                document_status="Approved",
                approved_by="approver@corp.com",
                approved_at="2026-08-25T11:30:00Z",
                user_email="approver@corp.com",
            )
            assert ok is True
            mock_cur.execute.assert_called()
            mock_conn.commit.assert_called()
            mock_audit.assert_called()

    def test_fabric_review_sync_api_endpoint(self):
        import asyncio
        from app.models import FabricReviewSyncRequest, DocumentMetadata
        from app.main import api_fabric_extract_review_sync
        from unittest.mock import patch

        req = FabricReviewSyncRequest(
            document_status="Approved",
            approved_by="signoff.approver@corp.com",
            approved_at="2026-08-25T11:30:00Z",
            doc_metadata=DocumentMetadata(
                title="Air Compressor Manual",
                oem_manufacturer="Atlas Copco",
            ),
        )

        async def _run():
            with patch("app.integrations.fabric_sql.fabric_configured", return_value=True), \
                 patch("app.main.get_done_run", return_value={"run_id": "run-999", "filename": "manual.pdf"}), \
                 patch("app.main.update_fabric_review_state", return_value=True):
                return await api_fabric_extract_review_sync("run-999", req, user=None)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            data = loop.run_until_complete(_run())
            assert data["status"] == "ok"
            assert data["run_id"] == "run-999"
        finally:
            loop.close()

    def test_dual_storage_baseline_persistence_and_diff_detection(self):
        """Tests that raw AI extraction is snapshotted into raw_payload immutably, and edited_payload tracks human edits."""
        import json
        from app.integrations.fabric_cache import load_extract_from_fabric, update_fabric_review_state
        from unittest.mock import patch, MagicMock

        # Baseline snapshot stored in error envelope
        raw_payload_data = {
            "spare_parts": [
                {"id": 1, "equipment_title": "Mud Pump", "part_name": "Piston Assembly", "part_number_code": "PA-001", "quantity": "2"}
            ],
            "maintenance": [
                {"id": 1, "equipment_title": "Mud Pump", "checks_instructions": "Inspect valve seat"}
            ],
            "troubleshooting": [],
            "doc_metadata": {"title": "Mud Pump Manual", "oem_manufacturer": "Honghua"},
            "extracted_at": "2026-08-28T10:00:00Z",
        }

        edited_payload_data = {
            "spare_parts": [
                {"id": 1, "equipment_title": "Mud Pump", "part_name": "Piston Assembly (Heavy Duty)", "part_number_code": "PA-001-HD", "quantity": "4"}
            ],
            "maintenance": [
                {"id": 1, "equipment_title": "Mud Pump", "checks_instructions": "Inspect valve seat and check clearance"}
            ],
            "troubleshooting": [],
            "doc_metadata": {"title": "Mud Pump Manual", "oem_manufacturer": "Honghua"},
            "last_modified_by": "editor@corp.com",
            "last_modified_at": "2026-08-28T11:00:00Z",
        }

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        mock_cur.description = [("equipment_title",), ("part_name",), ("part_number_code",), ("quantity",)]
        mock_cur.fetchall.side_effect = [
            # spares working table
            [("Mud Pump", "Piston Assembly (Heavy Duty)", "PA-001-HD", "4")],
            # maint working table
            [("Mud Pump", "Inspect valve seat and check clearance")],
            # trouble working table
            [],
        ]

        envelope_data = {
            "_v": 2,
            "raw_payload": raw_payload_data,
            "edited_payload": edited_payload_data,
            "spare_parts": edited_payload_data["spare_parts"],
            "maintenance": edited_payload_data["maintenance"],
            "troubleshooting": [],
            "doc_metadata": {"title": "Mud Pump Manual", "oem_manufacturer": "Honghua"},
            "document_status": "Pending Review",
        }

        with patch("app.integrations.fabric_sql.connect", return_value=mock_conn), \
             patch("app.integrations.fabric_cache.get_done_run", return_value={
                 "run_id": "run-dual-001",
                 "error": json.dumps(envelope_data),
                 "overall_score": 92.0,
             }):
            res = load_extract_from_fabric("run-dual-001", filename="mud_pump.pdf")
            
            # Verify working state reflects editor's modified payload
            assert len(res.spare_parts) == 1
            assert res.spare_parts[0].part_name == "Piston Assembly (Heavy Duty)"
            assert res.spare_parts[0].part_number_code == "PA-001-HD"
            assert res.spare_parts[0].quantity == "4"

            # Verify immutable baseline extraction snapshot is present and intact
            assert res.baseline is not None
            assert len(res.baseline.spare_parts) == 1
            assert res.baseline.spare_parts[0].part_name == "Piston Assembly"
            assert res.baseline.spare_parts[0].part_number_code == "PA-001"
            assert res.baseline.spare_parts[0].quantity == "2"

            # Verify diff detector flagged has_diff = True
            assert res.meta is not None
            assert res.meta.has_diff is True

    def test_metadata_precedence_and_synchronization(self):
        """Tests that editor-modified doc_metadata takes precedence over baseline and database columns."""
        import json
        from app.integrations.fabric_cache import load_extract_from_fabric
        from unittest.mock import patch, MagicMock

        raw_meta = {
            "title": "STEP 3",
            "oem_manufacturer": "Do-while-e Digital Solutions Pvt Ltd.",
            "equipment_model": "NA",
            "document_version": "NA",
            "publication_date": "2023",
        }
        edited_meta = {
            "title": "Welder or Welding Operator Performance Qualification (WPQ)",
            "oem_manufacturer": "I&D FABRICATION, INC.",
            "equipment_model": '10\'-0" I.D. x 40\'-0" S.S',
            "equipment_type": "Welding Performance Qualification",
            "document_version": "3",
            "publication_date": "5/10/2023",
        }

        envelope_data = {
            "_v": 2,
            "filename": "V-1461.pdf",
            "raw_payload": {
                "spare_parts": [{"id": 1, "equipment_title": "V-1461", "part_name": "Original Part", "part_number_code": "NA"}],
                "maintenance": [],
                "troubleshooting": [],
                "doc_metadata": raw_meta,
            },
            "edited_payload": {
                "spare_parts": [{"id": 1, "equipment_title": "V-1461", "part_name": "hi", "part_number_code": "NA"}],
                "maintenance": [],
                "troubleshooting": [],
                "doc_metadata": edited_meta,
            },
            "spare_parts": [{"id": 1, "equipment_title": "V-1461", "part_name": "hi", "part_number_code": "NA"}],
            "maintenance": [],
            "troubleshooting": [],
            "doc_metadata": edited_meta,
            "document_status": "Pending Sign-Off",
        }

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchall.side_effect = [
            [("V-1461", "hi", "NA")],  # spares
            [],  # maint
            [],  # trouble
        ]

        with patch("app.integrations.fabric_sql.connect", return_value=mock_conn), \
             patch("app.integrations.fabric_cache.get_done_run", return_value={
                 "run_id": "run-wpq-001",
                 "filename": "V-1461.pdf",
                 "doc_title": "STEP 3",  # older column in table
                 "oem_manufacturer": "Do-while-e Digital Solutions Pvt Ltd.",
                 "error": json.dumps(envelope_data),
                 "overall_score": 81.2,
             }):
            res = load_extract_from_fabric("run-wpq-001", filename="V-1461.pdf")
            
            # Working copy metadata must be the edited version
            assert res.meta.doc_metadata is not None
            assert res.meta.doc_metadata.title == "Welder or Welding Operator Performance Qualification (WPQ)"
            assert res.meta.doc_metadata.oem_manufacturer == "I&D FABRICATION, INC."
            assert res.meta.doc_metadata.equipment_model == '10\'-0" I.D. x 40\'-0" S.S'
            assert res.meta.doc_metadata.document_version == "3"
            assert res.meta.doc_metadata.publication_date == "5/10/2023"
            assert res.meta.filename == "V-1461.pdf"

            # Baseline metadata must be the raw AI baseline
            assert res.baseline is not None
            assert res.baseline.doc_metadata is not None
            assert res.baseline.doc_metadata.title == "STEP 3"
            assert res.baseline.doc_metadata.oem_manufacturer == "Do-while-e Digital Solutions Pvt Ltd."

            # has_diff must be True
            assert res.meta.has_diff is True

    def test_local_upload_persistence_and_approver_routing(self):
        """Tests that local PC uploads are assigned run_id, persisted in Fabric, and reach assigned approver on review submission."""
        import json
        import asyncio
        from unittest.mock import patch, MagicMock
        from app.integrations.fabric_cache import extract_with_fabric_cache, update_fabric_review_state, _row_matches_user
        from app.models import ExtractResponse, ExtractMeta, ExtractOptions, DocumentMetadata, SparePartRow

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        fake_res = ExtractResponse(
            maintenance=[],
            spare_parts=[SparePartRow(id=1, equipment_title="Compressor", part_name="Gasket", part_number_code="G-100")],
            troubleshooting=[],
            pages=[],
            meta=ExtractMeta(
                filename="local_compressor.pdf",
                engine="gemini",
                parse_strategy="ocr",
                doc_metadata=DocumentMetadata(title="Compressor Manual", oem_manufacturer="Atlas Copco"),
                overall_score=88.5,
            ),
        )

        async def fake_extract(*args, **kwargs):
            return fake_res

        options = ExtractOptions(engine="gemini", parse_strategy="ocr")

        with patch("app.integrations.fabric_sql.fabric_configured", return_value=True), \
             patch("app.integrations.fabric_cache.find_done_run", return_value=None), \
             patch("app.integrations.fabric_sql.connect", return_value=mock_conn), \
             patch("app.integrations.fabric_sql.insert_log") as mock_insert_log, \
             patch("app.integrations.fabric_sql.insert_many"), \
             patch("app.integrations.fabric_sql._get_table_columns", return_value={"run_id", "user_id", "user_email", "error"}):
            
            res = asyncio.run(
                extract_with_fabric_cache(
                    b"%PDF-1.4 test",
                    "local_compressor.pdf",
                    options,
                    extract_fn=fake_extract,
                    drive_item_id="LOCAL_UPLOAD",
                    user_id="user-editor-01",
                    user_email="editor@company.com",
                    user_role="editor",
                )
            )

            # 1. Verify run_id is generated and attached to response meta
            assert res.meta.run_id is not None
            assert len(res.meta.run_id) > 10

            # 2. Verify row matches editor for "My Extracts"
            inserted_log = mock_insert_log.call_args[0][1]
            assert inserted_log["user_email"] == "editor@company.com"
            assert _row_matches_user(inserted_log, user_id="user-editor-01", user_email="editor@company.com") is True

            # 3. Verify that submitting review updates envelope and targets assigned approver
            with patch("app.integrations.fabric_cache.get_done_run", return_value=inserted_log), \
                 patch("app.auth.store.find_by_email", return_value={"email": "editor@company.com", "assigned_approver": "lead.approver@company.com"}):
                success = update_fabric_review_state(
                    run_id=res.meta.run_id,
                    document_status="Pending Sign-Off",
                    user_id="user-editor-01",
                    user_email="editor@company.com",
                    user_role="editor",
                    spare_parts=res.spare_parts,
                    doc_metadata={"title": "Compressor Manual", "oem_manufacturer": "Atlas Copco"},
                )
                assert success is True

    def test_deduplication_cache_hit(self):
        """Tests that uploading the same document triggers instant deduplication from Fabric cache without calling extract_fn."""
        import json
        import asyncio
        from unittest.mock import patch, MagicMock
        from app.integrations.fabric_cache import extract_with_fabric_cache, find_done_run
        from app.models import ExtractResponse, ExtractMeta, ExtractOptions, DocumentMetadata, SparePartRow

        cached_envelope = {
            "_v": 2,
            "filename": "V-1461.pdf",
            "content_hash": "a1b2c3d4e5f6",
            "spare_parts": [{"id": 1, "equipment_title": "V-1461", "part_name": "Cached Valve", "part_number_code": "CV-01"}],
            "maintenance": [],
            "troubleshooting": [],
            "doc_metadata": {"title": "V-1461 Manual", "oem_manufacturer": "Atlas"},
            "document_status": "Pending Review",
        }

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.description = [("run_id",), ("filename",), ("overall_score",), ("maintenance_count",),
                                ("spare_parts_count",), ("troubleshooting_count",), ("engine",), ("parse_strategy",),
                                ("document_status",), ("user_id",), ("user_email",), ("error",)]
        mock_cur.fetchone.return_value = (
            "run-v1461-cached", "V-1461.pdf", 90.0, 0, 1, 0, "gemini", "ocr",
            "Pending Review", "user-01", "editor@company.com", json.dumps(cached_envelope)
        )

        mock_extract_fn = MagicMock()

        options = ExtractOptions(engine="gemini", parse_strategy="ocr")

        with patch("app.integrations.fabric_sql.fabric_configured", return_value=True), \
             patch("app.integrations.fabric_sql.connect", return_value=mock_conn), \
             patch("app.integrations.fabric_cache.get_done_run", return_value={
                 "run_id": "run-v1461-cached",
                 "filename": "V-1461.pdf",
                 "error": json.dumps(cached_envelope),
             }):
            
            res = asyncio.run(
                extract_with_fabric_cache(
                    b"%PDF-1.4 sample PDF bytes",
                    "V-1461.pdf",
                    options,
                    extract_fn=mock_extract_fn,
                    user_id="user-01",
                    user_email="editor@company.com",
                    user_role="editor",
                )
            )

            # Verify extraction function was NEVER called (cache hit / deduplicated!)
            assert mock_extract_fn.called is False
            assert res.meta.engine == "fabric-cache"
            assert res.meta.run_id is not None
            assert len(res.spare_parts) == 1
            assert res.spare_parts[0].part_name == "Cached Valve"

    def test_list_done_extracts_envelope_parsing(self):
        import json
        from pathlib import Path
        from app.integrations.fabric_cache import list_done_extracts
        from unittest.mock import patch, MagicMock

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        mock_cur.description = [("run_id",), ("filename",), ("status",), ("error",)]
        envelope = {
            "document_status": "Approved",
            "approved_by": "lead.engineer@corp.com",
            "doc_metadata": {"title": "Main Boiler Manual", "oem_manufacturer": "Cleaver-Brooks"},
        }
        mock_cur.fetchall.return_value = [
            ("run-101", "boiler.pdf", "done", json.dumps(envelope)),
        ]

        with patch("app.integrations.fabric_sql.fabric_configured", return_value=True), \
             patch("app.integrations.fabric_sql.connect", return_value=mock_conn), \
             patch("app.integrations.fabric_cache._MEM_CACHE", {}), \
             patch("app.integrations.fabric_cache.CACHE_DIR", Path("/nonexistent/temp")):
            rows = list_done_extracts(limit=10)
            assert len(rows) == 1
            assert rows[0]["document_status"] == "Approved"
            assert rows[0]["approved_by"] == "lead.engineer@corp.com"
            assert rows[0]["doc_title"] == "Main Boiler Manual"
            assert rows[0]["oem_manufacturer"] == "Cleaver-Brooks"

    def test_extract_audit_review_state_sync(self, tmp_path=None):
        from app.extract_audit import update_extract_audit_review_state, LOCAL_INDEX
        from unittest.mock import patch
        import json
        import tempfile
        from pathlib import Path

        if tmp_path is None:
            tmp_dir = tempfile.TemporaryDirectory()
            tmp_path = Path(tmp_dir.name)

        test_log_file = tmp_path / "test_audit.jsonl"
        sample_rec = {
            "id": "audit-rec-1",
            "run_id": "fabric-run-888",
            "filename": "turbine.pdf",
            "status": "pass",
            "document_status": "Pending Review",
        }
        test_log_file.write_text(json.dumps(sample_rec) + "\n", encoding="utf-8")

        with patch("app.extract_audit.LOCAL_INDEX", test_log_file):
            ok = update_extract_audit_review_state(
                "fabric-run-888",
                document_status="Approved",
                approved_by="tech.lead@corp.com",
                approved_at="2026-08-25T16:00:00Z",
            )
            assert ok is True
            updated_lines = test_log_file.read_text(encoding="utf-8").splitlines()
            rec = json.loads(updated_lines[0])
            assert rec["document_status"] == "Approved"
            assert rec["approved_by"] == "tech.lead@corp.com"

    def test_share_token_generation_and_validation(self):
        from app.security import create_share_token, decode_share_token
        secret = "test-secret-key-12345-enterprise-secure-jwt-key"
        token = create_share_token(run_id="run-share-xyz", secret=secret, expire_hours=24)
        assert isinstance(token, str)

        payload = decode_share_token(token, secret)
        assert payload["type"] == "extract_share"
        assert payload["run_id"] == "run-share-xyz"
        assert "exp" in payload

    def test_share_token_expired_rejection(self):
        from app.security import create_share_token, decode_share_token
        import jwt

        secret = "test-secret-key-12345-enterprise-secure-jwt-key"
        # Create token that expired 1 hour ago
        token = create_share_token(run_id="run-expired", secret=secret, expire_hours=-1)
        with pytest.raises(jwt.ExpiredSignatureError):
            decode_share_token(token, secret)

    def test_public_share_api_endpoints(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from unittest.mock import patch
        from app.models import ExtractResponse, ExtractMeta

        client = TestClient(app)

        # 1. Test POST share link creation
        with patch("app.integrations.fabric_sql.fabric_configured", return_value=True), \
             patch("app.main.get_done_run", return_value={"run_id": "run-share-001", "filename": "pump.pdf"}):
            resp = client.post("/api/fabric/extracts/run-share-001/share")
            assert resp.status_code == 200
            data = resp.json()
            assert data["run_id"] == "run-share-001"
            assert "share_token" in data
            assert data["expires_in_hours"] == 24
            share_token = data["share_token"]

        # 2. Test GET public share retrieval with the token
        mock_extract = ExtractResponse(
            maintenance=[],
            spare_parts=[],
            troubleshooting=[],
            meta=ExtractMeta(
                engine="gemini",
                parse_strategy="ocr",
                pages_total=5,
                pages_processed=5,
                maintenance_count=0,
                spare_parts_count=0,
                troubleshooting_count=0,
                filename="pump.pdf",
            )
        )

        with patch("app.integrations.fabric_sql.fabric_configured", return_value=True), \
             patch("app.main.get_done_run", return_value={"run_id": "run-share-001", "filename": "pump.pdf", "overall_score": 90.0}), \
             patch("app.main.load_extract_from_fabric", return_value=mock_extract):
            get_resp = client.get(f"/api/share/{share_token}")
            assert get_resp.status_code == 200
            share_data = get_resp.json()
            assert share_data["run_id"] == "run-share-001"
            assert share_data["filename"] == "pump.pdf"
            assert share_data["is_shared_view"] is True
            assert "expires_at" in share_data

    def test_sso_url_generation_strips_quotes(self):
        from app.auth.routes import _clean_env, _get_sso_url
        import os

        # Test that _clean_env strips whitespace and quotes
        with patch.dict(os.environ, {
            "AZURE_TENANT_ID": '"196e20ca-f848-4dbc-b812-0125cda86494"',
            "AZURE_CLIENT_ID": "'8d75ea52-c177-4dba-8bd0-19808e8f2220'",
            "SSO_REDIRECT_URI": ' "http://localhost:8001/api/auth/sso/callback" '
        }):
            assert _clean_env("AZURE_TENANT_ID") == "196e20ca-f848-4dbc-b812-0125cda86494"
            assert _clean_env("AZURE_CLIENT_ID") == "8d75ea52-c177-4dba-8bd0-19808e8f2220"
            assert _clean_env("SSO_REDIRECT_URI") == "http://localhost:8001/api/auth/sso/callback"

            sso_url, state = _get_sso_url()
            assert "https://login.microsoftonline.com/196e20ca-f848-4dbc-b812-0125cda86494/oauth2/v2.0/authorize" in sso_url
            assert "client_id=8d75ea52-c177-4dba-8bd0-19808e8f2220" in sso_url
            assert '"' not in sso_url
            assert "'" not in sso_url

    def test_sso_login_endpoint(self):
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        with patch("app.auth.routes._sso_configured", return_value=True), \
             patch.dict("os.environ", {
                 "AZURE_TENANT_ID": "196e20ca-f848-4dbc-b812-0125cda86494",
                 "AZURE_CLIENT_ID": "8d75ea52-c177-4dba-8bd0-19808e8f2220"
             }):
            resp = client.get("/api/auth/sso/login")
            assert resp.status_code == 200
            data = resp.json()
            assert "auth_url" in data
            assert "https://login.microsoftonline.com/196e20ca-f848-4dbc-b812-0125cda86494/oauth2/v2.0/authorize" in data["auth_url"]

    def test_sso_callback_does_not_use_microsoft_referer(self):
        from app.auth.routes import _resolve_base_url
        from starlette.requests import Request
        import os

        # Simulated callback request from Microsoft redirect
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/auth/sso/callback",
            "headers": [
                (b"host", b"omniparse-idp-alb-1471104279.eu-west-1.elb.amazonaws.com"),
                (b"referer", b"https://login.microsoftonline.com/196e20ca-f848-4dbc-b812-0125cda86494/oauth2/v2.0/authorize"),
            ],
        }
        req = Request(scope)

        with patch.dict(os.environ, {
            "CORS_ORIGINS": "http://localhost:8000,https://d11bl7hg497hj.cloudfront.net"
        }):
            resolved = _resolve_base_url(request=req)
            assert resolved == "https://d11bl7hg497hj.cloudfront.net"
            assert "microsoftonline.com" not in resolved
            assert "elb.amazonaws.com" not in resolved

    def test_sso_callback_successful_redirect(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.auth import store
        import os
        from unittest.mock import AsyncMock, MagicMock

        client = TestClient(app)
        
        with patch("app.integrations.fabric_sql.fabric_configured", return_value=False):
            # Pre-seed user in store as authorized
            store.create_user(
                email="engineer@company.com",
                password="SecurePassword123!",
                display_name="Lead Engineer",
                role="approver"
            )

        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = {"access_token": "mock-azure-access-token"}

        mock_me_resp = MagicMock()
        mock_me_resp.status_code = 200
        mock_me_resp.json.return_value = {
            "mail": "engineer@company.com",
            "displayName": "Lead Engineer",
            "userPrincipalName": "engineer@company.com"
        }

        with patch("app.auth.routes._sso_configured", return_value=True), \
             patch("app.integrations.fabric_sql.fabric_configured", return_value=False), \
             patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_token_resp), \
             patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_me_resp), \
             patch.dict(os.environ, {
                 "AZURE_TENANT_ID": "196e20ca-f848-4dbc-b812-0125cda86494",
                 "AZURE_CLIENT_ID": "8d75ea52-c177-4dba-8bd0-19808e8f2220",
                 "AZURE_CLIENT_SECRET": "testsecret",
                 "CORS_ORIGINS": "http://localhost:8000,https://d11bl7hg497hj.cloudfront.net"
             }):
            resp = client.get("/api/auth/sso/callback?code=mock-auth-code", follow_redirects=False)
            assert resp.status_code == 307
            loc = resp.headers["location"]
            assert loc.startswith("https://d11bl7hg497hj.cloudfront.net/index.html#sso_token=")
            assert "engineer%40company.com" in loc

    def test_sso_callback_unauthorized_user_access_denied(self):
        from fastapi.testclient import TestClient
        from app.main import app
        import os
        from unittest.mock import AsyncMock, MagicMock

        client = TestClient(app)
        
        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = {"access_token": "mock-azure-access-token"}

        mock_me_resp = MagicMock()
        mock_me_resp.status_code = 200
        mock_me_resp.json.return_value = {
            "mail": "unauthorized.stranger@company.com",
            "displayName": "Unauthorized Stranger",
            "userPrincipalName": "unauthorized.stranger@company.com"
        }

        with patch("app.auth.routes._sso_configured", return_value=True), \
             patch("app.integrations.fabric_sql.fabric_configured", return_value=False), \
             patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_token_resp), \
             patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_me_resp), \
             patch.dict(os.environ, {
                 "AZURE_TENANT_ID": "196e20ca-f848-4dbc-b812-0125cda86494",
                 "AZURE_CLIENT_ID": "8d75ea52-c177-4dba-8bd0-19808e8f2220",
                 "AZURE_CLIENT_SECRET": "testsecret",
                 "CORS_ORIGINS": "http://localhost:8000,https://d11bl7hg497hj.cloudfront.net"
             }):
            resp = client.get("/api/auth/sso/callback?code=mock-auth-code", follow_redirects=False)
            assert resp.status_code == 307
            loc = resp.headers["location"]
            assert "auth_error=access_denied" in loc
            assert "unauthorized.stranger%40company.com" in loc

    def test_fabric_review_sync_role_guards(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.auth.store import create_user
        from app.security import create_access_token
        from app.config import get_jwt_secret

        client = TestClient(app)
        
        with patch("app.integrations.fabric_sql.fabric_configured", return_value=False):
            # Create an Editor user
            editor_u = create_user(
                email="editor.technician@company.com",
                password="SecurePassword123!",
                display_name="Editor Tech",
                role="editor"
            )
        
        editor_token = create_access_token(
            user_id=editor_u.id,
            email=editor_u.email,
            role="editor",
            secret=get_jwt_secret(),
        )

        with patch("app.main.get_done_run", return_value={
                 "run_id": "run-test-123",
                 "filename": "manual.pdf",
                 "user_id": editor_u.id,
                 "user_email": editor_u.email,
                 "submitted_by": editor_u.email,
             }), \
             patch("app.main.update_fabric_review_state", return_value=True), \
             patch("app.main.update_extract_audit_review_state", return_value=True):
            
            # 1. Editor CAN submit review state as 'Pending Sign-Off' or 'In Review'
            res_submit = client.post(
                "/api/fabric/extracts/run-test-123/review-sync",
                json={"document_status": "Pending Sign-Off"},
                headers={"Authorization": f"Bearer {editor_token}"}
            )
            assert res_submit.status_code == 200

            # 2. Editor CANNOT perform final 'Approved' sign-off
            res_approve = client.post(
                "/api/fabric/extracts/run-test-123/review-sync",
                json={"document_status": "Approved", "approved_by": editor_u.email},
                headers={"Authorization": f"Bearer {editor_token}"}
            )
            assert res_approve.status_code == 403
            assert "cannot perform final sign-off" in res_approve.json()["detail"]

    def test_sso_login_with_select_account_prompt(self):
        from fastapi.testclient import TestClient
        from app.main import app
        import os
        from unittest.mock import patch

        client = TestClient(app)
        with patch("app.auth.routes._sso_configured", return_value=True), \
             patch.dict(os.environ, {
                 "AZURE_TENANT_ID": "196e20ca-f848-4dbc-b812-0125cda86494",
                 "AZURE_CLIENT_ID": "8d75ea52-c177-4dba-8bd0-19808e8f2220",
                 "CORS_ORIGINS": "http://localhost:8000,https://d11bl7hg497hj.cloudfront.net"
             }):
            resp = client.get("/api/auth/sso/login?prompt=select_account")
            assert resp.status_code == 200
            data = resp.json()
            assert "auth_url" in data
            assert "prompt=select_account" in data["auth_url"]

    def test_assign_approver_to_editor(self):
        from app.auth import store
        from unittest.mock import patch

        with patch("app.integrations.fabric_sql.fabric_configured", return_value=False):
            # Create approver
            approver = store.create_user(
                email="lead.approver@company.com",
                display_name="Lead Approver",
                role="approver",
            )
            # Create editor assigned to approver
            editor = store.create_user(
                email="junior.editor@company.com",
                display_name="Junior Editor",
                role="editor",
                assigned_approver=approver.email,
            )
            assert editor.assigned_approver == "lead.approver@company.com"

            # Update assigned approver
            updated = store.update_user(editor.id, assigned_approver="another.approver@company.com")
            assert updated.assigned_approver == "another.approver@company.com"

    def test_create_user_without_temp_password(self):
        from app.auth import store
        from unittest.mock import patch

        with patch("app.integrations.fabric_sql.fabric_configured", return_value=False):
            u = store.create_user(
                email="sso.only.user@company.com",
                password=None, # password omitted for SSO user
                display_name="SSO User",
                role="editor",
            )
            assert u.email == "sso.only.user@company.com"
            raw = store.find_by_id(u.id)
            assert raw is not None
            assert raw.get("password_hash")

    def test_pending_approvals_api_endpoint(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from unittest.mock import patch

        client = TestClient(app)
        mock_done_extracts = [
            {
                "run_id": "run-pending-1",
                "filename": "SOP_1.pdf",
                "document_status": "Pending Sign-Off",
                "maintenance_count": 5,
                "spare_parts_count": 2,
                "troubleshooting_count": 1,
                "overall_score": 95.0,
            },
            {
                "run_id": "run-approved-1",
                "filename": "SOP_2.pdf",
                "document_status": "Approved",
                "maintenance_count": 3,
                "overall_score": 99.0,
            },
            {
                "run_id": "run-pending-2",
                "filename": "SOP_3.pdf",
                "document_status": "Pending Review",
                "maintenance_count": 4,
                "overall_score": 88.0,
            }
        ]

        with patch("app.integrations.fabric_sql.fabric_configured", return_value=True), \
             patch("app.main.list_done_extracts", return_value=mock_done_extracts):
            res = client.get("/api/fabric/pending-approvals")
            assert res.status_code == 200
            data = res.json()
            assert data["count"] == 2
            run_ids = [item["run_id"] for item in data["items"]]
            assert "run-pending-1" in run_ids
            assert "run-pending-2" in run_ids
            assert "run-approved-1" not in run_ids

    def test_approved_document_syncs_row_statuses(self):
        from app.integrations import fabric_cache
        from unittest.mock import patch, MagicMock

        mock_conn = MagicMock()
        mock_log = {
            "run_id": "run-approved-sync",
            "filename": "Pump_Manual.pdf",
            "document_status": "Approved",
            "approved_by": "approver@company.com",
            "approved_at": "2026-08-27T12:00:00Z",
            "overall_score": 98.0,
        }
        mock_maint_rows = [
            {"equipment_title": "Pump", "subsystem_component": "Motor", "maintenance_routine": "Daily", "status": "Pending Review"}
        ]

        with patch("app.integrations.fabric_sql.connect", return_value=mock_conn), \
             patch("app.integrations.fabric_cache.get_done_run", return_value=mock_log), \
             patch("app.integrations.fabric_cache._fetch_table", side_effect=lambda c, t, r: mock_maint_rows if t == "Tbl_PM_Maintenance" else []):
            res = fabric_cache.load_extract_from_fabric("run-approved-sync", filename="Pump_Manual.pdf")
            assert res.meta.document_status == "Approved"
            assert res.meta.approved_by == "approver@company.com"
            assert len(res.maintenance) == 1
            assert res.maintenance[0].status == "Approved"
            assert res.maintenance[0].reviewed_by == "approver@company.com"

    def test_editor_modifications_sync_to_approver(self):
        """Verify that when an editor modifies records, submits for review, the modified records are persisted and loaded for the approver."""
        from app.integrations import fabric_cache
        from app.models import SparePartRow
        from unittest.mock import patch, MagicMock
        import json

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        staged_envelope = {}
        def mock_execute(sql, params=None):
            nonlocal staged_envelope
            if "UPDATE Tbl_PM_Extraction_logs" in sql and params:
                staged_envelope = json.loads(params[0])

        mock_cursor.execute.side_effect = mock_execute

        mock_log = {
            "run_id": "run-editor-sync-01",
            "filename": "1.JC70DB DW User Manual (1).pdf",
            "document_status": "Pending Review",
            "overall_score": 59.0,
            "error": "{}",
        }

        # Editor edited record #3 part name and added a custom field
        edited_spares = [
            SparePartRow(
                id=1,
                equipment_title="1.JC70DB DW User Manual (1)",
                subsystem_location="JC70DB DW",
                item_no="2",
                part_name="Drum",
                part_number_code="NA",
                drawing_model_no="Fig. 2-1",
                quantity="NA",
                status="Pending Review",
            ),
            SparePartRow(
                id=2,
                equipment_title="1",
                subsystem_location="NA",
                item_no="8",
                part_name="flrflvhfvhvfvfvfvf c covrng",
                part_number_code="10310",
                drawing_model_no="NA",
                quantity="2487",
                status="Pending Review",
            ),
        ]

        with patch("app.integrations.fabric_sql.fabric_configured", return_value=True), \
             patch("app.integrations.fabric_sql.connect", return_value=mock_conn), \
             patch("app.integrations.fabric_cache.get_done_run", return_value=mock_log), \
             patch("app.integrations.fabric_sql.insert_many", return_value=None), \
             patch("app.integrations.fabric_sql.insert_audit_event", return_value=None):

            # Editor submits review state with modified spare parts
            ok = fabric_cache.update_fabric_review_state(
                "run-editor-sync-01",
                document_status="Pending Sign-Off",
                approved_by="deepak.sharma@corp.com",
                user_email="deepak.sharma@corp.com",
                spare_parts=edited_spares,
            )
            assert ok is True
            assert "spare_parts" in staged_envelope
            assert len(staged_envelope["spare_parts"]) == 2
            assert staged_envelope["spare_parts"][1]["part_name"] == "flrflvhfvhvfvfvfvf c covrng"

            # Now Approver loads the extract from Fabric
            mock_log["error"] = json.dumps(staged_envelope)
            mock_log["document_status"] = "Pending Sign-Off"

            loaded_res = fabric_cache.load_extract_from_fabric("run-editor-sync-01", filename="1.JC70DB DW User Manual (1).pdf")
            assert len(loaded_res.spare_parts) == 2
            assert loaded_res.spare_parts[0].part_name == "Drum"
            assert loaded_res.spare_parts[1].part_name == "flrflvhfvhvfvfvfvf c covrng"
            assert loaded_res.spare_parts[1].quantity == "2487"
            assert loaded_res.meta.document_status == "Pending Sign-Off"

    def test_fabric_default_database_name(self):
        from app.integrations import fabric_sql
        from unittest.mock import patch

        with patch.dict("os.environ", {
            "SQL_SERVER": "fabric.datawarehouse.net",
            "AZURE_CLIENT_ID": "client-id",
            "AZURE_CLIENT_SECRET": "client-secret",
        }, clear=True):
            # When SQL_DATABASE is not explicitly passed, it defaults to WH_IDP
            assert fabric_sql.fabric_configured() is True

    def test_sync_users_from_fabric_on_startup(self):
        from app.auth import store
        from unittest.mock import patch, MagicMock

        mock_fabric_users = [
            {
                "id": "u-1",
                "user_id": "u-1",
                "email": "persisted.editor@company.com",
                "display_name": "Persisted Editor",
                "role": "editor",
                "status": "active",
                "copilot_daily_limit": 10,
                "preferred_model": "gemini-3.6-flash",
                "allowed_models": ["gemini-3.6-flash"],
                "password_hash": "",
                "assigned_approver": "lead.approver@company.com",
                "created_at": "2026-08-27T00:00:00Z",
                "updated_at": "2026-08-27T00:00:00Z",
            }
        ]

        mock_conn = MagicMock()
        with patch("app.integrations.fabric_sql.fabric_configured", return_value=True), \
             patch("app.integrations.fabric_sql.connect", return_value=mock_conn), \
             patch("app.integrations.fabric_sql.ensure_users_table"), \
             patch("app.integrations.fabric_sql.list_users_from_fabric", return_value=mock_fabric_users), \
             patch("app.integrations.fabric_sql.upsert_user_in_fabric"):
            
            # Simulate a freshly booted container with clean local store
            store.sync_users_from_fabric()
            user = store.find_by_email("persisted.editor@company.com")
            assert user is not None
            assert user["email"] == "persisted.editor@company.com"
            assert user["assigned_approver"] == "lead.approver@company.com"
            assert user["role"] == "editor"

    def test_parse_graph_url(self):
        from app.integrations.graph_sharepoint import parse_graph_url

        # User's exact Graph API URL
        user_url = "https://graph.microsoft.com/v1.0/drives/b!59_8-O77vU67uHpDjoDYsHJLQyAGBJpOq3_j3vQsdevGOXQ0QB2oR45h6scj8nrl/items/01HEZEZBVTOZJBMUCVCNFZMJ6I7XSSMTSB/children"
        drive_id, folder_id = parse_graph_url(user_url)
        assert drive_id == "b!59_8-O77vU67uHpDjoDYsHJLQyAGBJpOq3_j3vQsdevGOXQ0QB2oR45h6scj8nrl"
        assert folder_id == "01HEZEZBVTOZJBMUCVCNFZMJ6I7XSSMTSB"

        # Root URL
        root_url = "https://graph.microsoft.com/v1.0/drives/b!my-drive-123/root/children"
        d_id, f_id = parse_graph_url(root_url)
        assert d_id == "b!my-drive-123"
        assert f_id is None

        # Raw Drive ID
        raw_id = "b!direct-drive-id-only"
        d_id2, f_id2 = parse_graph_url(raw_id)
        assert d_id2 == "b!direct-drive-id-only"
        assert f_id2 is None

    def test_sharepoint_config_get_and_save(self, tmp_path=None):
        from app.integrations import graph_sharepoint
        from unittest.mock import patch
        import tempfile
        from pathlib import Path

        if tmp_path is None:
            tmp_dir = tempfile.TemporaryDirectory()
            tmp_path = Path(tmp_dir.name)

        with patch("app.integrations.graph_sharepoint.CONFIG_FILE", tmp_path / "sp_config.json"), \
             patch("app.integrations.fabric_sql.fabric_configured", return_value=False):
            
            cfg = graph_sharepoint.get_sharepoint_config()
            assert "drive_id" in cfg
            assert cfg.get("auto_sync_local_uploads") is True

            updated = graph_sharepoint.save_sharepoint_config({
                "graph_endpoint": "https://graph.microsoft.com/v1.0/drives/b!test-drive/items/01test-item/children",
                "folder_name": "Testing Site",
                "auto_sync_local_uploads": True,
            })
            assert updated["drive_id"] == "b!test-drive"
            assert updated["folder_item_id"] == "01test-item"
            assert updated["folder_name"] == "Testing Site"

    def test_admin_sharepoint_api_endpoints(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.auth.store import create_user
        from app.security import create_access_token
        from app.config import get_jwt_secret
        from unittest.mock import patch

        client = TestClient(app)

        # Admin user
        admin = create_user(
            email="admin.sharepoint@company.com",
            role="admin",
            password="adminpass",
        )
        token = create_access_token(user_id=admin.id, email=admin.email, role=admin.role, secret=get_jwt_secret())
        headers = {"Authorization": f"Bearer {token}"}

        # Non-admin user
        editor = create_user(
            email="editor.sharepoint@company.com",
            role="editor",
            password="editorpass",
        )
        editor_token = create_access_token(user_id=editor.id, email=editor.email, role=editor.role, secret=get_jwt_secret())
        editor_headers = {"Authorization": f"Bearer {editor_token}"}

        # 1. Non-admin is blocked
        resp = client.get("/api/admin/sharepoint/config", headers=editor_headers)
        assert resp.status_code == 403

        # 2. Admin can get config
        with patch("app.integrations.graph_sharepoint.get_sharepoint_config", return_value={
            "drive_id": "b!test-drive",
            "folder_item_id": "01folder",
            "folder_name": "Testing Site",
            "auto_sync_local_uploads": True,
        }):
            resp = client.get("/api/admin/sharepoint/config", headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["config"]["drive_id"] == "b!test-drive"

        # 3. Admin can save config
        with patch("app.integrations.graph_sharepoint.save_sharepoint_config", return_value={
            "drive_id": "b!59_8-O77vU67uHpDjoDYsHJLQyAGBJpOq3_j3vQsdevGOXQ0QB2oR45h6scj8nrl",
            "folder_item_id": "01HEZEZBVTOZJBMUCVCNFZMJ6I7XSSMTSB",
            "folder_name": "New Testing Site",
            "auto_sync_local_uploads": True,
        }):
            resp = client.post(
                "/api/admin/sharepoint/config",
                json={
                    "graph_endpoint": "https://graph.microsoft.com/v1.0/drives/b!59_8-O77vU67uHpDjoDYsHJLQyAGBJpOq3_j3vQsdevGOXQ0QB2oR45h6scj8nrl/items/01HEZEZBVTOZJBMUCVCNFZMJ6I7XSSMTSB/children",
                    "folder_name": "BOGEL_PM Plan_Spares BOM IDP Project",
                    "auto_sync_local_uploads": True,
                },
                headers=headers,
            )
            assert resp.status_code == 200

    def test_resolve_project_and_local_uploads_hierarchy(self):
        """Verifies resolution and creation of BOGEL_PM Plan_Spares BOM IDP Project and Local Uploads folders."""
        from app.integrations.graph_sharepoint import resolve_project_and_upload_folders, ensure_local_uploads_folder
        from unittest.mock import patch, MagicMock

        mock_children_parent = {
            "value": [
                {
                    "id": "proj-folder-item-001",
                    "name": "BOGEL_PM Plan_Spares BOM IDP Project",
                    "folder": {},
                },
                {
                    "id": "direct-upload-item-002",
                    "name": "Local Uploads",
                    "folder": {},
                }
            ]
        }
        mock_children_proj = {
            "value": [
                {
                    "id": "local-uploads-sub-item-003",
                    "name": "Local Uploads",
                    "folder": {},
                }
            ]
        }

        with patch("app.integrations.graph_sharepoint.sharepoint_configured", return_value=True), \
             patch("app.integrations.graph_sharepoint._require_configured", return_value=("tenant", "cid", "sec", "b!drive", "01parent")), \
             patch("app.integrations.graph_sharepoint.get_graph_token", return_value="mock-token"), \
             patch("app.integrations.graph_sharepoint._graph_json", side_effect=[mock_children_parent, mock_children_proj]):
            
            proj_id, upload_id = resolve_project_and_upload_folders("b!drive", "01parent")
            assert proj_id == "proj-folder-item-001"
            assert upload_id == "local-uploads-sub-item-003"

            # ensure_local_uploads_folder should return the resolved local uploads folder ID
            ensured_id = ensure_local_uploads_folder("b!drive", "01parent")
            assert ensured_id == "local-uploads-sub-item-003"

    def test_browse_sharepoint_directory_and_api_files(self):
        """Verifies directory browsing and folder switching API support."""
        from app.integrations.graph_sharepoint import browse_sharepoint_directory
        from fastapi.testclient import TestClient
        from app.main import app
        from unittest.mock import patch

        mock_folder_meta = {
            "id": "01HEZEZBRCZEBHLLDAJNB3PIBYPOIUF4DD",
            "name": "BLDG 2",
            "parentReference": {"id": "01parent-folder-id"},
        }
        mock_folder_children = {
            "value": [
                {
                    "id": "subfolder-01",
                    "name": "Substation",
                    "folder": {"childCount": 4},
                },
                {
                    "id": "file-01",
                    "name": "1.JC70DB DW User Manual (1).pdf",
                    "size": 7864320,
                    "file": {},
                },
                {
                    "id": "file-02",
                    "name": "2.10 User Manual of DCR.pdf",
                    "size": 1153433,
                    "file": {},
                }
            ]
        }

        with patch("app.integrations.graph_sharepoint.sharepoint_configured", return_value=True), \
             patch("app.integrations.graph_sharepoint._require_configured", return_value=("tenant", "cid", "sec", "b!drive", "01parent")), \
             patch("app.integrations.graph_sharepoint.get_graph_token", return_value="mock-token"), \
             patch("app.integrations.graph_sharepoint._graph_json", side_effect=[mock_folder_meta, mock_folder_children]):
            
            files, folders, curr_info, parent_id = browse_sharepoint_directory(
                folder_item_id="01HEZEZBRCZEBHLLDAJNB3PIBYPOIUF4DD",
                drive_id="b!drive",
            )
            assert len(files) == 2
            assert files[0].name == "1.JC70DB DW User Manual (1).pdf"
            assert len(folders) == 1
            assert folders[0]["name"] == "Substation"
            assert curr_info["name"] == "BLDG 2"
            assert parent_id == "01parent-folder-id"

        # Test API endpoint
        client = TestClient(app)
        with patch("app.integrations.graph_sharepoint.sharepoint_configured", return_value=True), \
             patch("app.integrations.graph_sharepoint.browse_sharepoint_directory", return_value=(files, folders, curr_info, parent_id)):
            
            resp = client.get("/api/integrations/sharepoint/files?folder_id=01HEZEZBRCZEBHLLDAJNB3PIBYPOIUF4DD")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["files"]) == 2
            assert len(data["folders"]) == 1
            assert data["current_folder"]["name"] == "BLDG 2"
            assert data["parent_folder_id"] == "01parent-folder-id"

    def test_user_sharepoint_folder_mapping(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.auth.store import create_user, update_user, find_by_id
        from app.security import create_access_token
        from app.config import get_jwt_secret
        from unittest.mock import patch

        client = TestClient(app)

        admin = create_user(
            email="admin.folder@company.com",
            role="admin",
            password="adminpass123",
        )
        token = create_access_token(user_id=admin.id, email=admin.email, role=admin.role, secret=get_jwt_secret())
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Create user with sharepoint folder via API
        with patch("app.integrations.fabric_sql.fabric_configured", return_value=False):
            resp = client.post(
                "/api/admin/users",
                headers=headers,
                json={
                    "email": "engineer1@company.com",
                    "display_name": "Test Engineer",
                    "role": "editor",
                    "sharepoint_folder": "/Manuals/Turbines",
                    "allowed_models": ["gemini-3.6-flash"],
                }
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["email"] == "engineer1@company.com"
            assert data["sharepoint_folder"] == "/Manuals/Turbines"

            # 2. Update user sharepoint folder via PATCH API
            user_id = data["id"]
            patch_resp = client.patch(
                f"/api/admin/users/{user_id}",
                headers=headers,
                json={
                    "sharepoint_folder": "01HEZEZBVTOZJBMUCVCNFZMJ6I7XSSMTSB",
                }
            )
            assert patch_resp.status_code == 200
            updated_data = patch_resp.json()
            assert updated_data["sharepoint_folder"] == "01HEZEZBVTOZJBMUCVCNFZMJ6I7XSSMTSB"

    def test_editor_retains_visibility_after_approver_signoff(self):
        """Ensures when an approver signs off a document, it remains visible in the original editor's My Extracts."""
        import json
        from app.integrations.fabric_cache import update_fabric_review_state, list_done_extracts, _store_in_cache

        run_id = "run-editor-preserve-01"
        initial_doc = {
            "run_id": run_id,
            "filename": "Turbine_Manual.pdf",
            "content_hash": "hash-turbine-999",
            "status": "done",
            "document_status": "Pending Review",
            "user_id": "editor-uid-01",
            "user_email": "editor@company.com",
            "submitted_by": "editor@company.com",
            "assigned_approver": "approver@company.com",
            "error": json.dumps({
                "run_id": run_id,
                "user_id": "editor-uid-01",
                "user_email": "editor@company.com",
                "submitted_by": "editor@company.com",
                "assigned_approver": "approver@company.com",
                "document_status": "Pending Review",
            }),
        }
        _store_in_cache(run_id, initial_doc)

        with patch("app.integrations.fabric_sql.fabric_configured", return_value=False):
            # 1. Editor can see it before sign-off
            editor_rows = list_done_extracts(user_id="editor-uid-01", user_email="editor@company.com")
            self.assertTrue(any(r["run_id"] == run_id for r in editor_rows))

            # 2. Approver signs off the document
            success = update_fabric_review_state(
                run_id,
                document_status="Approved",
                approved_by="approver@company.com",
                approved_at="2026-08-30T08:00:00Z",
                user_id="approver-uid-99",
                user_email="approver@company.com",
                user_role="approver",
            )
            self.assertTrue(success)

            # 3. Editor STILL sees the document in My Extracts with Approved status
            editor_rows_after = list_done_extracts(user_id="editor-uid-01", user_email="editor@company.com")
            matched = [r for r in editor_rows_after if r["run_id"] == run_id]
            self.assertEqual(len(matched), 1)
            self.assertEqual(matched[0]["document_status"], "Approved")
            self.assertEqual(matched[0]["approved_by"], "approver@company.com")

    def test_editor_edits_persisted_and_approver_receives_edited_version(self):
        """Validates that when an editor edits records and syncs review state,
        the edited version is returned on load, while the immutable baseline is preserved."""
        from app.integrations.fabric_cache import (
            _store_in_cache,
            update_fabric_review_state,
            load_extract_from_fabric,
        )
        from app.models import SparePartRow, MaintenanceRow, TroubleshootingRow
        import json

        run_id = "run-editor-edits-sync-99"
        raw_sp = [{
            "id": 1,
            "part_name": "Raw AI Bearing",
            "part_number_code": "BRG-001",
            "quantity": "2",
            "status": "Pending Review",
        }]
        raw_mt = [{
            "id": 1,
            "equipment_title": "Centrifugal Pump",
            "checks_instructions": "Inspect mechanical seal",
            "status": "Pending Review",
        }]
        raw_tr = [{
            "id": 1,
            "problem": "Vibration",
            "root_cause_solution": "Misalignment",
            "status": "Pending Review",
        }]

        initial_doc = {
            "run_id": run_id,
            "filename": "Pump_Manual.pdf",
            "status": "done",
            "document_status": "Pending Review",
            "user_id": "editor-uid-01",
            "user_email": "editor@company.com",
            "submitted_by": "editor@company.com",
            "spare_parts": raw_sp,
            "maintenance": raw_mt,
            "troubleshooting": raw_tr,
            "error": json.dumps({
                "run_id": run_id,
                "user_id": "editor-uid-01",
                "user_email": "editor@company.com",
                "document_status": "Pending Review",
                "spare_parts": raw_sp,
                "maintenance": raw_mt,
                "troubleshooting": raw_tr,
                "raw_payload": {
                    "spare_parts": raw_sp,
                    "maintenance": raw_mt,
                    "troubleshooting": raw_tr,
                    "extracted_at": "2026-08-30T10:00:00Z",
                },
            }),
        }
        _store_in_cache(run_id, initial_doc)

        with patch("app.integrations.fabric_sql.fabric_configured", return_value=False):
            # 1. Editor modifies the bearing name and quantity
            edited_sp = [
                SparePartRow(
                    id=1,
                    part_name="High-Temperature Precision Roller Bearing",
                    part_number_code="BRG-001-MOD",
                    quantity="4 (Upgraded)",
                    status="In Review",
                )
            ]
            ok = update_fabric_review_state(
                run_id,
                document_status="Pending Sign-Off",
                approved_by="editor@company.com",
                user_id="editor-uid-01",
                user_email="editor@company.com",
                user_role="editor",
                spare_parts=edited_sp,
                maintenance=[MaintenanceRow(**raw_mt[0])],
                troubleshooting=[TroubleshootingRow(**raw_tr[0])],
            )
            self.assertTrue(ok)

            # 2. Re-loading the extract returns the edited working rows
            extract = load_extract_from_fabric(run_id, filename="Pump_Manual.pdf")
            self.assertEqual(len(extract.spare_parts), 1)
            self.assertEqual(extract.spare_parts[0].part_name, "High-Temperature Precision Roller Bearing")
            self.assertEqual(extract.spare_parts[0].quantity, "4 (Upgraded)")
            self.assertEqual(extract.meta.document_status, "Pending Sign-Off")

            # 3. Verify the immutable raw baseline is intact
            self.assertIsNotNone(extract.baseline)
            self.assertEqual(len(extract.baseline.spare_parts), 1)
            self.assertEqual(extract.baseline.spare_parts[0].part_name, "Raw AI Bearing")
            self.assertTrue(extract.meta.has_diff)

    def test_review_sync_resilient_row_parsing(self):
        """Verifies that FabricReviewSyncRequest safely accepts and normalizes messy input types."""
        from app.models import FabricReviewSyncRequest

        # String confidence, lowercase status, and dictionary items
        req = FabricReviewSyncRequest(
            document_status="pending sign-off",
            spare_parts=[
                {
                    "id": "1",
                    "part_name": "Resilient Part",
                    "confidence": "0.95",
                    "status": "in review",
                    "page": "12",
                }
            ]
        )
        self.assertEqual(req.document_status, "Pending Sign-Off")
        self.assertIsNotNone(req.spare_parts)
        self.assertEqual(len(req.spare_parts), 1)
        self.assertEqual(req.spare_parts[0]["part_name"], "Resilient Part")

    def test_review_requeue_blocked_for_globally_approved_hash(self):
        from app.integrations.fabric_cache import review_requeue_blocked_message

        approved = {
            "run_id": "run-approved",
            "content_hash": "hash-1",
            "document_status": "Approved",
            "approved_by": "admin@corp.com",
            "approved_at": "2026-08-20T12:00:00Z",
        }
        with patch("app.integrations.fabric_cache.find_approved_run_by_content_hash", return_value=approved):
            msg = review_requeue_blocked_message("hash-1", new_status="Pending Sign-Off")
            self.assertIsNotNone(msg)
            self.assertIn("admin@corp.com", msg)
            self.assertIsNone(review_requeue_blocked_message("hash-1", new_status="Approved"))








