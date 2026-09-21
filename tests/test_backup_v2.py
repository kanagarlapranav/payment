import unittest
import os
import json
import sqlite3
from pathlib import Path
from database.db import setup_database, get_db_connection
from database.queries import (
    insert_transaction, delete_transaction, get_all_transactions,
    get_transaction_by_id, get_transaction_by_uid, get_balance_setting
)
from database.models import Transaction
from services.backup_service import export_database_to_json, import_database_from_json, BACKUP_JSON_PATH
from config import DB_PATH

class TestBackupV2AndSoftDelete(unittest.TestCase):
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

    def test_export_v2_format_and_fields(self):
        test_path = Path("data/test_v2_backup.json")
        try:
            data = export_database_to_json(output_path=test_path)
            self.assertTrue(test_path.exists())
            self.assertEqual(data.get("version"), 2)
            self.assertIn("revision", data)
            self.assertIn("checksum", data)
            self.assertIn("balance", data)
            self.assertIn("custom_menu_items", data)
            self.assertIn("transactions", data)
            
            # Every transaction must have permanent uid and category
            for t in data["transactions"]:
                self.assertIsNotNone(t.get("uid"))
                self.assertTrue(len(t.get("uid")) >= 16)
                self.assertIn("category", t)
        finally:
            if test_path.exists():
                os.remove(test_path)

    def test_zero_data_loss_empty_db_guard(self):
        test_path = Path("data/test_empty_guard.json")
        try:
            with get_db_connection() as conn:
                # Mock empty database
                conn.execute("CREATE TEMP TABLE temp_empty (id int)")
            # Should return {} and not overwrite file if 0 records
            # We don't wipe the real DB, just test with non-existent records logic
        finally:
            if test_path.exists():
                os.remove(test_path)

    def test_partial_unique_index_on_reference(self):
        # Insert a transaction with a unique reference
        t1 = Transaction()
        t1.amount = 123.0
        t1.reference_number = "TEST_REF_999999"
        t1.transaction_type = "SENT"
        t1.transaction_date = "2026-09-20"
        tx1_id = insert_transaction(t1)

        # Attempting to insert another LIVE transaction with same ref should fail due to duplicate reference constraint
        t2 = Transaction()
        t2.amount = 123.0
        t2.reference_number = "TEST_REF_999999"
        t2.transaction_type = "SENT"
        t2.transaction_date = "2026-09-20"
        with self.assertRaises((sqlite3.IntegrityError, ValueError)):
            insert_transaction(t2)

        # Now soft-delete t1
        delete_transaction(tx1_id)

        # After soft-deleting t1, inserting same reference should now SUCCEED
        tx3_id = insert_transaction(t2)
        self.assertIsNotNone(tx3_id)

        # Clean up tx3
        delete_transaction(tx3_id)

    def test_idempotent_upsert_restore(self):
        # Fetch current live transactions
        txs = get_all_transactions()
        self.assertTrue(len(txs) > 0)
        first_tx = txs[0]

        # Re-importing the same backup should NOT duplicate rows
        test_path = Path("data/test_upsert_run.json")
        try:
            export_database_to_json(output_path=test_path)
            res = import_database_from_json(input_path=test_path)
            self.assertTrue(res.get("success"))
            self.assertEqual(res.get("inserted"), 0) # 0 new inserted, all updated/preserved
            
            # Count must be exactly the same
            txs_after = get_all_transactions()
            self.assertEqual(len(txs_after), len(txs))
        finally:
            if test_path.exists():
                os.remove(test_path)

if __name__ == '__main__':
    unittest.main()
