import unittest
import os
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from database.db import setup_database, get_db_connection
from database.queries import (
    insert_transaction, delete_transaction, get_all_transactions,
    get_transaction_by_id, get_transaction_by_uid, get_balance_setting,
    get_default_workspace_id
)
from database.models import Transaction
from services.backup_service import export_database_to_json, import_database_from_json, BACKUP_JSON_PATH
from config import DB_PATH


class TestBackupV2AndSoftDelete(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.temp_db_path = Path(self.tmp_dir.name) / "test_backup.sqlite3"
        self.patchers = [
            patch("config.DB_PATH", self.temp_db_path),
            patch("database.db.DB_PATH", self.temp_db_path),
            patch("services.backup_service.DB_PATH", self.temp_db_path),
        ]
        for p in self.patchers:
            p.start()
        setup_database()
        
        # Mark initialized and seed one base transaction so format and upsert tests have real data
        with get_db_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('database_initialized', '1')")
            conn.commit()

        t0 = Transaction()
        t0.amount = 100.0
        t0.transaction_type = "SENT"
        t0.person_name = "Initial Store"
        t0.category = "Food"
        t0.transaction_date = "2026-09-20"
        t0.reference_number = "INIT_REF_001"
        self.init_tx_id = insert_transaction(t0)

    def tearDown(self):
        for p in reversed(self.patchers):
            p.stop()
        self.tmp_dir.cleanup()

    def test_export_v2_format_and_fields(self):
        test_path = Path(self.tmp_dir.name) / "test_v2_backup.json"
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

    def test_zero_data_loss_empty_db_guard(self):
        test_path = Path(self.tmp_dir.name) / "test_empty_guard.json"
        test_path.write_text("pre-existing cloud data", encoding="utf-8")
        
        # Wipe transactions and remove initialized flag to simulate uninitialized empty DB
        with get_db_connection() as conn:
            conn.execute("DELETE FROM transactions")
            conn.execute("DELETE FROM settings WHERE key = 'database_initialized'")
            conn.commit()

        res = export_database_to_json(output_path=test_path)
        self.assertEqual(res, {})
        # Must refuse export and preserve existing file content without overwriting
        self.assertEqual(test_path.read_text(encoding="utf-8"), "pre-existing cloud data")

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
        delete_transaction(tx1_id, workspace_id=get_default_workspace_id())

        # After soft-deleting t1, inserting same reference should now SUCCEED
        tx3_id = insert_transaction(t2)
        self.assertIsNotNone(tx3_id)

        # Clean up tx3
        delete_transaction(tx3_id, workspace_id=get_default_workspace_id())

    def test_idempotent_upsert_restore(self):
        # Fetch current live transactions
        txs = get_all_transactions()
        self.assertTrue(len(txs) > 0)

        # Re-importing the same backup should NOT duplicate rows
        test_path = Path(self.tmp_dir.name) / "test_upsert_run.json"
        export_database_to_json(output_path=test_path)
        res = import_database_from_json(input_path=test_path)
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("inserted"), 0) # 0 new inserted, all updated/preserved
        
        # Count must be exactly the same
        txs_after = get_all_transactions()
        self.assertEqual(len(txs_after), len(txs))


if __name__ == '__main__':
    unittest.main()
