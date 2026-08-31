from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from savior_client.config import ConfigurationError, Settings
from savior_client.database import SqlServerManager
from savior_client.models import InvalidPunch, Punch
from savior_client.onehash import DeliveryError, OneHashClient
from savior_client.runner import SaviorRunner


ENV = """\
SAVIOR_DB_SERVER=KH08\\SQLEXPRESS
SAVIOR_DB_NAME=SAVIOR
ONEHASH_URL=https://example.test
ONEHASH_API_KEY=key
ONEHASH_API_SECRET=secret
"""


class ClientTests(unittest.TestCase):
    def settings(self) -> Settings:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(ENV, encoding="utf-8")
            return Settings.load(path)

    def test_direction_mapping(self):
        stamp = datetime(2026, 8, 27, 9, 30)
        self.assertEqual(Punch(1, "101", stamp, "I").log_type, "IN")
        self.assertEqual(Punch(2, "101", stamp, "out").log_type, "OUT")
        self.assertIsNone(Punch(3, "101", stamp, "N").log_type)
        with self.assertRaises(InvalidPunch):
            Punch(4, "101", stamp, "X").validate()

    def test_retry_delay_is_bounded(self):
        settings = self.settings()
        runner = SaviorRunner(settings, queue=None, client=None)  # type: ignore[arg-type]
        self.assertEqual(runner.retry_delay(1), 60)
        self.assertEqual(runner.retry_delay(2), 120)
        self.assertEqual(runner.retry_delay(50), 3600)

    def test_duplicate_response_detection(self):
        body = {"exception": "This employee already has a log with the same timestamp."}
        self.assertTrue(OneHashClient._is_duplicate(417, body))
        self.assertFalse(OneHashClient._is_duplicate(500, {"exception": "failure"}))

    def test_server_timestamp_confirmation(self):
        client = OneHashClient(self.settings())
        punch = Punch(7, "KH0055", datetime(2026, 2, 4, 8, 56), "N")
        client._confirm_result(
            punch,
            {
                "message": {
                    "protocol_version": 1,
                    "created": True,
                    "checkin": "TEST-CHECKIN",
                    "timestamp": "2026-02-04 08:56:00",
                }
            },
        )
        with self.assertRaises(DeliveryError):
            client._confirm_result(
                punch,
                {"message": {"timestamp": "2026-08-31 15:31:10"}},
            )

    def test_custom_checkin_method_is_default(self):
        settings = self.settings()
        client = OneHashClient(settings)
        self.assertEqual(
            client.endpoint,
            "https://example.test/api/method/savior_add_employee_checkin",
        )

    def test_sql_server_uses_trusted_connection(self):
        connection_string = SqlServerManager(self.settings()).connection_string()
        self.assertIn("SERVER={KH08\\SQLEXPRESS}", connection_string)
        self.assertIn("Trusted_Connection=yes", connection_string)
        self.assertNotIn("PWD=", connection_string)

    def test_https_is_required(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(ENV.replace("https://", "http://"), encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                Settings.load(path)


if __name__ == "__main__":
    unittest.main()
