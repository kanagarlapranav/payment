import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database.db import get_db_connection, setup_database
from database.models import Transaction
from database.queries import (
    get_balance_setting,
    get_transaction_by_id,
    insert_transaction_with_balance,
    update_transaction,
)
from services.balance_service import (
    recalculate_all_balances,
    set_explicit_balance,
    validate_ledger_invariants,
)
from utils.dates import build_occurred_at


class TestLedgerReliability(unittest.TestCase):
    """
    Test suite for Prompt 3: Atomic ledger insertion, balance chain recalculation,
    ordering, /setbalance, and invariant validation.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_db_path = Path(self.temp_dir.name) / "test_ledger.sqlite3"
        self.db_patcher = patch("database.db.DB_PATH", self.test_db_path)
        self.cfg_patcher = patch("config.DB_PATH", self.test_db_path)
        self.db_patcher.start()
        self.cfg_patcher.start()

        # Initialize isolated schema
        setup_database()

    def tearDown(self):
        self.db_patcher.stop()
        self.cfg_patcher.stop()
        self.temp_dir.cleanup()

    def test_sent_and_received_signs(self):
        set_explicit_balance(1000.00)
        self.assertEqual(get_balance_setting(), 1000.00)

        # RECEIVED adds to balance
        t_recv = Transaction(
            transaction_type="RECEIVED",
            amount=250.50,
            transaction_date="2026-09-21",
            transaction_time="10:00 AM",
            reference_number="REF_SIGN_RECV",
        )
        insert_transaction_with_balance(t_recv)
        self.assertEqual(get_balance_setting(), 1250.50)

        # SENT subtracts from balance
        t_sent = Transaction(
            transaction_type="SENT",
            amount=100.25,
            transaction_date="2026-09-21",
            transaction_time="11:00 AM",
            reference_number="REF_SIGN_SENT",
        )
        insert_transaction_with_balance(t_sent)
        self.assertEqual(get_balance_setting(), 1150.25)

        errors = validate_ledger_invariants(db_path=self.test_db_path)
        self.assertEqual(errors, [])

    def test_failed_insert_leaves_balance_unchanged(self):
        set_explicit_balance(500.00)
        t_valid = Transaction(
            transaction_type="SENT",
            amount=100.00,
            transaction_date="2026-09-21",
            transaction_time="10:00 AM",
            reference_number="REF_VALID_1",
        )
        insert_transaction_with_balance(t_valid)
        self.assertEqual(get_balance_setting(), 400.00)

        # Invalid amount (negative)
        t_bad_amount = Transaction(
            transaction_type="SENT",
            amount=-50.00,
            transaction_date="2026-09-21",
            transaction_time="11:00 AM",
            reference_number="REF_BAD_1",
        )
        with self.assertRaises(ValueError):
            insert_transaction_with_balance(t_bad_amount)

        # Invalid transaction type
        t_bad_type = Transaction(
            transaction_type="INVALID_TYPE",
            amount=50.00,
            transaction_date="2026-09-21",
            transaction_time="11:00 AM",
            reference_number="REF_BAD_2",
        )
        with self.assertRaises(ValueError):
            insert_transaction_with_balance(t_bad_type)

        # Assert balance is completely unchanged and row count is 1
        self.assertEqual(get_balance_setting(), 400.00)
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM transactions")
            self.assertEqual(cur.fetchone()[0], 1)

    def test_duplicate_reference(self):
        t1 = Transaction(
            transaction_type="SENT",
            amount=100.00,
            transaction_date="2026-09-21",
            transaction_time="10:00 AM",
            reference_number="REF_DUP_123",
        )
        insert_transaction_with_balance(t1)

        t2 = Transaction(
            transaction_type="RECEIVED",
            amount=200.00,
            transaction_date="2026-09-21",
            transaction_time="11:00 AM",
            reference_number="REF_DUP_123",
        )
        with self.assertRaises(ValueError) as ctx:
            insert_transaction_with_balance(t2)
        self.assertIn("Duplicate live reference number", str(ctx.exception))

        # Direct duplicate in database is also detected by validate_ledger_invariants
        with get_db_connection() as conn:
            conn.execute("DROP INDEX IF EXISTS idx_tx_ref_live")
            conn.execute(
                "INSERT INTO transactions (transaction_type, amount, balance_before, balance_after, reference_number, uid) VALUES ('SENT', 10.0, 0.0, 0.0, 'REF_DUP_123', '11112222333344445555666677778888')"
            )
            conn.commit()

        errors = validate_ledger_invariants(db_path=self.test_db_path)
        self.assertTrue(any("Duplicate live reference_number" in e for e in errors))

    def test_simulated_insert_exception(self):
        set_explicit_balance(450.00)

        # Simulate exception during recalculation
        with patch(
            "services.balance_service.recalculate_in_connection",
            side_effect=RuntimeError("Simulated DB error during insert"),
        ):
            t = Transaction(
                transaction_type="SENT",
                amount=50.00,
                transaction_date="2026-09-21",
                transaction_time="12:00 PM",
                reference_number="REF_SIM_ERR",
            )
            with self.assertRaises(RuntimeError):
                insert_transaction_with_balance(t)

        # Assert balance remains 450.00 and no row was persisted
        self.assertEqual(get_balance_setting(), 450.00)
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM transactions")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_editing_old_transaction_updates_later_balances(self):
        set_explicit_balance(1000.00)

        t1_id = insert_transaction_with_balance(
            Transaction(
                transaction_type="SENT",
                amount=100.00,
                transaction_date="2026-09-01",
                transaction_time="10:00 AM",
                reference_number="REF_EDIT_1",
            )
        )
        t2_id = insert_transaction_with_balance(
            Transaction(
                transaction_type="SENT",
                amount=50.00,
                transaction_date="2026-09-02",
                transaction_time="10:00 AM",
                reference_number="REF_EDIT_2",
            )
        )
        t3_id = insert_transaction_with_balance(
            Transaction(
                transaction_type="RECEIVED",
                amount=200.00,
                transaction_date="2026-09-03",
                transaction_time="10:00 AM",
                reference_number="REF_EDIT_3",
            )
        )

        self.assertEqual(get_balance_setting(), 1050.00)

        # Edit T1 from SENT 100 to SENT 200
        success = update_transaction(t1_id, {"amount": 200.00})
        self.assertTrue(success)

        # Recalculated state:
        # Initial: 1000
        # T1: 1000 -> 800
        # T2: 800 -> 750
        # T3: 750 -> 950
        row1 = get_transaction_by_id(t1_id)
        row2 = get_transaction_by_id(t2_id)
        row3 = get_transaction_by_id(t3_id)

        self.assertEqual(row1["balance_before"], 1000.00)
        self.assertEqual(row1["balance_after"], 800.00)

        self.assertEqual(row2["balance_before"], 800.00)
        self.assertEqual(row2["balance_after"], 750.00)

        self.assertEqual(row3["balance_before"], 750.00)
        self.assertEqual(row3["balance_after"], 950.00)

        self.assertEqual(get_balance_setting(), 950.00)

        errors = validate_ledger_invariants(db_path=self.test_db_path)
        self.assertEqual(errors, [])

    def test_backdated_insert(self):
        set_explicit_balance(1000.00)

        t1_id = insert_transaction_with_balance(
            Transaction(
                transaction_type="SENT",
                amount=100.00,
                transaction_date="2026-09-10",
                transaction_time="10:00 AM",
                reference_number="REF_BACK_1",
            )
        )
        t2_id = insert_transaction_with_balance(
            Transaction(
                transaction_type="SENT",
                amount=100.00,
                transaction_date="2026-09-12",
                transaction_time="10:00 AM",
                reference_number="REF_BACK_2",
            )
        )
        self.assertEqual(get_balance_setting(), 800.00)

        # Back-dated insert on 2026-09-11
        t_back_id = insert_transaction_with_balance(
            Transaction(
                transaction_type="SENT",
                amount=50.00,
                transaction_date="2026-09-11",
                transaction_time="10:00 AM",
                reference_number="REF_BACK_DATE",
            )
        )

        # Ordering should be: T1 (9/10), T_back (9/11), T2 (9/12)
        row1 = get_transaction_by_id(t1_id)
        row_back = get_transaction_by_id(t_back_id)
        row2 = get_transaction_by_id(t2_id)

        self.assertEqual(row1["balance_before"], 1000.00)
        self.assertEqual(row1["balance_after"], 900.00)

        self.assertEqual(row_back["balance_before"], 900.00)
        self.assertEqual(row_back["balance_after"], 850.00)

        self.assertEqual(row2["balance_before"], 850.00)
        self.assertEqual(row2["balance_after"], 750.00)

        self.assertEqual(get_balance_setting(), 750.00)

        errors = validate_ledger_invariants(db_path=self.test_db_path)
        self.assertEqual(errors, [])

    def test_empty_ledger(self):
        with get_db_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('initial_balance', '500.00')"
            )
            conn.commit()

        derived = recalculate_all_balances()
        self.assertEqual(derived, 500.00)
        self.assertEqual(get_balance_setting(), 500.00)

        errors = validate_ledger_invariants(db_path=self.test_db_path)
        self.assertEqual(errors, [])

    def test_setbalance_100_10(self):
        # Insert initial transactions
        insert_transaction_with_balance(
            Transaction(
                transaction_type="SENT",
                amount=50.00,
                transaction_date="2026-09-20",
                transaction_time="10:00 AM",
                reference_number="REF_SETBAL_1",
            )
        )
        insert_transaction_with_balance(
            Transaction(
                transaction_type="RECEIVED",
                amount=200.00,
                transaction_date="2026-09-20",
                transaction_time="11:00 AM",
                reference_number="REF_SETBAL_2",
            )
        )

        # User runs /setbalance 100.10
        final_bal = set_explicit_balance(100.10)
        self.assertEqual(final_bal, 100.10)
        self.assertEqual(get_balance_setting(), 100.10)

        # Subsequent recalculations must preserve 100.10
        recalculated = recalculate_all_balances()
        self.assertEqual(recalculated, 100.10)
        self.assertEqual(get_balance_setting(), 100.10)

        errors = validate_ledger_invariants(db_path=self.test_db_path)
        self.assertEqual(errors, [])

    def test_nan_and_inf(self):
        for bad_val in [float("nan"), float("inf"), -float("inf")]:
            with self.subTest(val=bad_val):
                with self.assertRaises(ValueError):
                    insert_transaction_with_balance(
                        Transaction(
                            transaction_type="SENT",
                            amount=bad_val,
                            transaction_date="2026-09-21",
                        )
                    )
                with self.assertRaises(ValueError):
                    set_explicit_balance(bad_val)

        # Check invariant validator catches corrupt NaN/Inf row
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO transactions (transaction_type, amount, balance_before, balance_after, uid) VALUES ('SENT', 'nan', 0.0, 0.0, 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')"
            )
            conn.commit()

        errors = validate_ledger_invariants(db_path=self.test_db_path)
        self.assertTrue(any("non-positive or invalid amount" in e for e in errors))

    def test_twelve_hour_time_ordering(self):
        set_explicit_balance(1000.00)
        date_str = "2026-09-21"

        # Verify build_occurred_at converts to 24h format accurately
        self.assertEqual(
            build_occurred_at(date_str, "10:02 AM"), "2026-09-21 10:02:00"
        )
        self.assertEqual(
            build_occurred_at(date_str, "01:44 PM"), "2026-09-21 13:44:00"
        )
        self.assertEqual(
            build_occurred_at(date_str, "09:57 PM"), "2026-09-21 21:57:00"
        )

        # Insert transactions in reverse order
        t_late = Transaction(
            transaction_type="SENT",
            amount=30.00,
            transaction_date=date_str,
            transaction_time="09:57 PM",
            reference_number="REF_TIME_LATE",
        )
        t_noon = Transaction(
            transaction_type="SENT",
            amount=20.00,
            transaction_date=date_str,
            transaction_time="01:44 PM",
            reference_number="REF_TIME_NOON",
        )
        t_early = Transaction(
            transaction_type="SENT",
            amount=10.00,
            transaction_date=date_str,
            transaction_time="10:02 AM",
            reference_number="REF_TIME_EARLY",
        )

        t_late_id = insert_transaction_with_balance(t_late)
        t_noon_id = insert_transaction_with_balance(t_noon)
        t_early_id = insert_transaction_with_balance(t_early)

        # Chronological order: 10:02 AM -> 01:44 PM -> 09:57 PM
        r_early = get_transaction_by_id(t_early_id)
        r_noon = get_transaction_by_id(t_noon_id)
        r_late = get_transaction_by_id(t_late_id)

        # Early (10:02 AM): 1000 - 10 = 990
        self.assertEqual(r_early["balance_before"], 1000.00)
        self.assertEqual(r_early["balance_after"], 990.00)

        # Noon (01:44 PM): 990 - 20 = 970
        self.assertEqual(r_noon["balance_before"], 990.00)
        self.assertEqual(r_noon["balance_after"], 970.00)

        # Late (09:57 PM): 970 - 30 = 940
        self.assertEqual(r_late["balance_before"], 970.00)
        self.assertEqual(r_late["balance_after"], 940.00)

        self.assertEqual(get_balance_setting(), 940.00)

        errors = validate_ledger_invariants(db_path=self.test_db_path)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
