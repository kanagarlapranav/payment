import unittest
from unittest.mock import patch
from database.models import Transaction
from services.balance_service import update_balance_for_transaction

import os
from config import DB_PATH
from services.backup_service import BACKUP_JSON_PATH

class TestBalance(unittest.TestCase):
    def test_balance_calculation_pure(self):
        current_balance = 50000.0
        
        def mock_get_balance():
            return current_balance
            
        with patch('services.balance_service.get_balance_setting', side_effect=mock_get_balance):
            # Test SENT (does NOT mutate current_balance)
            t_sent = Transaction(transaction_type='SENT', amount=5000)
            t_sent = update_balance_for_transaction(t_sent)
            self.assertEqual(t_sent.balance_before, 50000.0)
            self.assertEqual(t_sent.balance_after, 45000.0)
            self.assertEqual(current_balance, 50000.0)
            
            # Test RECEIVED (does NOT mutate current_balance)
            t_recv = Transaction(transaction_type='RECEIVED', amount=3000)
            t_recv = update_balance_for_transaction(t_recv)
            self.assertEqual(t_recv.balance_before, 50000.0)
            self.assertEqual(t_recv.balance_after, 53000.0)
            self.assertEqual(current_balance, 50000.0)

    def test_backup_roundtrip(self):
        from services.backup_service import export_database_to_json, import_database_from_json
        from database.db import LEDGER_LOCK, get_db_connection
        from database.queries import insert_transaction_with_balance
        with LEDGER_LOCK, get_db_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('database_initialized', '1')")
        for i in range(4):
            t = Transaction()
            t.amount = 100.0 + i
            t.transaction_type = "SENT"
            t.person_name = f"Roundtrip Test {i}"
            t.transaction_date = "2026-09-22"
            t.transaction_time = "00:00:00"
            insert_transaction_with_balance(t)
        data = export_database_to_json()
        self.assertGreaterEqual(data.get("transaction_count", 0), 4)
        success = import_database_from_json(data_dict=data)
        self.assertTrue(success)

if __name__ == '__main__':
    unittest.main()
