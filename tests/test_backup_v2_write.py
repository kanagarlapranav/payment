import asyncio
import hashlib
import json
import os
import threading
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from config import DATA_DIR, DB_PATH
from database.db import LEDGER_LOCK, get_db_connection, setup_database
from database.models import Transaction
from database.queries import (
    delete_transaction,
    insert_transaction_with_balance,
)
from services.backup_service import (
    BACKUP_JSON_PATH,
    backup_to_telegram,
    export_database_to_json,
)
from services.task_manager import TaskManager


class TestBackupV2WriteSide(unittest.TestCase):
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

    def test_checksum_generation_canonical(self):
        """1. Checksum generation: SHA-256 over canonical JSON of version, revision, settings, custom_menu_items, budgets, transactions."""
        test_path = DATA_DIR / "test_checksum_export.json"
        try:
            data = export_database_to_json(output_path=test_path)
            self.assertTrue(test_path.exists())
            self.assertIn("checksum", data)
            self.assertIn("database_id", data)

            # Manually reproduce canonical SHA-256
            canonical_payload = {
                "version": data["version"],
                "revision": data["revision"],
                "settings": data["settings"],
                "custom_menu_items": data["custom_menu_items"],
                "budgets": data["budgets"],
                "transactions": data["transactions"],
            }
            canonical_str = json.dumps(canonical_payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
            expected_checksum = hashlib.sha256(canonical_str.encode('utf-8')).hexdigest()
            self.assertEqual(data["checksum"], expected_checksum)

            # Any alteration must change the checksum
            mutated_payload = dict(canonical_payload)
            mutated_payload["revision"] = data["revision"] + 999
            mutated_str = json.dumps(mutated_payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
            mutated_checksum = hashlib.sha256(mutated_str.encode('utf-8')).hexdigest()
            self.assertNotEqual(data["checksum"], mutated_checksum)
        finally:
            if test_path.exists():
                test_path.unlink()

    def test_empty_ledger_vs_zero_row_database(self):
        """2. Empty ledger vs zero-row database: zero rows refuses upload/export; tombstone ledger uploads."""
        test_path = DATA_DIR / "test_empty_rules.json"
        mock_bot = MagicMock()
        mock_bot.send_document = AsyncMock()

        try:
            # Step A: Simulate zero-row database
            with LEDGER_LOCK, get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM transactions")
                conn.commit()

            try:
                # Zero rows must return {} and NOT write file
                res = export_database_to_json(output_path=test_path)
                self.assertEqual(res, {})
                self.assertFalse(test_path.exists())

                # backup_to_telegram must refuse to upload and not send document
                with patch('services.backup_service.BACKUP_JSON_PATH', test_path):
                    ok = asyncio.run(backup_to_telegram(mock_bot, chat_id="12345"))
                    self.assertFalse(ok)
                    mock_bot.send_document.assert_not_called()

                # Step B: Insert a transaction then soft-delete it (tombstone ledger)
                t = Transaction()
                t.amount = 50.0
                t.transaction_type = "SENT"
                t.person_name = "Tombstone Test"
                t.transaction_date = "2026-09-21"
                t.transaction_time = "10:00 AM"
                tx_id = insert_transaction_with_balance(t)

                # Soft delete leaves 1 tombstone row, live_count == 0
                delete_transaction(tx_id)

                # Now export should succeed with empty_ledger=True and live_count=0
                res2 = export_database_to_json(output_path=test_path)
                self.assertTrue(test_path.exists())
                self.assertEqual(res2.get("transaction_count"), 1)
                self.assertEqual(res2.get("live_count"), 0)
                self.assertTrue(res2.get("empty_ledger"))

                # Cloud backup MUST be uploaded for valid empty active ledger
                mock_msg = MagicMock()
                mock_msg.message_id = 8888
                mock_bot.send_document = AsyncMock(return_value=mock_msg)
                mock_bot.pin_chat_message = AsyncMock()
                mock_bot.get_chat = AsyncMock(return_value=MagicMock(pinned_message=None))

                with patch('services.backup_service.BACKUP_JSON_PATH', test_path):
                    ok2 = asyncio.run(backup_to_telegram(mock_bot, chat_id="12345"))
                    self.assertTrue(ok2)
                    mock_bot.send_document.assert_called_once()
            finally:
                # Restore rows if any
                with LEDGER_LOCK, get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM transactions")
                    conn.commit()
                if self.db_backup:
                    with open(DB_PATH, 'wb') as f:
                        f.write(self.db_backup)
        finally:
            if test_path.exists():
                test_path.unlink()

    def test_atomic_write_crash_simulation(self):
        """3. Atomic write: writing to .tmp, flush, fsync, then replace. Mid-write crash leaves original file intact."""
        test_path = DATA_DIR / "test_atomic_file.json"
        original_content = {"valid_key": "untouched_data_before_crash"}
        try:
            with open(test_path, 'w', encoding='utf-8') as f:
                json.dump(original_content, f)

            # Patch os.fsync to raise an OSError simulating power cut or crash mid-write
            with patch('os.fsync', side_effect=OSError("Simulated system crash mid-write")):
                res = export_database_to_json(output_path=test_path)
                self.assertEqual(res, {})

            # Original file MUST remain untouched
            self.assertTrue(test_path.exists())
            with open(test_path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            self.assertEqual(loaded, original_content)
        finally:
            if test_path.exists():
                test_path.unlink()

    def test_concurrent_exports_produce_valid_files(self):
        """4. Concurrent exports under lock produce valid files without corruption or race condition."""
        test_path = DATA_DIR / "test_concurrent_export.json"
        errors = []

        def worker():
            try:
                for _ in range(5):
                    data = export_database_to_json(output_path=test_path)
                    if not data or "checksum" not in data:
                        errors.append("Invalid data returned")
            except (OSError, sqlite3.Error, ValueError) as err:
                errors.append(str(err))

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertTrue(test_path.exists())

        # Verify final file is valid JSON and not corrupted
        with open(test_path, 'r', encoding='utf-8') as f:
            content = json.load(f)
        self.assertEqual(content.get("version"), 2)
        self.assertIn("checksum", content)

        if test_path.exists():
            test_path.unlink()

    def test_upload_failure_reported_and_marks_dirty(self):
        """5. Upload failure is reported (returns False) and marks database dirty in settings."""
        mock_bot = MagicMock()
        mock_bot.get_chat = AsyncMock(return_value=MagicMock(pinned_message=None))
        mock_bot.send_document = AsyncMock(side_effect=RuntimeError("Connection timeout"))

        # Clear dirty flag first
        with LEDGER_LOCK, get_db_connection() as conn:
            conn.execute("UPDATE settings SET value = '0' WHERE key = 'is_dirty'")
            conn.commit()

        # Execute backup_to_telegram which fails
        ok = asyncio.run(backup_to_telegram(mock_bot, chat_id="12345"))
        self.assertFalse(ok)

        # Verify settings now has is_dirty = '1'
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = 'is_dirty'")
            val = cursor.fetchone()['value']
            self.assertEqual(val, '1')

    def test_lower_revision_refusal(self):
        """6. Refuse to upload a backup whose revision is lower than newest revision in cloud."""
        mock_bot = MagicMock()
        # Mock pinned message in cloud with higher revision (e.g. Rev 500)
        pinned_msg = MagicMock()
        pinned_msg.caption = "#PAYMENT_TRACKER_BACKUP_V2 ☁️ Auto-Backup Rev 500 (10 records, 10 live)"
        mock_bot.get_chat = AsyncMock(return_value=MagicMock(pinned_message=pinned_msg))
        mock_bot.send_document = AsyncMock()

        # Set local database revision lower (e.g. 100)
        with LEDGER_LOCK, get_db_connection() as conn:
            conn.execute("UPDATE settings SET value = '100' WHERE key = 'backup_revision'")
            conn.commit()

        ok = asyncio.run(backup_to_telegram(mock_bot, chat_id="12345"))
        self.assertFalse(ok)
        mock_bot.send_document.assert_not_called()

    def test_debounce_task_manager(self):
        """7. Debounce: multiple rapid mutations coalesce into a single background backup."""
        tm = TaskManager()
        mock_bot = MagicMock()

        with patch('services.backup_service.backup_to_telegram', new_callable=AsyncMock) as mock_bkp:
            mock_bkp.return_value = True

            async def _run_debounce_test():
                # Trigger 5 calls with 0.05s delay
                for _ in range(5):
                    tm.schedule_debounced_backup(mock_bot, chat_id="123", delay=0.05)
                    await asyncio.sleep(0.01)

                # Wait for debounce quiet window to elapse
                await asyncio.sleep(0.15)

            asyncio.run(_run_debounce_test())

            # Even though schedule_debounced_backup was called 5 times, backup_to_telegram ran exactly once!
            self.assertEqual(mock_bkp.call_count, 1)

    def test_revision_increment_on_committed_mutations_only(self):
        """Verify revision counter increments once per committed mutation, NOT on export."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = 'backup_revision'")
            rev_before = int(cursor.fetchone()['value'])

        # Calling export_database_to_json does NOT increase revision
        export_database_to_json()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = 'backup_revision'")
            rev_after_export = int(cursor.fetchone()['value'])
        self.assertEqual(rev_before, rev_after_export)

        # Committed mutation (inserting a transaction) MUST increase revision by 1
        t = Transaction()
        t.amount = 75.0
        t.transaction_type = "RECEIVED"
        t.person_name = "Revision Test"
        t.transaction_date = "2026-09-21"
        t.transaction_time = "11:00 AM"
        tx_id = insert_transaction_with_balance(t)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = 'backup_revision'")
            rev_after_mutation = int(cursor.fetchone()['value'])
        self.assertEqual(rev_after_mutation, rev_before + 1)

        # Clean up inserted transaction
        delete_transaction(tx_id)

    def test_telegram_backup_message_rotation_keeps_last_7(self):
        """Verify confirmed Telegram uploads rotate and retain only the last 7 backup message IDs."""
        mock_bot = MagicMock()
        mock_bot.get_chat = AsyncMock(return_value=MagicMock(pinned_message=None))
        mock_bot.pin_chat_message = AsyncMock()
        mock_bot.delete_message = AsyncMock()
        mock_bot.unpin_chat_message = AsyncMock()

        # Seed settings with 6 existing backup message IDs: [101, 102, 103, 104, 105, 106]
        seed_ids = [101, 102, 103, 104, 105, 106]
        with LEDGER_LOCK, get_db_connection() as conn:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = 'telegram_backup_message_ids'",
                (json.dumps(seed_ids),)
            )
            conn.execute("UPDATE settings SET value = '9999' WHERE key = 'backup_revision'")
            conn.commit()

        # 1. 7th upload: message_id = 107. Total becomes 7. None should be pruned.
        mock_bot.send_document = AsyncMock(return_value=MagicMock(message_id=107))
        ok = asyncio.run(backup_to_telegram(mock_bot, chat_id="12345", force=True))
        self.assertTrue(ok)
        mock_bot.delete_message.assert_not_called()

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = 'telegram_backup_message_ids'")
            ids = json.loads(cursor.fetchone()['value'])
            self.assertEqual(ids, [101, 102, 103, 104, 105, 106, 107])

        # 2. 8th upload: message_id = 108. Total would be 8. 101 must be pruned/deleted, keeping [102..108].
        mock_bot.send_document = AsyncMock(return_value=MagicMock(message_id=108))
        ok = asyncio.run(backup_to_telegram(mock_bot, chat_id="12345", force=True))
        self.assertTrue(ok)
        mock_bot.delete_message.assert_called_once_with(chat_id="12345", message_id=101)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = 'telegram_backup_message_ids'")
            ids = json.loads(cursor.fetchone()['value'])
            self.assertEqual(ids, [102, 103, 104, 105, 106, 107, 108])
            self.assertEqual(len(ids), 7)


if __name__ == '__main__':
    unittest.main()
