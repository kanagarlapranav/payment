import unittest
import json
import os
from config import BASE_DIR
from database.db import setup_database
from database.queries import (
    get_balance_setting, get_monthly_summary, get_category_summary,
    get_recent_transactions, get_all_transactions
)
from services.budget_service import get_budget_info

class TestDashboardData(unittest.TestCase):
    def setUp(self):
        setup_database()

    def test_dashboard_template_exists(self):
        template_path = BASE_DIR / 'web' / 'templates' / 'dashboard.html'
        self.assertTrue(os.path.exists(template_path))
        with open(template_path, 'r', encoding='utf-8') as f:
            content = f.read()
        self.assertIn("Payment Tracker", content)
        self.assertIn("categoryChart", content)
        self.assertIn("cashflowChart", content)

    def test_api_data_payload_structure(self):
        balance = get_balance_setting()
        monthly = get_monthly_summary(2026, 9)
        cat_summary = get_category_summary(2026, 9)
        budget_data = get_budget_info(2026, 9)
        recent_txs = get_recent_transactions(limit=10)
        all_txs = get_all_transactions()

        payload = {
            'period_name': "September 2026",
            'current_balance': balance,
            'total_received': monthly['total_received'],
            'total_spent': monthly['total_sent'],
            'net_savings': monthly['net_savings'],
            'total_transactions': len(all_txs),
            'budget_info': budget_data,
            'categories': cat_summary,
            'recent_transactions': recent_txs
        }

        # Verify JSON serializable
        json_str = json.dumps(payload, default=str)
        self.assertIsInstance(json_str, str)
        parsed = json.loads(json_str)
        self.assertEqual(parsed['current_balance'], balance)
        self.assertIn('categories', parsed)
        self.assertIn('budget_info', parsed)

if __name__ == '__main__':
    unittest.main()
