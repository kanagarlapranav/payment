import unittest
import os
import sqlite3
from database.db import get_db_connection, setup_database
from database.queries import (
    insert_transaction, delete_transaction, update_transaction,
    get_all_transactions_asc, get_transaction_by_id, get_balance_setting
)
from database.models import Transaction
from services.balance_service import resequence_transaction_ids, recalculate_all_balances
from bot.handlers import format_success_message
from config import DB_PATH

from services.backup_service import BACKUP_JSON_PATH

import tempfile
from pathlib import Path
from unittest.mock import patch

class TestResequenceAndBalance(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.test_db_path = Path(self.temp_dir.name) / "test_resequence.sqlite3"
        self.db_patcher = patch("database.db.DB_PATH", self.test_db_path)
        self.cfg_patcher = patch("config.DB_PATH", self.test_db_path)
        self.db_patcher.start()
        self.cfg_patcher.start()

        setup_database()
        with sqlite3.connect(self.test_db_path) as conn:
            conn.execute("DELETE FROM transactions")
            conn.execute("DELETE FROM settings")
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('initial_balance', '0.0')")
            # Insert 5 self-contained test transactions
            for i in range(1, 6):
                conn.execute("""
                    INSERT INTO transactions (
                        id, transaction_type, amount, person_name, transaction_date,
                        balance_before, balance_after, category
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (i, 'RECEIVED' if i % 2 == 1 else 'SENT', float(i * 1000), f"Person {i}", "2026-09-01", 0.0, float(i * 1000), "General"))
            conn.commit()
        resequence_transaction_ids()
        recalculate_all_balances()

    def tearDown(self):
        self.db_patcher.stop()
        self.cfg_patcher.stop()
        self.temp_dir.cleanup()

    def test_resequence_on_deletion(self):
        txs = get_all_transactions_asc()
        initial_count = len(txs)
        self.assertEqual(initial_count, 5)
        
        # Verify initial IDs are strictly 1..5
        ids = [t['id'] for t in txs]
        self.assertEqual(ids, [1, 2, 3, 4, 5])
        
        # Delete transaction #2
        deleted = delete_transaction(2)
        self.assertTrue(deleted)
        
        # After deletion, live count is 4 and ID #2 is missing with gap preserved: [1, 3, 4, 5]
        txs_after = get_all_transactions_asc()
        self.assertEqual(len(txs_after), 4)
        ids_after = [t['id'] for t in txs_after]
        self.assertEqual(ids_after, [1, 3, 4, 5])
        
        # Verify row #2 still exists in the database as a tombstone
        with get_db_connection() as conn:
            row2 = conn.execute("SELECT * FROM transactions WHERE id = 2").fetchone()
            self.assertIsNotNone(row2)
            self.assertIsNotNone(row2['deleted_at'])
        
        # Balances should remain consistent
        running = 0.0
        for t in txs_after:
            self.assertAlmostEqual(t['balance_before'], running)
            if t['transaction_type'] == 'SENT':
                running -= t['amount']
            elif t['transaction_type'] == 'RECEIVED':
                running += t['amount']
            self.assertAlmostEqual(t['balance_after'], running)
            
        self.assertAlmostEqual(get_balance_setting(), running)

    def test_balance_recalculation_on_amount_edit(self):
        txs = get_all_transactions_asc()
        self.assertTrue(len(txs) > 0)
        first_tx = txs[0]
        old_amt = first_tx['amount']
        new_amt = old_amt + 1000.0
        
        # Update amount
        update_transaction(first_tx['id'], {'amount': new_amt})
        new_bal = recalculate_all_balances()
        
        updated_first = get_transaction_by_id(first_tx['id'])
        self.assertEqual(updated_first['amount'], new_amt)
        self.assertEqual(updated_first['balance_after'], updated_first['balance_before'] + new_amt if updated_first['transaction_type'] == 'RECEIVED' else updated_first['balance_before'] - new_amt)

    def test_format_success_message(self):
        t = Transaction(
            transaction_type='SENT',
            amount=5000.0,
            person_name='Balaji',
            transaction_date='2026-09-05',
            transaction_time='10:02 AM',
            reference_number='661385614715',
            balance_before=36900.0,
            balance_after=31900.0
        )
        msg = format_success_message(t)
        self.assertIn("Payment Sent", msg)
        self.assertIn("Balaji", msg)
        self.assertIn("5,000", msg)
        self.assertIn("36,900", msg)
        self.assertIn("31,900", msg)

if __name__ == '__main__':
    unittest.main()
