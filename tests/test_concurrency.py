import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from database.db import get_db_connection, setup_database
from database.models import Transaction
from database.queries import insert_transaction


class TestSQLiteConcurrency(unittest.TestCase):
    """
    Concurrency test verifying SQLite reliability under multi-threaded load:
    8 threads x 25 inserts (200 total inserts).
    Must produce NO "database is locked" errors and ensure all 200 rows are present.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_db_path = Path(self.temp_dir.name) / "concurrent_test.sqlite3"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_eight_threads_twenty_five_inserts(self):
        NUM_THREADS = 8
        INSERTS_PER_THREAD = 25
        TOTAL_EXPECTED = NUM_THREADS * INSERTS_PER_THREAD

        # Point DB_PATH in both database.db and config to the temporary SQLite file
        with patch("database.db.DB_PATH", self.test_db_path), \
             patch("config.DB_PATH", self.test_db_path):

            # Initialize database schema in WAL mode
            setup_database()

            # Verify WAL mode was applied
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute("PRAGMA journal_mode;")
                mode = cur.fetchone()[0]
                self.assertEqual(mode.lower(), "wal")

            barrier = threading.Barrier(NUM_THREADS)
            errors = []
            inserted_ids = []
            results_lock = threading.Lock()

            def worker(thread_idx: int):
                # Wait until all 8 threads are ready so they contend simultaneously
                barrier.wait()
                for i in range(INSERTS_PER_THREAD):
                    try:
                        tx = Transaction(
                            transaction_type="SENT" if (i % 2 == 0) else "RECEIVED",
                            amount=10.0 + (thread_idx * 100) + i,
                            person_name=f"Payee_T{thread_idx}_{i}",
                            sender_name=f"Sender_T{thread_idx}",
                            recipient_name=f"Recipient_T{thread_idx}",
                            reference_number=f"REF_T{thread_idx}_{i}_{i*7}",
                            category="General",
                            balance_before=1000.0,
                            balance_after=1000.0 - (10.0 + i) if (i % 2 == 0) else 1000.0 + (10.0 + i)
                        )
                        row_id = insert_transaction(tx)
                        with results_lock:
                            inserted_ids.append(row_id)
                    except Exception as exc:  # noqa: BLE001
                        with results_lock:
                            errors.append((thread_idx, i, exc))

            threads = [
                threading.Thread(target=worker, args=(t,))
                for t in range(NUM_THREADS)
            ]

            for t in threads:
                t.start()

            for t in threads:
                t.join(timeout=30.0)

            # 1. Assert NO exceptions occurred (specifically no "database is locked")
            if errors:
                err_msgs = [f"Thread {t_idx} item {i}: {type(e).__name__}: {e}" for t_idx, i, e in errors]
                self.fail(f"Concurrency errors detected ({len(errors)}):\n" + "\n".join(err_msgs))

            # 2. Assert exactly 200 row IDs returned
            self.assertEqual(len(inserted_ids), TOTAL_EXPECTED)

            # 3. Query the database directly to ensure all 200 rows are present
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM transactions")
                count = cur.fetchone()[0]
                self.assertEqual(count, TOTAL_EXPECTED)

                # Verify all UIDs are unique
                cur.execute("SELECT COUNT(DISTINCT uid) FROM transactions")
                distinct_uids = cur.fetchone()[0]
                self.assertEqual(distinct_uids, TOTAL_EXPECTED)


if __name__ == "__main__":
    unittest.main()
