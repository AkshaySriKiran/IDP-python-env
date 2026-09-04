import json
import unittest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from app.auth import store
from app.auth.schemas import UserPublic
from app.auth.store import create_user
from app.config import get_jwt_secret
from app.integrations.fabric_cache import _row_matches_user, list_done_extracts
from app.main import app
from app.models import ExtractMeta, ExtractOptions, ExtractResponse
from app.security import create_access_token


class TestDataIsolationAndPrivacyScoping(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.secret = get_jwt_secret()

        # Seed standard users
        self.editor_user = create_user(
            email="alice.engineer@corp.com",
            display_name="Alice Engineer",
            role="editor",
            password="Password123!",
        )
        self.editor_token = create_access_token(
            user_id=self.editor_user.id,
            email=self.editor_user.email,
            role=self.editor_user.role,
            secret=self.secret,
        )

        self.viewer_user = create_user(
            email="charlie.viewer@corp.com",
            display_name="Charlie Viewer",
            role="viewer",
            password="Password123!",
        )
        self.viewer_token = create_access_token(
            user_id=self.viewer_user.id,
            email=self.viewer_user.email,
            role=self.viewer_user.role,
            secret=self.secret,
        )

        self.approver_user = create_user(
            email="dan.approver@corp.com",
            display_name="Dan Approver",
            role="approver",
            password="Password123!",
        )
        self.approver_token = create_access_token(
            user_id=self.approver_user.id,
            email=self.approver_user.email,
            role=self.approver_user.role,
            secret=self.secret,
        )

        self.admin_user = create_user(
            email="admin.security@corp.com",
            display_name="Admin Security",
            role="admin",
            password="Password123!",
        )
        self.admin_token = create_access_token(
            user_id=self.admin_user.id,
            email=self.admin_user.email,
            role=self.admin_user.role,
            secret=self.secret,
        )

    def test_row_matches_user_unit_scenarios(self):
        """Verify _row_matches_user against multiple telemetry formats."""
        # 1. Direct column match
        row1 = {"user_id": "usr-111", "user_email": "alice@corp.com", "engine": "gemini"}
        self.assertTrue(_row_matches_user(row1, user_id="usr-111"))
        self.assertTrue(_row_matches_user(row1, user_email="alice@corp.com"))
        self.assertTrue(_row_matches_user(row1, user_email="ALICE@CORP.COM"))
        self.assertFalse(_row_matches_user(row1, user_id="usr-999"))
        self.assertFalse(_row_matches_user(row1, user_email="bob@corp.com"))

        # 2. Engine tag match (e.g. gemini:gemini-1.5-flash [user:alice@corp.com])
        row2 = {"engine": "gemini:gemini-1.5-flash [user:alice@corp.com]"}
        self.assertTrue(_row_matches_user(row2, user_email="alice@corp.com"))
        self.assertFalse(_row_matches_user(row2, user_email="bob@corp.com"))

        # 3. JSON envelope in error column
        envelope = {"user_id": "usr-222", "user_email": "dan@corp.com", "document_status": "Approved"}
        row3 = {"error": json.dumps(envelope), "engine": "gemini"}
        self.assertTrue(_row_matches_user(row3, user_id="usr-222"))
        self.assertTrue(_row_matches_user(row3, user_email="dan@corp.com"))
        self.assertFalse(_row_matches_user(row3, user_id="usr-111"))

        # 4. Approver/Submitter match
        row4 = {"approved_by": "lead.approver@corp.com", "engine": "gemini"}
        self.assertTrue(_row_matches_user(row4, user_email="lead.approver@corp.com"))

        # 5. Exact email only — overlapping prefixes must not match
        row5 = {"user_email": "user@corp.com.au", "engine": "gemini"}
        self.assertFalse(_row_matches_user(row5, user_email="user@corp.com"))
        row6 = {"user_email": "alice.engineer@corp.com"}
        self.assertFalse(_row_matches_user(row6, user_email="alice@corp.com"))
        self.assertTrue(_row_matches_user(row6, user_email="Alice.Engineer@corp.com"))

    def test_list_done_extracts_sql_parameterization(self):
        """Verify list_done_extracts constructs parameterized WHERE clauses."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.description = [("run_id",), ("filename",), ("status",), ("error",), ("engine",)]
        mock_cur.fetchall.return_value = [
            ("run-alice-1", "pump.pdf", "done", json.dumps({"user_email": "alice@corp.com"}), "gemini [user:alice@corp.com]"),
            ("run-bob-1", "valve.pdf", "done", json.dumps({"user_email": "bob@corp.com"}), "gemini [user:bob@corp.com]"),
        ]

        with patch("app.integrations.fabric_sql.fabric_configured", return_value=True), \
             patch("app.integrations.fabric_sql.connect", return_value=mock_conn), \
             patch("app.integrations.fabric_sql._get_table_columns", return_value={"user_id", "user_email"}):
            
            rows = list_done_extracts(limit=50, user_id="usr-alice", user_email="alice@corp.com")
            
            # Verify SQL query executed was parameterized
            self.assertTrue(mock_cur.execute.called)
            query_sql, query_params = mock_cur.execute.call_args[0]
            self.assertIn("WHERE", query_sql)
            self.assertIn("user_id = ?", query_sql)
            self.assertIn("LOWER(user_email) = ?", query_sql)
            
            # Verify only Alice's row survived in-memory verification
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["run_id"], "run-alice-1")
            self.assertEqual(rows[0]["user_email"], "alice@corp.com")

    def test_fabric_extracts_endpoint_scopes_for_standard_users(self):
        """GET /api/fabric/extracts must strictly scope to current standard user."""
        headers = {"Authorization": f"Bearer {self.editor_token}"}
        
        mock_rows = [
            {"run_id": "run-alice-01", "filename": "alice_pump.pdf", "user_id": self.editor_user.id, "user_email": self.editor_user.email, "document_status": "Approved"},
        ]

        with patch("app.main.list_done_extracts", return_value=mock_rows) as mock_list:
            
            resp = self.client.get("/api/fabric/extracts", headers=headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["count"], 1)
            self.assertEqual(data["items"][0]["run_id"], "run-alice-01")

            # Verify list_done_extracts received user scoping
            mock_list.assert_called_once_with(
                limit=100,
                user_id=self.editor_user.id,
                user_email=self.editor_user.email,
            )

    def test_fabric_extracts_endpoint_blocks_all_users_for_non_admin(self):
        """GET /api/fabric/extracts?all_users=true must ignore all_users for non-admins."""
        headers = {"Authorization": f"Bearer {self.editor_token}"}
        
        with patch("app.main.list_done_extracts", return_value=[]) as mock_list:
            
            resp = self.client.get("/api/fabric/extracts?all_users=true", headers=headers)
            self.assertEqual(resp.status_code, 200)

            # Still filtered by user_id and user_email despite all_users=true
            mock_list.assert_called_once_with(
                limit=100,
                user_id=self.editor_user.id,
                user_email=self.editor_user.email,
            )

    def test_fabric_extracts_endpoint_admin_default_vs_global_view(self):
        """Admins default to personal extracts and can request all_users=true."""
        headers = {"Authorization": f"Bearer {self.admin_token}"}

        # 1. Admin without all_users (default My Extracts)
        with patch("app.main.list_done_extracts", return_value=[]) as mock_list:
            
            resp = self.client.get("/api/fabric/extracts", headers=headers)
            self.assertEqual(resp.status_code, 200)
            mock_list.assert_called_once_with(
                limit=100,
                user_id=self.admin_user.id,
                user_email=self.admin_user.email,
            )

        # 2. Admin with all_users=true (global view)
        with patch("app.main.list_done_extracts", return_value=[]) as mock_list_all:
            
            resp = self.client.get("/api/fabric/extracts?all_users=true", headers=headers)
            self.assertEqual(resp.status_code, 200)
            mock_list_all.assert_called_once_with(
                limit=100,
                user_id=None,
                user_email=None,
            )

    def test_fabric_extract_get_unauthorized_access_prevention(self):
        """Standard user cannot access extract run owned by someone else."""
        editor_headers = {"Authorization": f"Bearer {self.editor_token}"}

        # Extract belongs to bob@other.com
        other_user_meta = {
            "run_id": "run-foreign-999",
            "filename": "confidential_specs.pdf",
            "user_id": "usr-foreign-888",
            "user_email": "bob@other.com",
            "overall_score": 95.0,
        }

        with patch("app.main.get_done_run", return_value=other_user_meta):
            
            resp = self.client.get("/api/fabric/extracts/run-foreign-999", headers=editor_headers)
            self.assertEqual(resp.status_code, 403)
            self.assertIn("Access Denied", resp.json()["detail"])

    def test_fabric_extract_get_authorized_for_owner_admin_and_approver(self):
        """Owner, Admin, and the strictly assigned approver can access the extract run."""
        extract_meta = {
            "run_id": "run-alice-123",
            "filename": "generator_manual.pdf",
            "user_id": self.editor_user.id,
            "user_email": self.editor_user.email,
            "assigned_approver": self.approver_user.email,
            "overall_score": 88.0,
        }
        mock_extract_resp = ExtractResponse(
            meta=ExtractMeta(
                filename="generator_manual.pdf",
                overall_score=88.0,
                engine="gemini:gemini-1.5-flash",
                parse_strategy="ocr",
            ),
            spare_parts=[],
            maintenance=[],
            troubleshooting=[],
        )

        other_approver = create_user(
            email="eve.approver@corp.com",
            display_name="Eve Approver",
            role="approver",
            password="Password123!",
        )
        other_token = create_access_token(
            user_id=other_approver.id,
            email=other_approver.email,
            role=other_approver.role,
            secret=self.secret,
        )

        with patch("app.main.get_done_run", return_value=extract_meta), \
             patch("app.main.load_extract_from_fabric", return_value=mock_extract_resp):
            
            # 1. Owner (Alice) can access
            resp1 = self.client.get(
                "/api/fabric/extracts/run-alice-123",
                headers={"Authorization": f"Bearer {self.editor_token}"},
            )
            self.assertEqual(resp1.status_code, 200)

            # 2. Admin can access
            resp2 = self.client.get(
                "/api/fabric/extracts/run-alice-123",
                headers={"Authorization": f"Bearer {self.admin_token}"},
            )
            self.assertEqual(resp2.status_code, 200)

            # 3. Assigned approver can access
            resp3 = self.client.get(
                "/api/fabric/extracts/run-alice-123",
                headers={"Authorization": f"Bearer {self.approver_token}"},
            )
            self.assertEqual(resp3.status_code, 200)

            # 4. Unassigned approver is forbidden
            resp4 = self.client.get(
                "/api/fabric/extracts/run-alice-123",
                headers={"Authorization": f"Bearer {other_token}"},
            )
            self.assertEqual(resp4.status_code, 403)

    def test_pending_approvals_scoping_behavior(self):
        """Pending approvals is scoped strictly to assigned approver mapping."""
        # Setup editor assigned to self.approver_user
        store.update_user(self.editor_user.id, assigned_approver=self.approver_user.email)
        
        mock_rows = [
            {"run_id": "r1", "document_status": "Pending Review", "user_id": self.editor_user.id, "user_email": self.editor_user.email, "assigned_approver": self.approver_user.email},
            {"run_id": "r2", "document_status": "Pending Review", "user_id": "other-id", "user_email": "other@corp.com", "assigned_approver": "different.approver@corp.com"},
        ]

        with patch("app.main.list_done_extracts", return_value=mock_rows) as mock_list, \
             patch("app.auth.store.list_users", return_value=[self.editor_user, self.approver_user, self.admin_user]):
            
            # 1. Standard editor: gets only their own pending submission
            resp = self.client.get(
                "/api/fabric/pending-approvals",
                headers={"Authorization": f"Bearer {self.editor_token}"},
            )
            self.assertEqual(resp.status_code, 200)
            data1 = resp.json()
            self.assertEqual(data1["count"], 1)
            self.assertEqual(data1["items"][0]["run_id"], "r1")

            # 2. Approver: receives ONLY documents from subordinate editor (r1), NOT r2
            mock_list.reset_mock()
            resp2 = self.client.get(
                "/api/fabric/pending-approvals",
                headers={"Authorization": f"Bearer {self.approver_token}"},
            )
            self.assertEqual(resp2.status_code, 200)
            data2 = resp2.json()
            self.assertEqual(data2["count"], 1)
            self.assertEqual(data2["items"][0]["run_id"], "r1")

            # 3. Admin: global oversight receives both r1 and r2
            mock_list.reset_mock()
            resp3 = self.client.get(
                "/api/fabric/pending-approvals",
                headers={"Authorization": f"Bearer {self.admin_token}"},
            )
            self.assertEqual(resp3.status_code, 200)
            data3 = resp3.json()
            self.assertEqual(data3["count"], 2)

    def test_pending_approvals_reads_assigned_approver_from_envelope(self):
        """Bell backend hydrates assigned_approver from the JSON envelope in `error`."""
        store.update_user(self.editor_user.id, assigned_approver=self.approver_user.email)
        envelope = json.dumps({
            "document_status": "Pending Review",
            "submitted_by": self.editor_user.email,
            "assigned_approver": self.approver_user.email,
            "user_email": self.editor_user.email,
            "user_id": self.editor_user.id,
        })
        mock_rows = [
            {"run_id": "env-1", "filename": "Pump.pdf", "error": envelope},
            {
                "run_id": "env-other",
                "filename": "Other.pdf",
                "error": json.dumps({
                    "document_status": "Pending Review",
                    "submitted_by": "other@corp.com",
                    "assigned_approver": "someone.else@corp.com",
                }),
            },
        ]
        with patch("app.main.list_done_extracts", return_value=mock_rows), \
             patch("app.auth.store.list_users", return_value=[self.editor_user, self.approver_user, self.admin_user]):
            resp = self.client.get(
                "/api/fabric/pending-approvals",
                headers={"Authorization": f"Bearer {self.approver_token}"},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["count"], 1)
            self.assertEqual(data["items"][0]["run_id"], "env-1")
            self.assertEqual(data["items"][0]["assigned_approver"], self.approver_user.email)

    def test_apply_log_envelope_hydrates_approver_fields(self):
        from app.integrations.fabric_cache import _apply_log_envelope
        env = json.dumps({
            "document_status": "Pending Sign-Off",
            "submitted_by": "editor@corp.com",
            "assigned_approver": "dan.approver@corp.com",
            "user_email": "editor@corp.com",
            "user_id": "uid-1",
        })
        row = _apply_log_envelope({"run_id": "r1", "Error": env})
        self.assertEqual(row["assigned_approver"], "dan.approver@corp.com")
        self.assertEqual(row["submitted_by"], "editor@corp.com")
        self.assertEqual(row["document_status"], "Pending Sign-Off")
        self.assertEqual(row["user_email"], "editor@corp.com")

    def test_unauthenticated_access_returns_empty_and_no_leak(self):
        """Unauthenticated requests must never return global extracts."""
        with patch("app.main.list_done_extracts") as mock_list:
            resp = self.client.get("/api/fabric/extracts")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["items"], [])
            self.assertEqual(data["count"], 0)
            # Fabric query should never be called for unauthenticated requests
            mock_list.assert_not_called()

    def test_token_claim_fallback_for_external_sso_users(self):
        """Tokens with valid claims must resolve user identity even if user store is ephemeral/empty."""
        external_token = create_access_token(
            user_id="sso-user-uuid-999",
            email="sso.engineer@corp.com",
            role="editor",
            secret=self.secret,
        )
        mock_rows = [
            {"run_id": "r-sso-1", "filename": "sso_manual.pdf", "user_id": "sso-user-uuid-999", "user_email": "sso.engineer@corp.com", "document_status": "Approved"}
        ]
        with patch("app.main.list_done_extracts", return_value=mock_rows) as mock_list:
            resp = self.client.get(
                "/api/fabric/extracts",
                headers={"Authorization": f"Bearer {external_token}"},
            )
            self.assertEqual(resp.status_code, 200)
            mock_list.assert_called_with(
                limit=100,
                user_id="sso-user-uuid-999",
                user_email="sso.engineer@corp.com",
            )

    def test_cached_document_extraction_logs_history_for_current_user(self):
        """When an existing/cached file is extracted, a history log must be created for the current user."""
        import asyncio
        from app.integrations.fabric_cache import extract_with_fabric_cache

        mock_cached_run = {
            "run_id": "run-central-repo-orig",
            "overall_score": 92.5,
            "filename": "existing_manual.pdf",
            "user_id": "orig-user-001",
            "user_email": "orig.uploader@corp.com",
        }

        mock_loaded_response = ExtractResponse(
            meta=ExtractMeta(
                filename="existing_manual.pdf",
                overall_score=92.5,
                engine="fabric-cache",
                parse_strategy="cache",
                run_id="run-central-repo-orig",
            ),
            spare_parts=[],
            maintenance=[],
            troubleshooting=[],
        )

        dummy_bytes = b"%PDF-1.4 mock content for cache hit"
        options = ExtractOptions(engine="gemini", parse_strategy="ocr")

        with patch("app.integrations.fabric_sql.fabric_configured", return_value=True), \
             patch("app.integrations.fabric_cache.find_done_run", return_value=mock_cached_run), \
             patch("app.integrations.fabric_cache.find_user_run_by_content_hash", return_value=None), \
             patch("app.integrations.fabric_cache.load_extract_from_fabric", return_value=mock_loaded_response), \
             patch("app.integrations.fabric_cache.save_extract_to_fabric", return_value="run-new-user-session-888") as mock_save:

            res = asyncio.run(
                extract_with_fabric_cache(
                    file_bytes=dummy_bytes,
                    filename="existing_manual.pdf",
                    options=options,
                    extract_fn=MagicMock(),
                    user_id=self.editor_user.id,
                    user_email=self.editor_user.email,
                    user_role=self.editor_user.role,
                )
            )

            # 1. Verify save_extract_to_fabric was called to log history for the current user
            self.assertTrue(mock_save.called)
            save_kwargs = mock_save.call_args[1]
            self.assertEqual(save_kwargs["user_id"], self.editor_user.id)
            self.assertEqual(save_kwargs["user_email"], self.editor_user.email)
            self.assertEqual(save_kwargs["user_role"], self.editor_user.role)
            self.assertEqual(save_kwargs["filename"], "existing_manual.pdf")

            # 2. Verify returned result meta has the new session run_id
            self.assertEqual(res.meta.run_id, "run-new-user-session-888")
            self.assertEqual(res.meta.document_status, "Pending Review")
            self.assertFalse(res.meta.already_approved)

    def test_cache_hit_shows_global_approved_status(self):
        """Uploading a previously signed-off document shows Approved for any new uploader."""
        import asyncio
        from app.integrations.fabric_cache import extract_with_fabric_cache
        from app.models import BaselineExtraction, SparePartRow

        orig_raw = {
            "spare_parts": [{"id": 1, "part_name": "Piston", "part_number_code": "PA-1"}],
            "maintenance": [],
            "troubleshooting": [],
            "doc_metadata": {"title": "Pump Manual"},
            "extracted_at": "2026-08-01T00:00:00Z",
        }
        mock_loaded_response = ExtractResponse(
            meta=ExtractMeta(
                filename="signed_manual.pdf",
                overall_score=91.0,
                engine="fabric-cache",
                parse_strategy="cache",
                run_id="run-approved-orig",
                document_status="Approved",
                approved_by="orig.approver@corp.com",
                approved_at="2026-08-20T12:00:00Z",
            ),
            spare_parts=[SparePartRow(id=1, part_name="Piston EDITED", part_number_code="PA-1", status="Approved")],
            maintenance=[],
            troubleshooting=[],
            baseline=BaselineExtraction(
                spare_parts=[SparePartRow(id=1, part_name="Piston", part_number_code="PA-1")],
                maintenance=[],
                troubleshooting=[],
            ),
            raw_payload=orig_raw,
        )
        approved_global = {
            "run_id": "run-approved-orig",
            "overall_score": 91.0,
            "document_status": "Approved",
            "content_hash": "abc",
        }

        with patch("app.integrations.fabric_sql.fabric_configured", return_value=True), \
             patch("app.integrations.fabric_cache.find_done_run", return_value={"run_id": "run-approved-orig", "overall_score": 91.0}), \
             patch("app.integrations.fabric_cache.find_approved_run_by_content_hash", return_value=approved_global), \
             patch("app.integrations.fabric_cache.find_user_run_by_content_hash", return_value=None), \
             patch("app.integrations.fabric_cache.load_extract_from_fabric", return_value=mock_loaded_response), \
             patch("app.integrations.fabric_cache.save_extract_to_fabric", return_value="run-new-approved") as mock_save, \
             patch("app.integrations.fabric_cache.supersede_duplicate_runs", return_value=0), \
             patch("app.notifications.create_notification") as mock_notif:

            res = asyncio.run(
                extract_with_fabric_cache(
                    file_bytes=b"%PDF-1.4 signed",
                    filename="signed_manual.pdf",
                    options=ExtractOptions(engine="gemini", parse_strategy="ocr"),
                    extract_fn=MagicMock(),
                    user_id=self.editor_user.id,
                    user_email=self.editor_user.email,
                    user_role=self.editor_user.role,
                )
            )

            saved_result = mock_save.call_args[1]["result"]
            self.assertEqual(saved_result.meta.document_status, "Approved")
            self.assertEqual(saved_result.meta.approved_by, "orig.approver@corp.com")
            self.assertTrue(saved_result.meta.already_approved)
            self.assertEqual(saved_result.meta.prior_approved_by, "orig.approver@corp.com")
            self.assertEqual(saved_result.spare_parts[0].status, "Approved")
            self.assertEqual(res.meta.document_status, "Approved")
            self.assertTrue(res.meta.already_approved)
            self.assertTrue(mock_notif.called)

    def test_review_sync_blocks_requeue_of_globally_approved_document(self):
        extract_meta = {
            "run_id": "run-dup-approved",
            "filename": "pump.pdf",
            "content_hash": "abc123hash",
            "user_id": self.editor_user.id,
            "user_email": self.editor_user.email,
            "document_status": "Approved",
            "assigned_approver": self.approver_user.email,
            "submitted_by": self.editor_user.email,
        }
        approved_global = {
            "run_id": "run-canonical-approved",
            "content_hash": "abc123hash",
            "document_status": "Approved",
            "approved_by": "orig.approver@corp.com",
            "approved_at": "2026-08-20T12:00:00Z",
        }
        with patch("app.main.get_done_run", return_value=extract_meta), \
             patch("app.integrations.fabric_cache.find_approved_run_by_content_hash", return_value=approved_global), \
             patch("app.main.update_fabric_review_state", return_value=True) as mock_upd:
            resp = self.client.post(
                "/api/fabric/extracts/run-dup-approved/review-sync",
                json={"document_status": "Pending Sign-Off"},
                headers={"Authorization": f"Bearer {self.editor_token}"},
            )
            self.assertEqual(resp.status_code, 409)
            self.assertIn("already signed off", resp.json()["detail"])
            mock_upd.assert_not_called()

    def test_share_requires_document_ownership(self):
        extract_meta = {
            "run_id": "run-alice-share",
            "filename": "pump.pdf",
            "user_id": self.editor_user.id,
            "user_email": self.editor_user.email,
        }
        with patch("app.main.get_done_run", return_value=extract_meta):
            denied = self.client.post(
                "/api/fabric/extracts/run-alice-share/share",
                headers={"Authorization": f"Bearer {self.approver_token}"},
            )
            self.assertEqual(denied.status_code, 403)

            owned = self.client.post(
                "/api/fabric/extracts/run-alice-share/share",
                headers={"Authorization": f"Bearer {self.editor_token}"},
            )
            self.assertEqual(owned.status_code, 200)
            self.assertIn("share_token", owned.json())

    def test_review_sync_forbids_unassigned_approver(self):
        extract_meta = {
            "run_id": "run-alice-rev",
            "filename": "pump.pdf",
            "user_id": self.editor_user.id,
            "user_email": self.editor_user.email,
            "assigned_approver": "someone.else@corp.com",
            "submitted_by": self.editor_user.email,
        }
        with patch("app.main.get_done_run", return_value=extract_meta), \
             patch("app.main.update_fabric_review_state", return_value=True) as mock_upd:
            resp = self.client.post(
                "/api/fabric/extracts/run-alice-rev/review-sync",
                json={"document_status": "Approved", "approved_by": self.approver_user.email},
                headers={"Authorization": f"Bearer {self.approver_token}"},
            )
            self.assertEqual(resp.status_code, 403)
            mock_upd.assert_not_called()

    def test_notifications_fanout_and_list_scoped_to_recipient(self):
        from pathlib import Path
        import tempfile
        from app import notifications as nmod

        extract_meta = {
            "run_id": "run-alice-notif",
            "filename": "pump.pdf",
            "doc_title": "Mud Pump Manual",
            "user_id": self.editor_user.id,
            "user_email": self.editor_user.email,
            "submitted_by": self.editor_user.email,
            "assigned_approver": self.approver_user.email,
        }
        with tempfile.TemporaryDirectory() as td:
            notif_path = Path(td) / "notifications.json"
            with patch.object(nmod, "NOTIF_FILE", notif_path), \
                 patch("app.main.get_done_run", return_value=extract_meta), \
                 patch("app.main.update_fabric_review_state", return_value=True), \
                 patch("app.main.update_extract_audit_review_state", return_value=True):
                submit = self.client.post(
                    "/api/fabric/extracts/run-alice-notif/review-sync",
                    json={"document_status": "Pending Sign-Off"},
                    headers={"Authorization": f"Bearer {self.editor_token}"},
                )
                self.assertEqual(submit.status_code, 200)

                approver_list = self.client.get(
                    "/api/notifications",
                    headers={"Authorization": f"Bearer {self.approver_token}"},
                )
                self.assertEqual(approver_list.status_code, 200)
                items = approver_list.json()["items"]
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["event_type"], "submitted")
                self.assertEqual(items[0]["title"], "Mud Pump Manual")
                self.assertIn("fabric_run_id=run-alice-notif", items[0]["url"])

                editor_list = self.client.get(
                    "/api/notifications",
                    headers={"Authorization": f"Bearer {self.editor_token}"},
                )
                self.assertEqual(editor_list.json()["count"], 0)

                signoff = self.client.post(
                    "/api/fabric/extracts/run-alice-notif/review-sync",
                    json={"document_status": "Approved", "approved_by": self.approver_user.email},
                    headers={"Authorization": f"Bearer {self.approver_token}"},
                )
                self.assertEqual(signoff.status_code, 200)

                editor_after = self.client.get(
                    "/api/notifications",
                    headers={"Authorization": f"Bearer {self.editor_token}"},
                )
                ed_items = editor_after.json()["items"]
                self.assertEqual(len(ed_items), 1)
                self.assertEqual(ed_items[0]["event_type"], "signed_off")
                self.assertIn("fabric_run_id=run-alice-notif", ed_items[0]["url"])

    def test_cache_hit_reuses_existing_user_run_instead_of_duplicate(self):
        """Re-uploading the same file must reopen the user's existing run, not insert a new row."""
        import asyncio
        from app.integrations.fabric_cache import extract_with_fabric_cache

        existing = {
            "run_id": "run-user-existing",
            "content_hash": "abc123hash",
            "filename": "1.pdf",
            "user_id": self.editor_user.id,
            "user_email": self.editor_user.email,
            "document_status": "Approved",
            "overall_score": 94.4,
        }
        mock_result = ExtractResponse(
            meta=ExtractMeta(
                filename="1.pdf",
                overall_score=94.4,
                engine="fabric-cache",
                parse_strategy="cache",
                run_id="run-user-existing",
                document_status="Approved",
            ),
            spare_parts=[],
            maintenance=[],
            troubleshooting=[],
        )

        with patch("app.integrations.fabric_cache.find_done_run", return_value={"run_id": "run-global", "overall_score": 94.4}), \
             patch("app.integrations.fabric_cache.find_user_run_by_content_hash", return_value=existing), \
             patch("app.integrations.fabric_cache.load_extract_from_fabric", return_value=mock_result), \
             patch("app.integrations.fabric_cache.save_extract_to_fabric") as mock_save:
            res = asyncio.run(
                extract_with_fabric_cache(
                    file_bytes=b"%PDF-same-file",
                    filename="1.pdf",
                    options=ExtractOptions(engine="gemini", parse_strategy="ocr"),
                    extract_fn=MagicMock(),
                    user_id=self.editor_user.id,
                    user_email=self.editor_user.email,
                    user_role=self.editor_user.role,
                )
            )
            mock_save.assert_not_called()
            self.assertEqual(res.meta.run_id, "run-user-existing")

    def test_review_sync_row_level_does_not_flood_editor_notifications(self):
        """Intermediate In Review / Pending Review syncs must not notify the editor."""
        from app.main import _fanout_review_notifications
        from app import notifications as nmod
        from pathlib import Path
        import tempfile

        meta = {
            "run_id": "run-doc",
            "filename": "1.pdf",
            "user_email": self.editor_user.email,
            "submitted_by": self.editor_user.email,
            "assigned_approver": self.approver_user.email,
        }
        with tempfile.TemporaryDirectory() as td:
            with patch.object(nmod, "NOTIF_FILE", Path(td) / "notifications.json"), \
                 patch("app.main.upsert_document_notification") as mock_upsert:
                _fanout_review_notifications(
                    meta,
                    "In Review",
                    self.approver_user.email,
                    "run-doc",
                    previous_status="Pending Review",
                )
                _fanout_review_notifications(
                    meta,
                    "Pending Review",
                    self.approver_user.email,
                    "run-doc",
                    previous_status="In Review",
                )
                mock_upsert.assert_not_called()

                _fanout_review_notifications(
                    meta,
                    "Approved",
                    self.approver_user.email,
                    "run-doc",
                    previous_status="Pending Review",
                    maintenance=[{"status": "Approved"}] * 14,
                )
                self.assertEqual(mock_upsert.call_count, 1)
                body = mock_upsert.call_args[1].get("body") or ""
                self.assertIn("14 approved", body)

    def test_upsert_coalesces_review_outcomes_without_repeat_email(self):
        """Row-by-row sign-off should refresh one unread notif and email only on create."""
        from app import notifications as nmod
        from pathlib import Path
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as td:
            with patch.object(nmod, "NOTIF_FILE", Path(td) / "notifications.json"), \
                 patch.object(nmod, "_fabric_ready", return_value=False), \
                 patch.object(nmod, "_try_email") as mock_email:
                first = nmod.upsert_document_notification(
                    recipient_email=self.editor_user.email,
                    event_type="signed_off",
                    run_id="run-coalesce",
                    title="Doc",
                    actor_email=self.approver_user.email,
                    body="1 approved, 0 rejected, 2 pending",
                    email_context={"final_status": "Approved"},
                )
                self.assertIsNotNone(first)
                self.assertEqual(mock_email.call_count, 1)

                second = nmod.upsert_document_notification(
                    recipient_email=self.editor_user.email,
                    event_type="revision_requested",
                    run_id="run-coalesce",
                    title="Doc",
                    actor_email=self.approver_user.email,
                    body="1 approved, 1 rejected, 1 pending",
                    email_context={"final_status": "Needs Revision", "comments": "fix row"},
                )
                self.assertEqual(second["id"], first["id"])
                self.assertEqual(second["event_type"], "revision_requested")
                self.assertEqual(mock_email.call_count, 1)

                items = nmod.list_for_user(self.editor_user.email)
                unread = [i for i in items if i.get("run_id") == "run-coalesce" and not i.get("read")]
                self.assertEqual(len(unread), 1)


if __name__ == "__main__":
    unittest.main()
