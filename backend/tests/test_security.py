import unittest
from app.security import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
    validate_outbound_url,
)


class TestSecurityModule(unittest.TestCase):
    def test_password_hashing_and_verification(self):
        pwd = "StrongPassword#2026"
        hashed = hash_password(pwd)
        self.assertTrue(hashed.startswith("pbkdf2_sha256$"))
        self.assertTrue(verify_password(pwd, hashed))
        self.assertFalse(verify_password("WrongPassword", hashed))

    def test_jwt_lifecycle(self):
        secret = "super_secret_test_key_minimum_length_32_bytes!!"
        token = create_access_token(
            user_id="usr_123",
            email="test@omniparse.local",
            role="admin",
            secret=secret,
            expire_hours=1,
        )
        payload = decode_access_token(token, secret)
        self.assertEqual(payload["sub"], "usr_123")
        self.assertEqual(payload["email"], "test@omniparse.local")
        self.assertEqual(payload["role"], "admin")

    def test_ssrf_blocks_cloud_metadata(self):
        with self.assertRaises(ValueError):
            validate_outbound_url("http://169.254.169.254/latest/meta-data/")

    def test_ssrf_blocks_invalid_schemes(self):
        with self.assertRaises(ValueError):
            validate_outbound_url("file:///etc/passwd")

        with self.assertRaises(ValueError):
            validate_outbound_url("gopher://127.0.0.1:6379/_test")

    def test_ssrf_host_whitelist(self):
        allowed = ["localhost", "127.0.0.1", "api.gemini.ai"]
        result = validate_outbound_url("http://localhost:11434", allowed_hosts=allowed)
        self.assertEqual(result, "http://localhost:11434")

        with self.assertRaises(ValueError):
            validate_outbound_url("http://evil-attacker-server.com:11434", allowed_hosts=allowed)


class TestGraphNotificationEmail(unittest.TestCase):
    def test_submitted_template_contains_link_and_status(self):
        from app.email_graph import build_notification_email

        subject, html, text = build_notification_email(
            event_type="submitted",
            title="CAT Pump Manual",
            open_url="https://d11bl7hg497hj.cloudfront.net/index.html?fabric_run_id=abc123",
            actor_email="editor@corp.com",
            context={"filename": "pump.pdf", "submitted_at": "2026-09-04T12:00:00Z"},
        )
        self.assertIn("submitted for your review", subject.lower())
        self.assertIn("fabric_run_id=abc123", html)
        self.assertIn("Pending Sign-Off", html)
        self.assertIn("Review and sign off", text)

    def test_signed_off_and_revision_subjects(self):
        from app.email_graph import build_notification_email

        subj_ok, html_ok, _ = build_notification_email(
            event_type="signed_off",
            title="Pump Manual",
            open_url="https://example.com/index.html?fabric_run_id=r1",
            actor_email="approver@corp.com",
            context={"final_status": "Approved", "record_summary": "3 approved, 0 rejected, 0 pending"},
        )
        self.assertIn("has been approved", subj_ok)
        self.assertIn("3 approved", html_ok)
        self.assertNotIn("Approver comments", html_ok)

        subj_rev, html_rev, text_rev = build_notification_email(
            event_type="revision_requested",
            title="Pump Manual",
            open_url="https://example.com/index.html?fabric_run_id=r1",
            context={"final_status": "Needs Revision", "comments": "Check page 4"},
        )
        self.assertIn("revisions requested", subj_rev.lower())
        self.assertIn("Check page 4", text_rev)
        self.assertIn("Approver comments", html_rev)
        self.assertNotIn("No additional comments were provided", html_rev)
        self.assertNotIn("No additional comments were provided", text_rev)

    def test_signed_off_omits_empty_comments_section(self):
        from app.email_graph import build_notification_email

        _, html, text = build_notification_email(
            event_type="signed_off",
            title="Pump Manual",
            open_url="https://example.com/index.html?fabric_run_id=r1",
            context={"final_status": "Approved", "comments": "   "},
        )
        self.assertNotIn("Approver comments", html)
        self.assertNotIn("Approver comments", text)
        self.assertNotIn("No additional comments were provided", html)

    def test_already_approved_template(self):
        from app.email_graph import build_notification_email

        subject, html, _ = build_notification_email(
            event_type="already_approved",
            title="CAT Pump Manual",
            open_url="https://d11bl7hg497hj.cloudfront.net/index.html?fabric_run_id=canon",
            context={"prior_approved_by": "admin@corp.com", "prior_approved_at": "2026-08-31T10:00:00Z", "filename": "cat.pdf"},
        )
        self.assertIn("already approved", subject.lower())
        self.assertIn("admin@corp.com", html)
        self.assertIn("fabric_run_id=canon", html)

    def test_email_disabled_does_not_call_graph(self):
        from unittest.mock import patch
        from app.email_graph import maybe_send_notification_email

        item = {
            "recipient_email": "approver@corp.com",
            "event_type": "submitted",
            "title": "Doc",
            "url": "https://example.com/index.html?fabric_run_id=x",
            "actor_email": "editor@corp.com",
            "body": "submitted",
        }
        with patch.dict("os.environ", {"EMAIL_ENABLED": "false"}, clear=False), \
             patch("app.email_graph.send_graph_mail") as send:
            maybe_send_notification_email(item)
            send.assert_not_called()

    def test_email_enabled_sends_and_skips_self(self):
        from unittest.mock import patch
        from app.email_graph import maybe_send_notification_email

        with patch.dict("os.environ", {"EMAIL_ENABLED": "true"}, clear=False), \
             patch("app.email_graph.send_graph_mail") as send:
            maybe_send_notification_email({
                "recipient_email": "same@corp.com",
                "actor_email": "same@corp.com",
                "event_type": "submitted",
                "title": "Doc",
                "url": "https://x/index.html?fabric_run_id=1",
            })
            send.assert_not_called()
            maybe_send_notification_email({
                "recipient_email": "approver@corp.com",
                "actor_email": "editor@corp.com",
                "event_type": "submitted",
                "title": "Doc",
                "url": "https://x/index.html?fabric_run_id=1",
            })
            send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
