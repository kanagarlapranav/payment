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
        from database.queries import insert_transaction_with_balance, get_default_workspace_id
        default_ws = get_default_workspace_id()
        with LEDGER_LOCK, get_db_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('database_initialized', '1')")
        for i in range(4):
            t = Transaction()
            t.amount = 100.0 + i
            t.transaction_type = "SENT"
            t.person_name = f"Roundtrip Test {i}"
            t.transaction_date = "2026-09-22"
            t.transaction_time = "00:00:00"
            t.workspace_id = default_ws
            insert_transaction_with_balance(t)
        data = export_database_to_json()
        self.assertGreaterEqual(data.get("transaction_count", 0), 4)
        success = import_database_from_json(data_dict=data)
        self.assertTrue(success)

    def test_set_explicit_balance_includes_legacy_empty_workspace_id(self):
        """set_explicit_balance correctly includes rows with legacy workspace_id = '' matching recalculate_in_connection."""
        from database.db import get_db_connection, setup_database
        from database.queries import get_default_workspace_id
        from services.balance_service import set_explicit_balance, recalculate_all_balances
        import uuid
        setup_database()
        default_ws = get_default_workspace_id()

        # Insert legacy row with empty string workspace_id
        with get_db_connection() as conn:
            conn.execute('''
                INSERT INTO transactions (
                    transaction_type, amount, person_name, transaction_date, transaction_time,
                    uid, occurred_at, deleted_at, created_at, updated_at, workspace_id, balance_before, balance_after
                ) VALUES ('SENT', 200.0, 'Legacy Blank WS', '2026-10-01', '10:00 AM', ?, '2026-10-01 10:00:00', NULL, '2026-10-01T10:00:00Z', '2026-10-01T10:00:00Z', '', 0.0, 0.0)
            ''', (uuid.uuid4().hex,))
            conn.commit()

        # Set explicit balance to 1000.0 on default workspace
        final_bal = set_explicit_balance(1000.0, workspace_id=default_ws)
        self.assertEqual(final_bal, 1000.0)

        # Recalculate all balances must yield the exact same 1000.0 (no disagreement)
        recalc_bal = recalculate_all_balances(workspace_id=default_ws)
        self.assertEqual(recalc_bal, 1000.0)


if __name__ == '__main__':
    unittest.main()

