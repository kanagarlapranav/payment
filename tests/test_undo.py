import unittest
from services.undo_service import record_delete_action, record_edit_action, perform_undo, _UNDO_STACK
from database.db import setup_database, get_db_connection
from database.queries import insert_transaction, get_transaction_by_id, delete_transaction
from database.models import Transaction

import os
from config import DB_PATH
from services.backup_service import BACKUP_JSON_PATH

class TestUndoService(unittest.TestCase):
    def setUp(self):
        setup_database()
        _UNDO_STACK.clear()

    def test_undo_delete(self):
        t = Transaction()
        t.transaction_type = "SENT"
        t.amount = 450.0
        t.person_name = "Test Person"
        t.transaction_date = "2026-09-18"
        t.balance_before = 1000.0
        t.balance_after = 550.0
        tx_id = insert_transaction(t)
        
        tx = get_transaction_by_id(tx_id)
        self.assertIsNotNone(tx)
        
        record_delete_action(tx)
        delete_transaction(tx_id)
        
        # Verify it's deleted
        self.assertIsNone(get_transaction_by_id(tx_id))
        
        # Perform undo
        success, msg = perform_undo()
        self.assertTrue(success)
        self.assertIn("Undo Successful", msg)
        
        # Verify transaction is restored with the same ID and permanent UID
        restored_tx = get_transaction_by_id(tx_id)
        self.assertIsNotNone(restored_tx)
        self.assertEqual(restored_tx['uid'], tx['uid'])
        self.assertIsNone(restored_tx['deleted_at'])

    def test_undo_empty(self):
        _UNDO_STACK.clear()
        success, msg = perform_undo()
        self.assertFalse(success)
        self.assertIn("No recent action", msg)

if __name__ == '__main__':
    unittest.main()
