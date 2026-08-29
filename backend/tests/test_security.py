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


if __name__ == "__main__":
    unittest.main()
