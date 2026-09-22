import unittest
import os
import json
import uuid
from decimal import Decimal
from pathlib import Path
from datetime import datetime, timezone, timedelta

from database.db import setup_database, get_db_connection, LEDGER_LOCK
from database.models import Transaction
from database.queries import (
    insert_transaction_with_balance,
    delete_transaction,
    get_all_transactions,
    get_transaction_by_uid,
    get_balance_setting,
)
from services.backup_service import (
    export_database_to_json,
    import_database_from_json,
    preview_database_import,
    compute_canonical_checksum,
    verify_backup_payload,
    BACKUP_JSON_PATH,
)
from utils.dates import utc_now_iso, build_occurred_at
from config import DB_PATH

class TestBackupRestorePrompt6(unittest.TestCase):
    def setUp(self):
        setup_database()

    def test_newer_wins(self):
        """Incoming row with newer updated_at updates the local row."""
        uid = uuid.uuid4().hex
        t1_iso = "2026-09-20T10:00:00.000000+00:00"
        t2_iso = "2026-09-20T12:00:00.000000+00:00"

        with get_db_connection() as conn:
            conn.execute('''
                INSERT INTO transactions (
                    transaction_type, amount, person_name, transaction_date, transaction_time,
                    uid, occurred_at, deleted_at, created_at, updated_at, balance_before, balance_after
                ) VALUES ('SENT', 100.0, 'Old Person', '2026-09-20', '10:00 AM', ?, '2026-09-20 10:00:00', NULL, ?, ?, 0.0, 0.0)
            ''', (uid, t1_iso, t1_iso))
            conn.commit()

        backup_payload = {
            "version": 2,
            "revision": 10,
            "settings": {},
            "custom_menu_items": [],
            "budgets": [],
            "transactions": [{
                "uid": uid,
                "transaction_type": "SENT",
                "amount": 200.0,
                "person_name": "New Person",
                "transaction_date": "2026-09-20",
                "transaction_time": "10:00 AM",
                "occurred_at": "2026-09-20 10:00:00",
                "deleted_at": None,
                "created_at": t1_iso,
                "updated_at": t2_iso,
            }]
        }
        backup_payload["checksum"] = compute_canonical_checksum(backup_payload)

        res = import_database_from_json(data_dict=backup_payload)
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("updated"), 1)

        row = get_transaction_by_uid(uid)
        self.assertIsNotNone(row)
        self.assertEqual(row["person_name"], "New Person")
        self.assertEqual(row["amount"], 200.0)

    def test_older_ignored(self):
        """Incoming row with older updated_at is ignored and local data is preserved."""
        uid = uuid.uuid4().hex
        t1_iso = "2026-09-20T10:00:00.000000+00:00"
        t2_iso = "2026-09-20T12:00:00.000000+00:00"

        with get_db_connection() as conn:
            conn.execute('''
                INSERT INTO transactions (
                    transaction_type, amount, person_name, transaction_date, transaction_time,
                    uid, occurred_at, deleted_at, created_at, updated_at, balance_before, balance_after
                ) VALUES ('SENT', 500.0, 'Local Newer', '2026-09-20', '12:00 PM', ?, '2026-09-20 12:00:00', NULL, ?, ?, 0.0, 0.0)
            ''', (uid, t2_iso, t2_iso))
            conn.commit()

        backup_payload = {
            "version": 2,
            "revision": 5,
            "settings": {},
            "custom_menu_items": [],
            "budgets": [],
            "transactions": [{
                "uid": uid,
                "transaction_type": "SENT",
                "amount": 50.0,
                "person_name": "Incoming Older",
                "transaction_date": "2026-09-20",
                "transaction_time": "10:00 AM",
                "occurred_at": "2026-09-20 10:00:00",
                "deleted_at": None,
                "created_at": t1_iso,
                "updated_at": t1_iso,
            }]
        }
        backup_payload["checksum"] = compute_canonical_checksum(backup_payload)

        res = import_database_from_json(data_dict=backup_payload)
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("updated"), 0)
        self.assertEqual(res.get("skipped"), 1)

        row = get_transaction_by_uid(uid)
        self.assertEqual(row["person_name"], "Local Newer")
        self.assertEqual(row["amount"], 500.0)

    def test_equal_timestamps_tombstone_wins(self):
        """When timestamps are equal, a tombstone wins over a live row."""
        uid = uuid.uuid4().hex
        same_iso = "2026-09-20T10:00:00.000000+00:00"

        with get_db_connection() as conn:
            conn.execute('''
                INSERT INTO transactions (
                    transaction_type, amount, person_name, transaction_date, transaction_time,
                    uid, occurred_at, deleted_at, created_at, updated_at, balance_before, balance_after
                ) VALUES ('SENT', 100.0, 'Live Guy', '2026-09-20', '10:00 AM', ?, '2026-09-20 10:00:00', NULL, ?, ?, 0.0, 0.0)
            ''', (uid, same_iso, same_iso))
            conn.commit()

        # Incoming has tombstone with equal updated_at
        backup_payload = {
            "version": 2,
            "revision": 5,
            "settings": {},
            "custom_menu_items": [],
            "budgets": [],
            "transactions": [{
                "uid": uid,
                "transaction_type": "SENT",
                "amount": 100.0,
                "person_name": "Live Guy",
                "transaction_date": "2026-09-20",
                "transaction_time": "10:00 AM",
                "occurred_at": "2026-09-20 10:00:00",
                "deleted_at": same_iso,
                "created_at": same_iso,
                "updated_at": same_iso,
            }]
        }
        backup_payload["checksum"] = compute_canonical_checksum(backup_payload)

        res = import_database_from_json(data_dict=backup_payload)
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("updated"), 1)

        row = get_transaction_by_uid(uid)
        self.assertIsNotNone(row["deleted_at"])

    def test_newer_local_tombstone_beats_older_live_backup(self):
        """A newer local tombstone beats an older live backup row."""
        uid = uuid.uuid4().hex
        t_create = "2026-09-20T08:00:00.000000+00:00"
        t_delete = "2026-09-20T14:00:00.000000+00:00"
        t_backup = "2026-09-20T10:00:00.000000+00:00"

        with get_db_connection() as conn:
            conn.execute('''
                INSERT INTO transactions (
                    transaction_type, amount, person_name, transaction_date, transaction_time,
                    uid, occurred_at, deleted_at, created_at, updated_at, balance_before, balance_after
                ) VALUES ('SENT', 250.0, 'Deleted Local', '2026-09-20', '08:00 AM', ?, '2026-09-20 08:00:00', ?, ?, ?, 0.0, 0.0)
            ''', (uid, t_delete, t_create, t_delete))
            conn.commit()

        # Backup was taken at t_backup (before deletion at t_delete) and has the row as live
        backup_payload = {
            "version": 2,
            "revision": 3,
            "settings": {},
            "custom_menu_items": [],
            "budgets": [],
            "transactions": [{
                "uid": uid,
                "transaction_type": "SENT",
                "amount": 250.0,
                "person_name": "Deleted Local",
                "transaction_date": "2026-09-20",
                "transaction_time": "08:00 AM",
                "occurred_at": "2026-09-20 08:00:00",
                "deleted_at": None,
                "created_at": t_create,
                "updated_at": t_backup,
            }]
        }
        backup_payload["checksum"] = compute_canonical_checksum(backup_payload)

        res = import_database_from_json(data_dict=backup_payload)
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("updated"), 0)
        self.assertEqual(res.get("skipped"), 1)

        row = get_transaction_by_uid(uid)
        self.assertIsNotNone(row["deleted_at"])

    def test_v1_id_collision_cannot_overwrite_local_row(self):
        """A v1 backup row matching an existing local id does NOT overwrite it."""
        # Insert a local row
        uid_local = uuid.uuid4().hex
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO transactions (
                    transaction_type, amount, person_name, reference_number, transaction_date, transaction_time,
                    uid, occurred_at, deleted_at, created_at, updated_at, balance_before, balance_after
                ) VALUES ('SENT', 999.0, 'Local Keeper', 'REF_LOCAL_1', '2026-09-20', '09:00 AM', ?, '2026-09-20 09:00:00', NULL, '2026-09-20T09:00:00+00:00', '2026-09-20T09:00:00+00:00', 0.0, 0.0)
            ''', (uid_local,))
            local_id = cursor.lastrowid
            conn.commit()

        # v1 backup with matching 'id' but different details
        v1_backup = {
            "version": 1,
            "transactions": [{
                "id": local_id,
                "transaction_type": "RECEIVED",
                "amount": 111.0,
                "person_name": "V1 Colliding ID",
                "reference_number": "REF_V1_NEW_2",
                "transaction_date": "2026-09-20",
                "transaction_time": "09:30 AM",
            }]
        }

        res = import_database_from_json(data_dict=v1_backup)
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("inserted"), 1)

        # Ensure local row at local_id was NOT overwritten
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM transactions WHERE id = ?", (local_id,))
            row = dict(cursor.fetchone())
            self.assertEqual(row["person_name"], "Local Keeper")
            self.assertEqual(row["amount"], 999.0)

    def test_repeated_restore_is_idempotent(self):
        """Re-importing the same backup is completely idempotent."""
        # Export current state
        export_path = Path("data/test_idempotent.json")
        try:
            data = export_database_to_json(output_path=export_path)
            self.assertTrue(export_path.exists())

            # First import
            res1 = import_database_from_json(input_path=export_path)
            self.assertTrue(res1.get("success"))
            self.assertEqual(res1.get("inserted"), 0)

            # Second import
            res2 = import_database_from_json(input_path=export_path)
            self.assertTrue(res2.get("success"))
            self.assertEqual(res2.get("inserted"), 0)
            self.assertEqual(res2.get("updated"), 0)
            self.assertEqual(res2.get("skipped"), res2.get("total"))
        finally:
            if export_path.exists():
                os.remove(export_path)

    def test_tampered_checksum_rejected(self):
        """Any tampering with backup fields without updating checksum rejects import."""
        uid = uuid.uuid4().hex
        backup_payload = {
            "version": 2,
            "revision": 5,
            "settings": {},
            "custom_menu_items": [],
            "budgets": [],
            "transactions": [{
                "uid": uid,
                "transaction_type": "SENT",
                "amount": 100.0,
                "person_name": "Original",
                "transaction_date": "2026-09-20",
                "transaction_time": "10:00 AM",
                "occurred_at": "2026-09-20 10:00:00",
                "deleted_at": None,
                "created_at": "2026-09-20T10:00:00+00:00",
                "updated_at": "2026-09-20T10:00:00+00:00",
            }]
        }
        # Compute valid checksum
        backup_payload["checksum"] = compute_canonical_checksum(backup_payload)

        # Tamper payload
        backup_payload["transactions"][0]["amount"] = 99999.0

        res = import_database_from_json(data_dict=backup_payload)
        self.assertFalse(res.get("success"))
        self.assertIn("Checksum mismatch", res.get("error", ""))

    def test_rollback_on_bad_row(self):
        """A bad/corrupt row inside the backup causes complete atomic rollback."""
        uid1 = uuid.uuid4().hex
        uid2 = uuid.uuid4().hex

        bad_payload = {
            "version": 2,
            "revision": 12,
            "settings": {},
            "custom_menu_items": [],
            "budgets": [],
            "transactions": [
                {
                    "uid": uid1,
                    "transaction_type": "SENT",
                    "amount": 150.0,
                    "person_name": "Valid First",
                    "transaction_date": "2026-09-20",
                    "transaction_time": "10:00 AM",
                    "occurred_at": "2026-09-20 10:00:00",
                    "deleted_at": None,
                    "created_at": "2026-09-20T10:00:00+00:00",
                    "updated_at": "2026-09-20T10:00:00+00:00",
                },
                {
                    "uid": uid2,
                    "transaction_type": "SENT",
                    "amount": -500.0,  # INVALID AMOUNT
                    "person_name": "Bad Amount",
                    "transaction_date": "2026-09-20",
                    "transaction_time": "10:05 AM",
                    "occurred_at": "2026-09-20 10:05:00",
                    "deleted_at": None,
                    "created_at": "2026-09-20T10:05:00+00:00",
                    "updated_at": "2026-09-20T10:05:00+00:00",
                }
            ]
        }
        bad_payload["checksum"] = compute_canonical_checksum(bad_payload)

        res = import_database_from_json(data_dict=bad_payload)
        self.assertFalse(res.get("success"))

        # Confirm uid1 was NOT inserted due to rollback
        row1 = get_transaction_by_uid(uid1)
        self.assertIsNone(row1)

    def test_restart_scenario_deleted_row_stays_deleted(self):
        """Restart scenario: delete -> backup -> wipe DB -> restore -> row stays deleted."""
        uid = uuid.uuid4().hex
        now_iso = utc_now_iso()

        with get_db_connection() as conn:
            conn.execute('''
                INSERT INTO transactions (
                    transaction_type, amount, person_name, transaction_date, transaction_time,
                    uid, occurred_at, deleted_at, created_at, updated_at, balance_before, balance_after
                ) VALUES ('SENT', 300.0, 'To Be Tombstone', '2026-09-20', '10:00 AM', ?, '2026-09-20 10:00:00', ?, ?, ?, 0.0, 0.0)
            ''', (uid, now_iso, now_iso, now_iso))
            conn.commit()

        # Export backup with tombstone
        export_path = Path("data/test_restart_tombstone.json")
        try:
            backup_data = export_database_to_json(output_path=export_path)
            self.assertTrue(export_path.exists())

            # Simulate clean wipe
            with get_db_connection() as conn:
                conn.execute("DELETE FROM transactions")
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('database_initialized', '0')")
                conn.commit()

            # Restore from backup
            res = import_database_from_json(input_path=export_path)
            self.assertTrue(res.get("success"))

            # Check that row is restored as a tombstone
            row = get_transaction_by_uid(uid)
            self.assertIsNotNone(row)
            self.assertIsNotNone(row["deleted_at"])
        finally:
            if export_path.exists():
                os.remove(export_path)

    def test_startup_skip_logic_and_backup_blocked(self):
        """Startup skips restore when DB is populated or initialized; blocks backup on empty restore failure."""
        with get_db_connection() as conn:
            # Set backup_blocked to 1
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('backup_blocked', '1')")
            conn.commit()

        # Try to backup to telegram while blocked
        from services.backup_service import backup_to_telegram
        import asyncio

        class FakeBot:
            pass

        async def run_backup():
            return await backup_to_telegram(FakeBot())

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            success = loop.run_until_complete(run_backup())
            self.assertFalse(success)
        finally:
            loop.close()

        # Inserting a real transaction clears backup_blocked
        t = Transaction()
        t.amount = 50.0
        t.transaction_type = "SENT"
        t.transaction_date = "2026-09-21"
        t.person_name = "First Real Tx"
        tx_id = insert_transaction_with_balance(t)

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM settings WHERE key = 'backup_blocked'")
            row = cur.fetchone()
            self.assertEqual(row["value"], "0")
            cur.execute("SELECT value FROM settings WHERE key = 'database_initialized'")
            row2 = cur.fetchone()
    def test_stale_empty_backup_rejection(self):
        """A stale empty backup must NEVER delete current live rows unless explicitly confirmed."""
        from database.models import Transaction
        from database.queries import insert_transaction_with_balance
        from services.backup_service import import_database_from_json, compute_canonical_checksum
        
        t = Transaction(amount=500.0, transaction_type="SENT", person_name="Stale Test")
        insert_transaction_with_balance(t)
        
        stale_backup = {
            "version": 2,
            "revision": 1,
            "exported_at": "2026-01-01T00:00:00Z",
            "transaction_count": 0,
            "live_count": 0,
            "empty_ledger": True,
            "balance": 0.0,
            "settings": {"backup_revision": "1"},
            "custom_menu_items": [],
            "budgets": [],
            "transactions": []
        }
        stale_backup["checksum"] = compute_canonical_checksum(stale_backup)
        
        res = import_database_from_json(data_dict=stale_backup, allow_empty_ledger=False)
        self.assertFalse(res.get("success"))
        err_msg = res.get("error", "")
        self.assertTrue("Stale" in err_msg or "requires" in err_msg)
        
        with get_db_connection() as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM transactions WHERE deleted_at IS NULL").fetchone()[0]
            self.assertGreater(cnt, 0)

if __name__ == '__main__':
    unittest.main()
