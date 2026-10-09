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

    def test_claim_workspace_job_atomic_and_completed(self):
        from services.scheduler_service import claim_workspace_job, complete_workspace_job
        from database.queries import get_or_create_workspace
        ws = get_or_create_workspace(chat_id=-991188, title="Test WS Atomic 1")
        ws_id = ws.id
        job_date = "2026-10-09"
        self.assertTrue(claim_workspace_job(ws_id, "digest", job_date))
        # Second immediate claim fails while running lease is active
        self.assertFalse(claim_workspace_job(ws_id, "digest", job_date))
        complete_workspace_job(ws_id, "digest", job_date, "completed")
        # Cannot claim already completed job
        self.assertFalse(claim_workspace_job(ws_id, "digest", job_date))

    def test_claim_workspace_job_expired_lease(self):
        from services.scheduler_service import claim_workspace_job
        from database.queries import get_or_create_workspace
        ws = get_or_create_workspace(chat_id=-991189, title="Test WS Atomic 2")
        ws_id = ws.id
        job_date = "2026-10-09"
        # Claim with 0-second lease so it immediately expires
        self.assertTrue(claim_workspace_job(ws_id, "digest", job_date, lease_timeout_seconds=0))
        # Should be reclaimable
        self.assertTrue(claim_workspace_job(ws_id, "digest", job_date, lease_timeout_seconds=0))

if __name__ == '__main__':
    unittest.main()

