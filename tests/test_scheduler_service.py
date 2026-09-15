import unittest
from services.scheduler_service import format_daily_digest, get_current_ist_time
from database.db import setup_database

class TestSchedulerService(unittest.TestCase):
    def setUp(self):
        setup_database()

    def test_current_ist_time(self):
        t = get_current_ist_time()
        self.assertIsNotNone(t.tzinfo)

    def test_format_daily_digest(self):
        digest = format_daily_digest("2026-09-04")
        self.assertIn("DAILY FINANCIAL DIGEST", digest)
        self.assertIn("Money Received", digest)
        self.assertIn("Money Spent", digest)
        self.assertIn("Closing Balance", digest)

    def test_format_daily_digest_empty_day(self):
        digest = format_daily_digest("2020-01-01")
        self.assertIn("DAILY FINANCIAL DIGEST", digest)
        self.assertIn("No transactions recorded on this day", digest)

if __name__ == '__main__':
    unittest.main()
