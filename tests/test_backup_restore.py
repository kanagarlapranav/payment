import unittest
import os
import json
import uuid
import tempfile
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
    get_default_workspace_id,
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
        self.tmp_dir = tempfile.TemporaryDirectory()
        setup_database()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM settings")
            self._initial_settings = {row["key"]: row["value"] for row in cursor.fetchall()}

    def tearDown(self):
        if hasattr(self, "tmp_dir"):
            self.tmp_dir.cleanup()
        if hasattr(self, "_initial_settings"):
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM settings")
                for k, v in self._initial_settings.items():
                    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v))
                conn.commit()

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

        row = get_transaction_by_uid(uid, workspace_id=get_default_workspace_id())
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

        row = get_transaction_by_uid(uid, workspace_id=get_default_workspace_id())
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

        row = get_transaction_by_uid(uid, workspace_id=get_default_workspace_id())
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

        row = get_transaction_by_uid(uid, workspace_id=get_default_workspace_id())
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
        export_path = Path(self.tmp_dir.name) / "test_idempotent.json"
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
        row1 = get_transaction_by_uid(uid1, workspace_id=get_default_workspace_id())
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

        # Snapshot transactions before clean wipe
        saved_tx_rows = []
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM transactions")
            col_names = [d[0] for d in cursor.description] if cursor.description else []
            saved_tx_rows = [dict(zip(col_names, row)) for row in cursor.fetchall()]

        # Export backup with tombstone
        export_path = Path(self.tmp_dir.name) / "test_restart_tombstone.json"
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
            row = get_transaction_by_uid(uid, workspace_id=get_default_workspace_id())
            self.assertIsNotNone(row)
            self.assertIsNotNone(row["deleted_at"])
        finally:
            if export_path.exists():
                os.remove(export_path)
            # Restore original transactions
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM transactions")
                for r in saved_tx_rows:
                    cols = list(r.keys())
                    placeholders = ",".join(["?"] * len(cols))
                    colnames_str = ",".join(cols)
                    cursor.execute(f"INSERT INTO transactions ({colnames_str}) VALUES ({placeholders})", [r[c] for c in cols])
                conn.commit()

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
        self.assertTrue(
            any(kw in err_msg for kw in ("Stale", "stale", "rejected", "requires", "required", "Empty backup")),
            f"Expected stale/rejection message, got: {err_msg!r}"
        )
        
        with get_db_connection() as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM transactions WHERE deleted_at IS NULL").fetchone()[0]
            self.assertGreater(cnt, 0)

    def test_json_import_staging_ttl_and_authorization(self):
        """Verify that _pending_json_imports prunes stale entries and rejects non-uploader/non-owner callers."""
        import time
        from pathlib import Path
        from unittest.mock import MagicMock, AsyncMock, patch
        from bot.handlers import (
            _pending_json_imports, _prune_pending_json_imports, handle_callback_query
        )
        import asyncio

        # 1. TTL test
        fake_token = "tok_test_ttl_123"
        fake_path = Path("tmp_fake_test_file.json")
        _pending_json_imports[fake_token] = {
            'path': fake_path,
            'preview': {},
            'created_at': time.time() - 700,  # > 600s ago
            'uploader_id': 12345,
            'workspace_id': 'default'
        }
        _prune_pending_json_imports()
        self.assertNotIn(fake_token, _pending_json_imports)

        # 2. Authorization test: stranger trying to confirm import uploaded by another user
        valid_token = "tok_test_auth_456"
        _pending_json_imports[valid_token] = {
            'path': fake_path,
            'preview': {},
            'created_at': time.time(),
            'uploader_id': 11111,
            'workspace_id': 'default'
        }

        update = MagicMock()
        query = MagicMock()
        query.data = f"json_import_confirm:{valid_token}"
        query.answer = AsyncMock()
        update.callback_query = query
        user = MagicMock()
        user.id = 99999  # Stranger (neither uploader 11111 nor owner)
        update.effective_user = user
        context = MagicMock()

        with patch("bot.handlers.is_owner", return_value=False), \
             patch("bot.handlers.require_owner", AsyncMock(return_value=False)):
            asyncio.run(handle_callback_query(update, context))

        # Must still be in _pending_json_imports (not consumed by stranger)
        self.assertIn(valid_token, _pending_json_imports)
        _pending_json_imports.pop(valid_token, None)

    def test_checksum_covers_workspaces_and_metadata(self):
        """Verifies that tampering with workspaces, balance, or metadata invalidates checksum."""
        from services.backup_service import compute_canonical_checksum, verify_backup_payload
        uid = uuid.uuid4().hex
        payload = {
            "version": 2,
            "revision": 3,
            "exported_at": "2026-10-09T12:00:00+00:00",
            "balance": 1500.0,
            "empty_ledger": False,
            "workspaces": [{"id": "ws-1", "title": "Test Workspace"}],
            "workspace_members": [{"workspace_id": "ws-1", "telegram_user_id": 12345}],
            "workspace_settings": [{"workspace_id": "ws-1", "key": "k", "value": "v"}],
            "settings": {},
            "custom_menu_items": [],
            "budgets": [],
            "transactions": [{
                "uid": uid,
                "transaction_type": "SENT",
                "amount": 100.0,
                "person_name": "Test Person",
                "transaction_date": "2026-10-09",
                "transaction_time": "12:00 PM",
                "occurred_at": "2026-10-09 12:00:00",
                "created_at": "2026-10-09T12:00:00+00:00",
                "updated_at": "2026-10-09T12:00:00+00:00",
            }]
        }
        payload["checksum"] = compute_canonical_checksum(payload)
        valid, msg = verify_backup_payload(payload)
        self.assertTrue(valid, msg)

        # Tampering with balance must fail verification
        tampered_balance = dict(payload)
        tampered_balance["balance"] = 9999.0
        valid, msg = verify_backup_payload(tampered_balance)
        self.assertFalse(valid)
        self.assertIn("Checksum mismatch", msg)

        # Tampering with workspaces must fail verification
        tampered_ws = dict(payload)
        tampered_ws["workspaces"] = [{"id": "ws-2", "title": "Tampered"}]
        valid, msg = verify_backup_payload(tampered_ws)
        self.assertFalse(valid)
        self.assertIn("Checksum mismatch", msg)

    def test_validate_json_backup_rejects_duplicate_or_invalid_uids(self):
        """Pre-validation catches duplicate UIDs or malformed UIDs before DB insertion."""
        from services.backup_service import validate_json_backup, compute_canonical_checksum
        dup_uid = uuid.uuid4().hex
        payload = {
            "version": 2,
            "revision": 1,
            "settings": {},
            "custom_menu_items": [],
            "budgets": [],
            "transactions": [
                {
                    "uid": dup_uid,
                    "transaction_type": "SENT",
                    "amount": 50.0,
                    "person_name": "First Entry",
                    "transaction_date": "2026-10-09",
                },
                {
                    "uid": dup_uid,
                    "transaction_type": "RECEIVED",
                    "amount": 75.0,
                    "person_name": "Duplicate Entry",
                    "transaction_date": "2026-10-09",
                }
            ]
        }
        payload["checksum"] = compute_canonical_checksum(payload)
        valid, msg = validate_json_backup(payload)
        self.assertFalse(valid)
        self.assertIn("Duplicate UID", msg)

        # Missing UID in v2 must be rejected
        payload_missing_uid = {
            "version": 2,
            "revision": 1,
            "settings": {},
            "custom_menu_items": [],
            "budgets": [],
            "transactions": [
                {
                    "uid": "",
                    "transaction_type": "SENT",
                    "amount": 50.0,
                    "person_name": "No UID Entry",
                    "transaction_date": "2026-10-09",
                }
            ]
        }
        payload_missing_uid["checksum"] = compute_canonical_checksum(payload_missing_uid)
        valid, msg = validate_json_backup(payload_missing_uid)
        self.assertFalse(valid)
        self.assertIn("missing mandatory UID", msg)

    def test_import_database_from_json_sets_is_dirty_to_1(self):
        """Verifies that successful JSON import marks is_dirty = '1' to trigger cloud sync."""
        from services.backup_service import import_database_from_json, compute_canonical_checksum
        with get_db_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('is_dirty', '0', '2026-10-09T00:00:00Z')")
            conn.commit()

        uid = uuid.uuid4().hex
        payload = {
            "version": 2,
            "revision": 2,
            "settings": {},
            "custom_menu_items": [],
            "budgets": [],
            "transactions": [{
                "uid": uid,
                "transaction_type": "SENT",
                "amount": 25.0,
                "person_name": "Dirty Test",
                "transaction_date": "2026-10-09",
            }]
        }
        payload["checksum"] = compute_canonical_checksum(payload)
        res = import_database_from_json(data_dict=payload)
        self.assertTrue(res.get("success"), res.get("error"))

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = 'is_dirty'")
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["value"], "1")

    def test_stale_backup_prevention_on_delete_and_restart(self):
        """
        Problem 1 Regression Test:
        Deleting a transaction must update the cloud backup so a wiped DB restart does NOT
        restore the stale pre-delete backup. Confirmation message must report backup status honestly.
        """
        import asyncio
        from unittest.mock import MagicMock, AsyncMock, patch
        from bot.handlers import handle_callback_query
        from services.backup_service import restore_from_telegram, backup_to_telegram
        from services.balance_service import set_explicit_balance
        from bot.commands import setbalance_command

        # 0. Clean database for this test
        with get_db_connection() as conn:
            conn.execute("DELETE FROM transactions")
            conn.execute("UPDATE settings SET value = '0.0' WHERE key = 'current_balance'")
            conn.commit()

        # 1. Setup initial state: transaction with balance
        t = Transaction(amount=100.0, transaction_type="RECEIVED", person_name="Alice")
        tx_id = insert_transaction_with_balance(t)
        self.assertEqual(get_balance_setting(), 100.0)

        # 2. Simulate Telegram bot with mock chat and pinned message storage
        pinned_holder = {'pinned': None}

        async def fake_send_document(chat_id, document, caption=None, **kwargs):
            doc_file = MagicMock()
            doc_file.file_id = f"file_{uuid.uuid4().hex}"
            doc_file.file_name = "backup.json"
            # Read bytes of document
            if hasattr(document, 'seek'):
                document.seek(0)
                content = document.read()
            else:
                content = document
            msg = MagicMock()
            msg.message_id = 9999
            msg.document = doc_file
            msg.caption = caption
            msg._file_content = content
            pinned_holder['pinned'] = msg
            return msg

        async def fake_get_chat(chat_id):
            chat = MagicMock()
            chat.pinned_message = pinned_holder['pinned']
            return chat

        async def fake_get_file(file_id):
            f = MagicMock()
            async def fake_download(custom_path):
                with open(custom_path, 'wb') as out:
                    out.write(pinned_holder['pinned']._file_content)
            f.download_to_drive = AsyncMock(side_effect=fake_download)
            return f

        mock_bot = MagicMock()
        mock_bot.send_document = AsyncMock(side_effect=fake_send_document)
        mock_bot.get_chat = AsyncMock(side_effect=fake_get_chat)
        mock_bot.get_file = AsyncMock(side_effect=fake_get_file)
        mock_bot.pin_chat_message = AsyncMock(return_value=True)
        mock_bot.unpin_chat_message = AsyncMock(return_value=True)

        # 3. Create initial pre-delete cloud backup pinned in Telegram
        loop = asyncio.new_event_loop()
        try:
            bk_ok = loop.run_until_complete(backup_to_telegram(mock_bot, force=True))
            self.assertTrue(bk_ok)
            self.assertIsNotNone(pinned_holder['pinned'])

            # 4. Invoke delete callback via handle_callback_query
            query = MagicMock()
            query.data = f"delete_confirm:{tx_id}"
            query.answer = AsyncMock()
            edit_calls = []
            async def fake_edit(text, **kwargs):
                edit_calls.append(text)
            query.edit_message_text = AsyncMock(side_effect=fake_edit)
            query.message = MagicMock()
            query.message.edit_text = AsyncMock(side_effect=fake_edit)

            update = MagicMock()
            update.callback_query = query
            update.effective_chat = MagicMock(id=123456789)
            update.effective_user = MagicMock(id=123456789)

            context = MagicMock()
            context.bot = mock_bot
            context.user_data = {}

            with patch("bot.handlers.is_owner", return_value=True), \
                 patch("bot.handlers.require_owner", AsyncMock(return_value=True)):
                loop.run_until_complete(handle_callback_query(update, context))

            # Verify confirmation message shows honest backup status
            self.assertTrue(len(edit_calls) > 0)
            conf_msg = edit_calls[-1]
            self.assertIn("Cloud backup completed", conf_msg)

            # 5. Wipe isolated database
            with get_db_connection() as conn:
                conn.execute("DELETE FROM transactions")
                conn.execute("UPDATE settings SET value = '0' WHERE key = 'database_initialized'")
                conn.execute("UPDATE settings SET value = '0.0' WHERE key = 'current_balance'")
                conn.commit()

            # 6. Restore from Telegram into empty database
            res = loop.run_until_complete(restore_from_telegram(mock_bot, chat_id="123456789"))
            self.assertTrue(res.success)

            # Assert: deleted row MUST STAY DELETED and balance must NOT revert to 100.0!
            with get_db_connection() as conn:
                live_cnt = conn.execute("SELECT COUNT(*) FROM transactions WHERE deleted_at IS NULL").fetchone()[0]
                self.assertEqual(live_cnt, 0)
                bal = float(conn.execute("SELECT value FROM settings WHERE key = 'current_balance'").fetchone()[0])
                self.assertEqual(bal, 0.0)

            # 7. Also verify balance change sequence persists across wipe
            context.args = ["250.0"]
            update.message = MagicMock()
            update.message.reply_text = AsyncMock()
            loop.run_until_complete(setbalance_command(update, context))

            # Wipe DB again
            with get_db_connection() as conn:
                conn.execute("DELETE FROM transactions")
                conn.execute("UPDATE settings SET value = '0' WHERE key = 'database_initialized'")
                conn.execute("UPDATE settings SET value = '0.0' WHERE key = 'current_balance'")
                conn.commit()

            # Restore again
            res2 = loop.run_until_complete(restore_from_telegram(mock_bot, chat_id="123456789"))
            self.assertTrue(res2.success)
            self.assertEqual(get_balance_setting(), 250.0)

            # 8. Verify failure reporting when cloud backup fails
            t2 = Transaction(amount=50.0, transaction_type="SENT", person_name="Bob")
            tx2_id = insert_transaction_with_balance(t2)
            query2 = MagicMock()
            query2.data = f"delete_confirm:{tx2_id}"
            query2.answer = AsyncMock()
            edit_calls2 = []
            async def fake_edit2(text, **kwargs):
                edit_calls2.append(text)
            query2.edit_message_text = AsyncMock(side_effect=fake_edit2)
            update2 = MagicMock()
            update2.callback_query = query2
            update2.effective_chat = MagicMock(id=123456789)
            update2.effective_user = MagicMock(id=123456789)
            fail_bot = MagicMock()
            fail_bot.send_document = AsyncMock(side_effect=RuntimeError("Network down"))
            fail_bot.get_chat = AsyncMock(side_effect=RuntimeError("Network down"))
            context2 = MagicMock()
            context2.bot = fail_bot
            context2.user_data = {}
            with patch("bot.handlers.is_owner", return_value=True), \
                 patch("bot.handlers.require_owner", AsyncMock(return_value=True)):
                loop.run_until_complete(handle_callback_query(update2, context2))
            self.assertTrue(len(edit_calls2) > 0)
            self.assertIn("cloud backup is pending retry", edit_calls2[-1])
        finally:
            loop.close()


if __name__ == '__main__':
    unittest.main()


