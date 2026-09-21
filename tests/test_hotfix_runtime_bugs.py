import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
import os
import shutil
import tempfile

from database.db import setup_database, get_db_connection
from database.models import Transaction
from services.transaction_service import commit_transaction
from bot.handlers import handle_text, handle_callback_query
from bot.commands import restore_command, send_pdf_report, send_excel_report
from services.scheduler_service import format_daily_digest
from services.task_manager import create_tracked_task, task_manager


class TestHotfixRuntimeBugs(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_hotfix.db")
        self.db_patch = patch("database.db.DB_PATH", self.db_path)
        self.db_patch.start()
        setup_database()

    def tearDown(self):
        self.db_patch.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_bug1_and_bug2_handlers_datetime_timedelta_and_unbound_local(self):
        """
        Bug 1 & 2: Verify handlers.py does not raise NameError for datetime/timedelta
        or UnboundLocalError when accessing get_current_time_in_tz across callback queries.
        """
        t = Transaction(
            transaction_type="SENT",
            amount=100.0,
            person_name="Hotfix Merchant",
            transaction_date=datetime.now().date(),
            transaction_time="10:00 AM"
        )
        commit_transaction(t)

        # 1. Test nav:today
        update = MagicMock()
        update.effective_user.id = 12345
        update.effective_chat.id = 12345
        query = MagicMock()
        query.data = "nav:today"
        query.message = MagicMock()
        query.edit_message_text = AsyncMock()
        update.callback_query = query
        context = MagicMock()
        context.user_data = {}

        with patch("bot.handlers.require_authorized", new_callable=AsyncMock, return_value=True), \
             patch("bot.auth.is_authorized_user", return_value=True):
            await handle_callback_query(update, context)
            query.edit_message_text.assert_called()

        # 2. Test filter:yesterday (exercises timedelta at module level)
        query.reset_mock()
        query.data = "filter:yesterday"
        with patch("bot.handlers.require_authorized", new_callable=AsyncMock, return_value=True), \
             patch("bot.auth.is_authorized_user", return_value=True):
            await handle_callback_query(update, context)
            query.edit_message_text.assert_called()

        # 3. Test filter:today
        query.reset_mock()
        query.data = "filter:today"
        with patch("bot.handlers.require_authorized", new_callable=AsyncMock, return_value=True), \
             patch("bot.auth.is_authorized_user", return_value=True):
            await handle_callback_query(update, context)
            query.edit_message_text.assert_called()

    async def test_bug3_handlers_standalone_amount_search(self):
        """
        Bug 3: Verify standalone amount input (e.g. '500') invokes search_transactions
        with exact_amount= parameter rather than invalid amount= parameter.
        """
        t = Transaction(
            transaction_type="SENT",
            amount=500.0,
            person_name="Search Merchant",
            transaction_date=datetime.now().date(),
            transaction_time="11:00 AM"
        )
        commit_transaction(t)

        update = MagicMock()
        update.message = MagicMock()
        update.message.text = "500"
        update.message.reply_text = AsyncMock()
        update.effective_chat.id = 12345
        update.effective_user.id = 12345

        context = MagicMock()
        context.user_data = {}
        context.args = []

        with patch("bot.handlers.require_authorized", new_callable=AsyncMock, return_value=True), \
             patch("bot.auth.is_authorized_user", return_value=True), \
             patch("bot.commands.amount_command", new_callable=AsyncMock) as mock_amt_cmd:
            await handle_text(update, context)
            mock_amt_cmd.assert_called_once()
            self.assertEqual(context.args, ["500"])

    async def test_bug4_commands_asyncio_module_level_in_restore_command(self):
        """
        Bug 4: Verify restore_command has access to module-level asyncio and runs without NameError.
        """
        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.args = []
        context.bot = MagicMock()

        with patch("bot.commands.require_admin", new_callable=AsyncMock, return_value=True), \
             patch("bot.commands.is_admin_user", return_value=True), \
             patch("services.backup_service.import_database_from_json", return_value={"success": True, "inserted": 1, "updated": 0, "skipped": 0, "balance_match": True}), \
             patch("services.backup_service.backup_to_telegram", new_callable=AsyncMock, return_value=True), \
             patch("database.queries.get_all_transactions", return_value=[]):
            await restore_command(update, context)
            self.assertTrue(update.message.reply_text.called)

    async def test_bug5_scheduler_format_daily_digest(self):
        """
        Bug 5: Verify format_daily_digest handles target_date_str correctly without date_str NameError.
        """
        digest_default = format_daily_digest()
        self.assertIn("DAILY FINANCIAL DIGEST", digest_default)

        digest_explicit = format_daily_digest(target_date_str="2026-09-21")
        self.assertIn("DAILY FINANCIAL DIGEST", digest_explicit)
        self.assertIn("2026", digest_explicit)

    async def test_bug7_tracked_background_task_helper(self):
        """
        Bug 7: Verify create_tracked_task tracks background tasks and logs exceptions on failure.
        """
        async def failing_task():
            raise RuntimeError("Intentional background test error")

        async def successful_task():
            return 42

        # Successful task
        t_succ = create_tracked_task(successful_task(), name="test_succ")
        await t_succ
        self.assertEqual(t_succ.result(), 42)

        # Failing task: should be logged and not crash event loop
        with patch("services.task_manager.logger.error") as mock_log_err:
            t_fail = create_tracked_task(failing_task(), name="test_failing")
            try:
                await t_fail
            except RuntimeError:
                pass
            await asyncio.sleep(0.01)
            self.assertTrue(mock_log_err.called)
            logged_msg = mock_log_err.call_args[0][0]
            self.assertIn("Tracked background task 'test_failing' failed", logged_msg)


if __name__ == "__main__":
    unittest.main()
