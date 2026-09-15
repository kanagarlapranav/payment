import unittest
from services.category_service import predict_category, get_category_icon, format_spending_insights
from database.db import setup_database

class TestCategoryService(unittest.TestCase):
    def setUp(self):
        setup_database()

    def test_predict_food(self):
        cat = predict_category("Paid to Swiggy order 450", "Swiggy", "SENT")
        self.assertEqual(cat, "Food & Dining")

    def test_predict_groceries(self):
        cat = predict_category("Blinkit Commerce Delivery", "Blinkit", "SENT")
        self.assertEqual(cat, "Groceries")

    def test_predict_travel(self):
        cat = predict_category("Uber trip payment", "Uber", "SENT")
        self.assertEqual(cat, "Travel & Transport")

    def test_predict_bills(self):
        cat = predict_category("Paid electricity bill", "APEPDCL", "SENT")
        self.assertEqual(cat, "Bills & Utilities")

    def test_predict_person_transfer(self):
        cat = predict_category("Money sent to friend", "Ramesh Kumar", "SENT")
        self.assertEqual(cat, "Transfers & P2P")

    def test_category_icon(self):
        self.assertEqual(get_category_icon("Food & Dining"), "🍔")
        self.assertEqual(get_category_icon("Groceries"), "🛒")
        self.assertEqual(get_category_icon("Nonexistent"), "💳")

    def test_format_spending_insights(self):
        text = format_spending_insights(2026, 9)
        self.assertIn("SPENDING INSIGHTS", text)
        self.assertIn("Total Income", text)
        self.assertIn("Total Spent", text)

if __name__ == '__main__':
    unittest.main()
