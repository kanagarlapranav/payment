import unittest
from services.undo_service import record_delete_action, record_edit_action, perform_undo, _UNDO_STACK
from database.db import setup_database, get_db_connection
from database.queries import insert_transaction, get_transaction_by_id, delete_transaction, get_default_workspace_id
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
        ws_id = get_default_workspace_id()
        
        tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
        self.assertIsNotNone(tx)
        
        record_delete_action(tx)
        delete_transaction(tx_id, workspace_id=ws_id)
        
        # Verify it's deleted
        self.assertIsNone(get_transaction_by_id(tx_id, workspace_id=ws_id))
        
        # Perform undo
        success, msg = perform_undo(workspace_id=ws_id)
        self.assertTrue(success)
        self.assertIn("Undo Successful", msg)
        
        # Verify transaction is restored with the same ID and permanent UID
        restored_tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
        self.assertIsNotNone(restored_tx)
        self.assertEqual(restored_tx['uid'], tx['uid'])
        self.assertIsNone(restored_tx['deleted_at'])

    def test_undo_empty(self):
        _UNDO_STACK.clear()
        ws_id = get_default_workspace_id()
        success, msg = perform_undo(workspace_id=ws_id)
        self.assertFalse(success)
        self.assertIn("No recent action", msg)

    def test_undo_insert_null_workspace_record(self):
        from services.undo_service import record_insert_action
        from database.queries import get_transaction_by_uid, get_default_workspace_id
        t = Transaction()
        t.transaction_type = "SENT"
        t.amount = 200.0
        t.person_name = "Legacy Store"
        t.transaction_date = "2026-10-09"
        t.workspace_id = None
        tx_id = insert_transaction(t)
        with get_db_connection() as conn:
            conn.cursor().execute("UPDATE transactions SET workspace_id = NULL WHERE id = ?", (tx_id,))
            conn.commit()

        tx = get_transaction_by_id(tx_id, workspace_id=get_default_workspace_id())
        self.assertIsNotNone(tx)
        uid = tx['uid']

        # Undo with empty workspace_id must raise ValueError instead of silently defaulting
        with self.assertRaises(ValueError):
            perform_undo(chat_id=123, user_id=456, workspace_id="")

    def test_undo_insert_non_default_workspace(self):
        from services.undo_service import record_insert_action
        from database.queries import get_or_create_workspace, get_transaction_by_uid
        ws = get_or_create_workspace(chat_id=-100999888, chat_type="group", title="Tenant WS")
        t = Transaction()
        t.transaction_type = "SENT"
        t.amount = 350.0
        t.person_name = "Tenant Cafe"
        t.transaction_date = "2026-10-09"
        t.workspace_id = ws.id
        tx_id = insert_transaction(t)
        tx = get_transaction_by_id(tx_id, workspace_id=ws.id)
        uid = tx['uid']

        record_insert_action(uid, chat_id=-100999888, user_id=777, workspace_id=ws.id)
        success, msg = perform_undo(chat_id=-100999888, user_id=777, workspace_id=ws.id)
        self.assertTrue(success, f"Undo failed: {msg}")
        self.assertIn("Removed newly added transaction", msg)

        tx_check = get_transaction_by_uid(uid, workspace_id=ws.id)
        self.assertIsNotNone(tx_check['deleted_at'])

if __name__ == '__main__':
    unittest.main()
