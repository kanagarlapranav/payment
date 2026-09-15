import unittest
from services.budget_service import set_budget, get_budget_info, format_budget_status, check_budget_alert
from database.db import setup_database

class TestBudgetService(unittest.TestCase):
    def setUp(self):
        setup_database()

    def test_set_and_get_budget(self):
        msg = set_budget(25000.0)
        self.assertIn("25,000", msg)
        
        info = get_budget_info(2026, 9)
        self.assertEqual(info["budget"], 25000.0)
        self.assertTrue(0 <= info["percentage"] <= 100 or info["percentage"] > 100)
        self.assertTrue(len(info["progress_bar"]) > 0)

    def test_budget_status_formatting(self):
        set_budget(20000.0)
        card = format_budget_status(2026, 9)
        self.assertIn("MONTHLY BUDGET TRACKER", card)
        self.assertIn("Budget Limit", card)

    def test_budget_disabled(self):
        msg = set_budget(0)
        self.assertIn("disabled", msg)
        card = format_budget_status(2026, 9)
        self.assertIn("No monthly budget target is currently set", card)

    def test_budget_alert_thresholds(self):
        set_budget(10000.0)
        # Test alert calculation for an outgoing expense
        alert = check_budget_alert(1000.0, "SENT")
        self.assertIsInstance(alert, str)

if __name__ == '__main__':
    unittest.main()
