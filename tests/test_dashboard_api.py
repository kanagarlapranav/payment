import unittest
import json
import os
import io
from unittest.mock import MagicMock, patch
from config import BASE_DIR
from database.db import setup_database
from database.queries import (
    get_balance_setting, get_monthly_summary, get_category_summary,
    get_recent_transactions, get_all_transactions
)
from services.budget_service import get_budget_info
from app import WebAppAndHealthHandler

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
        self.assertIn("X-Dash-Token", content)

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

    def test_handler_healthz_get(self):
        handler = WebAppAndHealthHandler.__new__(WebAppAndHealthHandler)
        handler.path = '/healthz'
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()

        handler.do_GET()
        handler.send_response.assert_called_with(200)
        self.assertEqual(handler.wfile.getvalue(), b"OK")

    def test_handler_healthz_head(self):
        handler = WebAppAndHealthHandler.__new__(WebAppAndHealthHandler)
        handler.path = '/healthz'
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()

        handler.do_HEAD()
        handler.send_response.assert_called_with(200)

    def test_handler_api_unauthorized_without_token(self):
        handler = WebAppAndHealthHandler.__new__(WebAppAndHealthHandler)
        handler.path = '/api/data'
        handler.headers = {}
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()

        with patch.dict(os.environ, {"DASHBOARD_TOKEN": "test_secret_123"}):
            with patch("app.DASHBOARD_TOKEN", "test_secret_123"):
                handler.do_GET()
                handler.send_response.assert_called_with(401)

    def test_handler_api_authorized_with_valid_header_token(self):
        handler = WebAppAndHealthHandler.__new__(WebAppAndHealthHandler)
        handler.path = '/api/data'
        handler.headers = {'X-Dash-Token': 'test_secret_123'}
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()

        with patch.dict(os.environ, {"DASHBOARD_TOKEN": "test_secret_123"}):
            with patch("app.DASHBOARD_TOKEN", "test_secret_123"):
                handler.do_GET()
                handler.send_response.assert_called_with(200)
                # Verify CORS wildcard is removed
                for call in handler.send_header.call_args_list:
                    self.assertNotEqual(call[0][0], 'Access-Control-Allow-Origin')

    def test_handler_api_authorized_with_valid_query_token(self):
        handler = WebAppAndHealthHandler.__new__(WebAppAndHealthHandler)
        handler.path = '/api/data?token=test_secret_123'
        handler.headers = {}
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()

        with patch.dict(os.environ, {"DASHBOARD_TOKEN": "test_secret_123"}):
            with patch("app.DASHBOARD_TOKEN", "test_secret_123"):
                handler.do_GET()
                handler.send_response.assert_called_with(200)

    def test_handler_api_invalid_year(self):
        for bad_year in ["abc", "1899", "2101"]:
            handler = WebAppAndHealthHandler.__new__(WebAppAndHealthHandler)
            handler.path = f'/api/data?token=test_secret_123&year={bad_year}'
            handler.headers = {}
            handler.send_response = MagicMock()
            handler.send_header = MagicMock()
            handler.end_headers = MagicMock()
            handler.wfile = io.BytesIO()

            with patch.dict(os.environ, {"DASHBOARD_TOKEN": "test_secret_123"}):
                with patch("app.DASHBOARD_TOKEN", "test_secret_123"):
                    handler.do_GET()
                    handler.send_response.assert_called_with(400)

    def test_handler_api_invalid_month(self):
        for bad_month in ["abc", "0", "13", "-1"]:
            handler = WebAppAndHealthHandler.__new__(WebAppAndHealthHandler)
            handler.path = f'/api/data?token=test_secret_123&month={bad_month}'
            handler.headers = {}
            handler.send_response = MagicMock()
            handler.send_header = MagicMock()
            handler.end_headers = MagicMock()
            handler.wfile = io.BytesIO()

            with patch.dict(os.environ, {"DASHBOARD_TOKEN": "test_secret_123"}):
                with patch("app.DASHBOARD_TOKEN", "test_secret_123"):
                    handler.do_GET()
                    handler.send_response.assert_called_with(400)

if __name__ == '__main__':
    unittest.main()
