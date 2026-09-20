import unittest
from services.undo_service import record_delete_action, record_edit_action, perform_undo, _UNDO_STACK
from database.db import setup_database, get_db_connection
from database.queries import insert_transaction, get_transaction_by_id, delete_transaction
from database.models import Transaction

import os
from config import DB_PATH
from services.backup_service import BACKUP_JSON_PATH

class TestUndoService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_backup = None
        cls.json_backup = None
        if os.path.exists(DB_PATH):
            with open(DB_PATH, 'rb') as f:
                cls.db_backup = f.read()
        if os.path.exists(BACKUP_JSON_PATH):
            with open(BACKUP_JSON_PATH, 'rb') as f:
                cls.json_backup = f.read()

    @classmethod
    def tearDownClass(cls):
        if cls.db_backup is not None:
            with open(DB_PATH, 'wb') as f:
                f.write(cls.db_backup)
        if cls.json_backup is not None:
            with open(BACKUP_JSON_PATH, 'wb') as f:
                f.write(cls.json_backup)

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

    def test_undo_empty(self):
        _UNDO_STACK.clear()
        success, msg = perform_undo()
        self.assertFalse(success)
        self.assertIn("No recent action", msg)

if __name__ == '__main__':
    unittest.main()
