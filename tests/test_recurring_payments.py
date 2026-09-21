import unittest
from datetime import date, timedelta
from database.db import get_db_connection, setup_database
from database.queries import get_transaction_by_id
from services.recurring_service import (
    calculate_next_due_date, add_recurring_payment, get_all_recurring,
    get_recurring_by_id, get_upcoming_recurring, mark_recurring_paid,
    skip_recurring_due, update_recurring_status, delete_recurring_payment,
    get_recurring_monthly_total, add_months
)
from bot.keyboards import get_recurring_menu_keyboard, get_recurring_detail_keyboard

class TestRecurringPayments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_database()

    def test_due_date_calculations(self):
        # 1. Month-end clamping
        jan_31 = date(2026, 1, 31)
        feb_due = calculate_next_due_date(jan_31, "MONTHLY", interval=1)
        self.assertEqual(feb_due, date(2026, 2, 28))

        # 2. Leap year month-end
        jan_31_2028 = date(2028, 1, 31)
        feb_2028 = calculate_next_due_date(jan_31_2028, "MONTHLY", interval=1)
        self.assertEqual(feb_2028, date(2028, 2, 29))

        # 3. Weekly interval
        start_d = date(2026, 9, 1)
        next_week = calculate_next_due_date(start_d, "WEEKLY", interval=2)
        self.assertEqual(next_week, date(2026, 9, 15))

        # 4. Yearly interval
        next_year = calculate_next_due_date(start_d, "YEARLY", interval=1)
        self.assertEqual(next_year, date(2027, 9, 1))

    def test_add_and_get_recurring(self):
        rec_id = add_recurring_payment(
            payee_name="Broadband WiFi",
            amount=999.0,
            category="Bills & Utilities",
            frequency="MONTHLY",
            start_date=date(2026, 9, 15),
            notes="Fiber optic plan"
        )
        self.assertIsNotNone(rec_id)
        
        rec = get_recurring_by_id(rec_id)
        self.assertEqual(rec['payee_name'], "Broadband WiFi")
        self.assertEqual(rec['amount'], 999.0)
        self.assertEqual(rec['status'], "ACTIVE")
        self.assertEqual(str(rec['next_due_date'])[:10], "2026-09-15")

    def test_mark_paid_creates_transaction_and_advances_date(self):
        rec_id = add_recurring_payment(
            payee_name="Gym Membership",
            amount=1500.0,
            category="Fitness",
            frequency="MONTHLY",
            start_date=date(2026, 9, 10)
        )
        
        paid_date = date(2026, 9, 10)
        tx_id, next_due = mark_recurring_paid(rec_id, paid_date=paid_date)
        
        # Verify transaction created
        self.assertIsNotNone(tx_id)
        tx = get_transaction_by_id(tx_id)
        self.assertEqual(tx['amount'], 1500.0)
        self.assertEqual(tx['person_name'], "Gym Membership")
        self.assertEqual(tx['category'], "Fitness")
        
        # Verify recurring record updated
        rec_after = get_recurring_by_id(rec_id)
        self.assertEqual(str(rec_after['last_paid_date'])[:10], "2026-09-10")
        self.assertEqual(rec_after['next_due_date'], "2026-10-10")
        self.assertEqual(next_due, date(2026, 10, 10))

    def test_skip_recurring_due(self):
        rec_id = add_recurring_payment(
            payee_name="Newspaper Subscription",
            amount=300.0,
            frequency="MONTHLY",
            start_date=date(2026, 9, 1)
        )
        
        new_due = skip_recurring_due(rec_id)
        self.assertEqual(new_due, date(2026, 10, 1))
        
        rec = get_recurring_by_id(rec_id)
        self.assertEqual(rec['next_due_date'], "2026-10-01")
        self.assertIsNone(rec['last_paid_date'])

    def test_recurring_monthly_projection(self):
        # Clear / sample items
        total = get_recurring_monthly_total()
        self.assertGreaterEqual(total, 0.0)

    def test_recurring_keyboards_callback_data_under_64_bytes(self):
        kb_menu = get_recurring_menu_keyboard(upcoming_items=[{"id": 12, "payee_name": "Netflix", "amount": 649.0}])
        kb_detail = get_recurring_detail_keyboard(12, status="ACTIVE")
        
        for kb in [kb_menu, kb_detail]:
            for row in kb.inline_keyboard:
                for btn in row:
                    cb = btn.callback_data
                    if cb:
                        self.assertLessEqual(len(cb.encode('utf-8')), 64)

if __name__ == "__main__":
    unittest.main()
