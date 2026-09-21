import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from database.db import get_db_connection, setup_database
from database.models import Transaction
from database.queries import (
    delete_transaction,
    find_potential_duplicate,
    get_all_transactions,
    get_all_transactions_asc,
    get_balance_setting,
    get_category_summary,
    get_contact_ledger,
    get_daily_spend_series,
    get_daily_summary_stats,
    get_live_transaction_by_uid,
    get_monthly_spending,
    get_monthly_summary,
    get_recent_transactions,
    get_top_payees,
    get_transaction_by_id,
    get_transaction_by_uid,
    get_transactions_by_date,
    get_transactions_paginated,
    insert_transaction_with_balance,
    restore_soft_deleted_transaction,
    search_transactions,
)
from services.backup_service import record_confirmed_backup
from services.balance_service import set_explicit_balance
from services.maintenance_service import purge_eligible_tombstones
from services.undo_service import (
    perform_undo,
    record_delete_action,
)
from utils.dates import utc_now_iso


class TestSoftDeleteAndUndo(unittest.TestCase):
    """
    Comprehensive test suite for Prompt 4:
    Soft delete, tombstones preservation, scoped & persistent undo in SQLite,
    and conditional maintenance purge.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_db_path = Path(self.temp_dir.name) / "test_soft_delete.sqlite3"
        self.db_patcher = patch("database.db.DB_PATH", self.test_db_path)
        self.cfg_patcher = patch("config.DB_PATH", self.test_db_path)
        self.db_patcher.start()
        self.cfg_patcher.start()

        setup_database()

    def tearDown(self):
        self.db_patcher.stop()
        self.cfg_patcher.stop()
        self.temp_dir.cleanup()

    def _create_sample_tx(self, amount: float = 250.0, tx_type: str = "SENT", person: str = "Alice", ref: str = "REF1001") -> dict:
        t = Transaction(
            transaction_type=tx_type,
            amount=amount,
            person_name=person,
            sender_name="Self",
            recipient_name=person,
            transaction_date="2026-09-21",
            transaction_time="10:00 AM",
            reference_number=ref,
            category="Food & Dining",
        )
        tx_id = insert_transaction_with_balance(t)
        return get_transaction_by_id(tx_id)

    def test_deleted_rows_are_absent_from_every_live_query(self):
        tx = self._create_sample_tx(amount=150.0, ref="REF_LIVE_QUERY_TEST")
        tx_id = tx['id']
        tx_uid = tx['uid']

        # Verify present before deletion
        self.assertIsNotNone(get_transaction_by_id(tx_id))
        self.assertIsNotNone(get_live_transaction_by_uid(tx_uid))

        # Perform soft delete
        deleted = delete_transaction(tx_id)
        self.assertTrue(deleted)

        # 1. get_transaction_by_id
        self.assertIsNone(get_transaction_by_id(tx_id))

        # 2. get_live_transaction_by_uid
        self.assertIsNone(get_live_transaction_by_uid(tx_uid))

        # 3. get_all_transactions
        all_txs = get_all_transactions()
        self.assertFalse(any(t['id'] == tx_id for t in all_txs))

        # 4. get_all_transactions_asc
        all_asc = get_all_transactions_asc()
        self.assertFalse(any(t['id'] == tx_id for t in all_asc))

        # 5. get_recent_transactions
        recent = get_recent_transactions(limit=20)
        self.assertFalse(any(t['id'] == tx_id for t in recent))

        # 6. get_transactions_by_date
        by_date = get_transactions_by_date("2026-09-21")
        self.assertFalse(any(t['id'] == tx_id for t in by_date))

        # 7. search_transactions
        search_res = search_transactions(query_text="Alice")
        self.assertFalse(any(t['id'] == tx_id for t in search_res))

        # 8. get_monthly_summary
        monthly = get_monthly_summary(2026, 9)
        self.assertEqual(monthly['tx_count'], 0)
        self.assertEqual(monthly['total_sent'], 0.0)

        # 9. get_monthly_spending
        self.assertEqual(get_monthly_spending(2026, 9), 0.0)

        # 10. get_category_summary
        cat_summary = get_category_summary(2026, 9)
        self.assertEqual(cat_summary, [])

        # 11. get_daily_summary_stats
        daily_stats = get_daily_summary_stats("2026-09-21")
        self.assertEqual(daily_stats['tx_count'], 0)
        self.assertEqual(daily_stats['transactions'], [])

        # 12. get_top_payees
        top_payees = get_top_payees(limit=5, year=2026, month=9)
        self.assertEqual(top_payees, [])

        # 13. get_daily_spend_series
        series = get_daily_spend_series(2026, 9)
        self.assertEqual(series, [])

        # 14. get_transactions_paginated
        paginated = get_transactions_paginated(year=2026, month=9)
        self.assertEqual(paginated['total_count'], 0)
        self.assertEqual(paginated['transactions'], [])

        # 15. get_contact_ledger
        contacts = get_contact_ledger()
        self.assertEqual(contacts, [])

        # 16. find_potential_duplicate
        dup = find_potential_duplicate(amount=150.0, reference_number="REF_LIVE_QUERY_TEST")
        self.assertIsNone(dup)

    def test_tombstone_is_preserved(self):
        tx = self._create_sample_tx(amount=300.0, ref="REF_TOMBSTONE_1")
        tx_id = tx['id']
        tx_uid = tx['uid']

        delete_transaction(tx_id)

        # Row must still exist physically in SQLite table
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,))
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row['uid'], tx_uid)
            self.assertIsNotNone(row['deleted_at'])
            self.assertTrue(len(row['deleted_at']) > 0)
            self.assertIsNotNone(row['updated_at'])

        # get_transaction_by_uid without live_only finds the tombstone
        tombstone = get_transaction_by_uid(tx_uid, live_only=False)
        self.assertIsNotNone(tombstone)
        self.assertIsNotNone(tombstone['deleted_at'])

    def test_undo_works_exactly_once(self):
        tx = self._create_sample_tx(amount=100.0, ref="REF_ONCE_1")
        chat_id = 9991
        user_id = 8881

        record_delete_action(tx, chat_id=chat_id, user_id=user_id)
        delete_transaction(tx['id'])

        # First undo succeeds
        success1, msg1 = perform_undo(chat_id=chat_id, user_id=user_id)
        self.assertTrue(success1)
        self.assertIn("Undo Successful", msg1)

        # Transaction is live again
        self.assertIsNotNone(get_transaction_by_id(tx['id']))

        # Second undo on same scope fails because record was already consumed
        success2, msg2 = perform_undo(chat_id=chat_id, user_id=user_id)
        self.assertFalse(success2)
        self.assertIn("No recent action found to undo", msg2)

    def test_undo_cannot_restore_live_row_or_another_uid(self):
        tx = self._create_sample_tx(amount=120.0, ref="REF_LIVE_RESTORE")
        live_uid = tx['uid']

        # 1. Attempt direct restore on an active, non-deleted row
        direct_restored = restore_soft_deleted_transaction(uid=live_uid)
        self.assertFalse(direct_restored)

        # 2. Attempt restore on nonexistent random UID
        fake_uid = "99999999999999999999999999999999"
        fake_restored = restore_soft_deleted_transaction(uid=fake_uid)
        self.assertFalse(fake_restored)

        # 3. Via undo log: record action pointing to a live row
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO undo_log (chat_id, user_id, action, uid, created_at) VALUES (1, 1, 'delete', ?, ?)",
                (live_uid, utc_now_iso()),
            )
            conn.commit()

        success, msg = perform_undo(chat_id=1, user_id=1)
        self.assertFalse(success)
        self.assertIn("Cannot restore transaction", msg)

    def test_another_user_cannot_undo(self):
        tx = self._create_sample_tx(amount=200.0, ref="REF_USER_ISOLATION")
        chat_id = 555
        owner_id = 111
        other_user_id = 222

        record_delete_action(tx, chat_id=chat_id, user_id=owner_id)
        delete_transaction(tx['id'])

        # Other user tries to undo -> refused
        success_other, msg_other = perform_undo(chat_id=chat_id, user_id=other_user_id)
        self.assertFalse(success_other)
        self.assertIn("No recent action found to undo", msg_other)

        # Transaction remains deleted
        self.assertIsNone(get_transaction_by_id(tx['id']))

        # Legitimate owner undoes -> succeeds
        success_owner, msg_owner = perform_undo(chat_id=chat_id, user_id=owner_id)
        self.assertTrue(success_owner)
        self.assertIn("Undo Successful", msg_owner)
        self.assertIsNotNone(get_transaction_by_id(tx['id']))

    def test_expired_undo_is_refused(self):
        tx = self._create_sample_tx(amount=80.0, ref="REF_EXPIRE")
        chat_id = 777
        user_id = 888

        # Simulate undo recorded 15 minutes ago
        past_time = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO undo_log (chat_id, user_id, action, uid, created_at) VALUES (?, ?, 'delete', ?, ?)",
                (chat_id, user_id, tx['uid'], past_time),
            )
            conn.commit()

        delete_transaction(tx['id'])

        success, msg = perform_undo(chat_id=chat_id, user_id=user_id)
        self.assertFalse(success)
        self.assertIn("expired", msg.lower())

        # Row remains deleted
        self.assertIsNone(get_transaction_by_id(tx['id']))

    def test_restart_keeps_undo_working(self):
        tx = self._create_sample_tx(amount=50.0, ref="REF_PERSIST")
        chat_id = 1234
        user_id = 5678

        record_delete_action(tx, chat_id=chat_id, user_id=user_id)
        delete_transaction(tx['id'])

        # Simulate container or bot process restart: verify entry is in SQLite table
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM undo_log WHERE uid = ?", (tx['uid'],))
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row['chat_id'], chat_id)
            self.assertEqual(row['user_id'], user_id)
            self.assertIsNone(row['used_at'])

        # Re-invoke undo as fresh caller: still succeeds
        success, _ = perform_undo(chat_id=chat_id, user_id=user_id)
        self.assertTrue(success)
        self.assertIsNotNone(get_transaction_by_id(tx['id']))

    def test_balance_is_right_after_delete_and_undo(self):
        set_explicit_balance(1000.00)
        self.assertEqual(get_balance_setting(), 1000.00)

        # Insert SENT 250.00 -> balance becomes 750.00
        tx = self._create_sample_tx(amount=250.00, tx_type="SENT", ref="REF_BAL_CHECK")
        self.assertEqual(get_balance_setting(), 750.00)

        # Delete transaction -> balance recalculates to 1000.00
        record_delete_action(tx, chat_id=1, user_id=1)
        delete_transaction(tx['id'])
        self.assertEqual(get_balance_setting(), 1000.00)

        # Undo delete -> balance recalculates back to 750.00
        success, _ = perform_undo(chat_id=1, user_id=1)
        self.assertTrue(success)
        self.assertEqual(get_balance_setting(), 750.00)

    def test_maintenance_tombstone_purge_conditions(self):
        now = datetime.now(timezone.utc)
        recent_del = (now - timedelta(days=30)).isoformat()
        ancient_del_1 = (now - timedelta(days=400)).isoformat()
        ancient_del_2 = (now - timedelta(days=500)).isoformat()

        # Insert 3 transactions directly as tombstones with varying ages
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO transactions (id, transaction_type, amount, balance_before, balance_after, uid, deleted_at)
                VALUES (101, 'SENT', 10.0, 0.0, 0.0, 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', ?)
                """,
                (recent_del,),
            )
            conn.execute(
                """
                INSERT INTO transactions (id, transaction_type, amount, balance_before, balance_after, uid, deleted_at)
                VALUES (102, 'SENT', 20.0, 0.0, 0.0, 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', ?)
                """,
                (ancient_del_1,),
            )
            conn.execute(
                """
                INSERT INTO transactions (id, transaction_type, amount, balance_before, balance_after, uid, deleted_at)
                VALUES (103, 'SENT', 30.0, 0.0, 0.0, 'cccccccccccccccccccccccccccccccc', ?)
                """,
                (ancient_del_2,),
            )
            conn.commit()

        # Condition 1: No confirmed backup upload on record -> purge skipped
        purged = purge_eligible_tombstones(db_path=self.test_db_path)
        self.assertEqual(purged, 0)

        # Condition 2: Confirmed backup is older than ancient_del_1 (e.g. 450 days ago)
        # Only ancient_del_2 (500 days ago) is older than both 365 days AND backup (450 days ago)
        backup_ts_450 = (now - timedelta(days=450)).isoformat()
        record_confirmed_backup(backup_ts_450)

        purged = purge_eligible_tombstones(db_path=self.test_db_path)
        self.assertEqual(purged, 1)  # Only row 103 (500 days) purged

        # Row 101 (recent, 30 days) and Row 102 (400 days, newer than backup) must remain
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM transactions WHERE id IN (101, 102, 103)")
            remaining_ids = [r[0] for r in cur.fetchall()]
            self.assertIn(101, remaining_ids)
            self.assertIn(102, remaining_ids)
            self.assertNotIn(103, remaining_ids)

        # Condition 3: Now backup uploaded today (newer than ancient_del_1)
        record_confirmed_backup(now.isoformat())
        purged = purge_eligible_tombstones(db_path=self.test_db_path)
        self.assertEqual(purged, 1)  # Row 102 is now purged

        # Row 101 (recent 30 days) is STILL preserved because it is younger than 365 days
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM transactions WHERE id = 101")
            self.assertIsNotNone(cur.fetchone())


if __name__ == "__main__":
    unittest.main()
