import unittest
from app.extractors.parse import repair_truncated_json, parse_model_json_response, sanitize_val


class TestParsingModule(unittest.TestCase):
    def test_sanitize_val(self):
        self.assertEqual(sanitize_val(None), "NA")
        self.assertEqual(sanitize_val("undefined"), "NA")
        self.assertEqual(sanitize_val("  null  "), "NA")
        self.assertEqual(sanitize_val("Valve 101"), "Valve 101")

    def test_repair_truncated_json(self):
        truncated = '{"maintenance": [{"equipment_title": "Pump", "checks_instructions": "Inspect'
        repaired = repair_truncated_json(truncated)
        parsed = parse_model_json_response(repaired)
        self.assertIn("maintenance", parsed)
        self.assertIsInstance(parsed["maintenance"], list)


if __name__ == "__main__":
    unittest.main()
