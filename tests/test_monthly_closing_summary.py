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

    def test_monthly_closing_keyboard_callback_data_under_64_bytes(self):
        kb = get_monthly_closing_keyboard(2026, 8, is_closed=True)
        for row in kb.inline_keyboard:
            for btn in row:
                cb = btn.callback_data
                if cb:
                    self.assertLessEqual(len(cb.encode('utf-8')), 64)

if __name__ == "__main__":
    unittest.main()
