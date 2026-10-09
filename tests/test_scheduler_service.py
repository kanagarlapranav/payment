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

    def test_check_missed_daily_digest_catchup(self):
        import asyncio
        from unittest.mock import AsyncMock, patch, MagicMock
        from services.scheduler_service import check_missed_daily_digest

        mock_context = MagicMock()
        mock_context.bot = AsyncMock()

        with patch("services.scheduler_service.daily_digest_job", new_callable=AsyncMock) as mock_digest:
            asyncio.run(check_missed_daily_digest(mock_context))
            mock_digest.assert_called_once()
            self.assertIn("target_date_str", mock_digest.call_args.kwargs)

    def test_backup_retry_job_backoff_and_give_up_alert(self):
        import asyncio
        from unittest.mock import AsyncMock, patch, MagicMock
        import services.scheduler_service as sched_mod
        from database.db import get_db_connection

        # Set is_dirty = 1
        with get_db_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('is_dirty', '1', '2026-10-09T00:00:00Z')")
            conn.commit()

        mock_context = MagicMock()
        mock_context.bot = AsyncMock()

        sched_mod._BACKUP_RETRY_FAILURES = 0
        sched_mod._BACKUP_RETRY_NEXT_ATTEMPT = 0.0
        sched_mod._BACKUP_RETRY_ALERTED = False

        # Mock backup_to_telegram to fail
        with patch("services.backup_service.backup_to_telegram", new_callable=AsyncMock, return_value=False), \
             patch("services.scheduler_service.TELEGRAM_USER_ID", "123456"):
            
            # Simulate 5 consecutive failures
            for i in range(5):
                sched_mod._BACKUP_RETRY_NEXT_ATTEMPT = 0.0  # bypass delay for test loop
                asyncio.run(sched_mod.backup_retry_job(mock_context))

            self.assertEqual(sched_mod._BACKUP_RETRY_FAILURES, 5)
            self.assertTrue(sched_mod._BACKUP_RETRY_ALERTED)
            # Verify owner got alert
            mock_context.bot.send_message.assert_called_once()
            call_args = mock_context.bot.send_message.call_args
            self.assertEqual(call_args.kwargs["chat_id"], "123456")
            self.assertIn("Cloud Backup Alert", call_args.kwargs["text"])

    def test_tombstone_purge_eligible_with_drive_backup(self):
        from database.db import get_db_connection
        from services.maintenance_service import purge_eligible_tombstones
        from database.models import Transaction
        from database.queries import insert_transaction

        # Insert a transaction and tombstone it 400 days ago
        t = Transaction(amount=100.0, transaction_type="SENT", person_name="Old Test")
        t_id = insert_transaction(t)

        with get_db_connection() as conn:
            # Set deleted_at to 400 days ago
            conn.execute("UPDATE transactions SET deleted_at = '2024-01-01T00:00:00Z' WHERE id = ?", (t_id,))
            # Only set last_drive_backup_at, no last_confirmed_backup_at
            conn.execute("DELETE FROM settings WHERE key = 'last_confirmed_backup_at'")
            conn.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('last_drive_backup_at', '2026-10-09T00:00:00Z', '2026-10-09T00:00:00Z')")
            conn.commit()

        purged = purge_eligible_tombstones()
        self.assertGreaterEqual(purged, 1)

if __name__ == '__main__':
    unittest.main()

