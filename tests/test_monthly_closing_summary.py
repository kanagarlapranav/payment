import unittest
import uuid
from datetime import date
from database.db import get_db_connection, setup_database
from database.models import Transaction
from database.queries import insert_transaction_with_balance
from services.monthly_review_service import (
    calculate_monthly_closing_metrics, close_and_record_monthly_review, get_monthly_review
)
from bot.commands import render_monthly_closing_summary_text
from bot.keyboards import get_monthly_closing_keyboard

class TestMonthlyClosingSummary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_database()

    def test_calculate_closing_metrics(self):
        # Insert known income and expense for 2026-08
        t_inc = Transaction(
            amount=50000.0,
            transaction_type="RECEIVED",
            person_name="Tech Corp Salary",
            category="Salary",
            transaction_date="2026-08-01",
            reference_number=f"INC{uuid.uuid4().hex[:8].upper()}"
        )
        t_exp = Transaction(
            amount=15000.0,
            transaction_type="SENT",
            person_name="Landlord Realty",
            category="Rent & Housing",
            transaction_date="2026-08-05",
            reference_number=f"EXP{uuid.uuid4().hex[:8].upper()}"
        )
        insert_transaction_with_balance(t_inc)
        insert_transaction_with_balance(t_exp)

        metrics = calculate_monthly_closing_metrics(2026, 8)
        self.assertEqual(metrics['year'], 2026)
        self.assertEqual(metrics['month'], 8)
        self.assertGreaterEqual(metrics['total_income'], 50000.0)
        self.assertGreaterEqual(metrics['total_expense'], 15000.0)
        self.assertEqual(metrics['net_savings'], round(metrics['total_income'] - metrics['total_expense'], 2))
        self.assertIn('top_category', metrics)
        self.assertIn('top_payee', metrics)
        self.assertIsNotNone(metrics['max_transaction_id'])

    def test_close_and_record_monthly_review(self):
        rev = close_and_record_monthly_review(2026, 8, notes="Audited and closed")
        self.assertTrue(rev['is_closed'])
        self.assertIsNotNone(rev['reviewed_at'])

        # Verify retrieval
        fetched = get_monthly_review(2026, 8)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched['year'], 2026)
        self.assertEqual(fetched['month'], 8)
        self.assertEqual(fetched['is_closed'], 1)
        self.assertEqual(fetched['notes'], "Audited and closed")

    def test_render_monthly_closing_text(self):
        text = render_monthly_closing_summary_text(2026, 8)
        self.assertIn("Monthly Financial Closing", text)
        self.assertIn("Total Income:", text)
        self.assertIn("Total Expenses:", text)
        self.assertIn("Net Savings:", text)
        self.assertIn("Savings Rate:", text)
        self.assertIn("August 2026", text)

    def test_closed_month_freezes_mutations(self):
        """Once a month is closed, mutations (inserts, updates, deletes) in that month are rejected."""
        from services.monthly_review_service import close_and_record_monthly_review, reopen_monthly_review
        from database.queries import update_transaction, delete_transaction
        # First ensure transactions exist in 2026-06
        t_pre = Transaction(
            amount=500.0,
            transaction_type="SENT",
            person_name="Pre Close Payee",
            category="General",
            transaction_date="2026-06-10",
            reference_number=f"PRE{uuid.uuid4().hex[:8].upper()}"
        )
        tx_id = insert_transaction_with_balance(t_pre)
        self.assertIsNotNone(tx_id)

        # Close month 2026-06
        close_and_record_monthly_review(2026, 6)

        # 1. Back-dated insert into 2026-06 is rejected
        t_back = Transaction(
            amount=100.0,
            transaction_type="SENT",
            person_name="Late Payee",
            category="General",
            transaction_date="2026-06-15",
            reference_number=f"LATE{uuid.uuid4().hex[:8].upper()}"
        )
        with self.assertRaises(ValueError) as cm:
            insert_transaction_with_balance(t_back)
        self.assertIn("closed month 2026-06", str(cm.exception))

        # 2. Edit of transaction in closed month 2026-06 is rejected
        from database.queries import get_default_workspace_id
        default_ws = get_default_workspace_id()
        with self.assertRaises(ValueError) as cm:
            update_transaction(tx_id, {"amount": 600.0}, workspace_id=default_ws)
        self.assertIn("closed month 2026-06", str(cm.exception))

        # 3. Deletion of transaction in closed month 2026-06 is rejected
        with self.assertRaises(ValueError) as cm:
            delete_transaction(tx_id, workspace_id=default_ws)
        self.assertIn("closed month 2026-06", str(cm.exception))

        # 4. Reopening allows modification again
        self.assertTrue(reopen_monthly_review(2026, 6, workspace_id=default_ws))
        # Now update succeeds
        self.assertTrue(update_transaction(tx_id, {"amount": 600.0}, workspace_id=default_ws))

    def test_double_close_warns_and_sets_was_already_closed(self):
        """Double-closing a month sets was_already_closed = True."""
        from services.monthly_review_service import close_and_record_monthly_review, reopen_monthly_review
        reopen_monthly_review(2026, 5)
        rev1 = close_and_record_monthly_review(2026, 5)
        self.assertFalse(rev1['was_already_closed'])

        # Second close
        rev2 = close_and_record_monthly_review(2026, 5)
        self.assertTrue(rev2['was_already_closed'])


if __name__ == "__main__":
    unittest.main()

