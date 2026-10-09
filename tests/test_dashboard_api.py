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
from services.dashboard_auth import create_one_time_code, exchange_code_for_session
from app import WebAppAndHealthHandler

class TestDashboardData(unittest.TestCase):
    def setUp(self):
        setup_database()
        # Create a valid session cookie for tests
        code = create_one_time_code()
        success, sid, cookie_hdr = exchange_code_for_session(code, client_ip="127.0.0.1")
        self.session_cookie = cookie_hdr.split(";")[0]

    def test_dashboard_template_exists(self):
        template_path = BASE_DIR / 'web' / 'templates' / 'dashboard.html'
        self.assertTrue(os.path.exists(template_path))
        with open(template_path, 'r', encoding='utf-8') as f:
            content = f.read()
        self.assertIn("Payment Tracker", content)
        self.assertIn("categoryChart", content)
        self.assertIn("cashflowChart", content)
        self.assertNotIn("localStorage.setItem('dashboard_token'", content)

    def test_api_data_payload_structure(self):
        handler = WebAppAndHealthHandler.__new__(WebAppAndHealthHandler)
        handler.path = '/api/data?year=2026&month=9'
        handler.client_address = ('127.0.0.1', 1234)
        handler.headers = {'Cookie': self.session_cookie}
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()

        handler.do_GET()
        handler.send_response.assert_called_with(200)

        raw_body = handler.wfile.getvalue().decode('utf-8')
        parsed = json.loads(raw_body)
        
        self.assertEqual(parsed['period_name'], "September 2026")
        self.assertEqual(parsed['year'], 2026)
        self.assertEqual(parsed['month'], 9)
        self.assertIn('current_balance', parsed)
        self.assertIn('total_received', parsed)
        self.assertIn('total_spent', parsed)
        self.assertIn('net_savings', parsed)
        self.assertIn('total_transactions', parsed)
        self.assertIn('month_transactions_count', parsed)
        self.assertIn('comparison', parsed)
        self.assertIn('budget_info', parsed)
        self.assertIn('categories', parsed)
        self.assertIn('daily_series', parsed)
        self.assertIn('top_payees', parsed)
        self.assertIn('recent_transactions', parsed)
        self.assertIn('backup_status', parsed)

    def test_handler_healthz_get(self):
        handler = WebAppAndHealthHandler.__new__(WebAppAndHealthHandler)
        handler.path = '/healthz'
        handler.client_address = ('127.0.0.1', 1234)
        handler.headers = {}
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
        handler.client_address = ('127.0.0.1', 1234)
        handler.headers = {}
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()

        handler.do_HEAD()
        handler.send_response.assert_called_with(200)

    def test_handler_api_unauthorized_without_session(self):
        handler = WebAppAndHealthHandler.__new__(WebAppAndHealthHandler)
        handler.path = '/api/data'
        handler.client_address = ('127.0.0.1', 1234)
        handler.headers = {}
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()

        handler.do_GET()
        handler.send_response.assert_called_with(401)

    def test_handler_api_authorized_with_valid_session_cookie(self):
        handler = WebAppAndHealthHandler.__new__(WebAppAndHealthHandler)
        handler.path = '/api/data'
        handler.client_address = ('127.0.0.1', 1234)
        handler.headers = {'Cookie': self.session_cookie}
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()

        handler.do_GET()
        handler.send_response.assert_called_with(200)

    def test_handler_api_invalid_year(self):
        for bad_year in ["abc", "1899", "2101"]:
            handler = WebAppAndHealthHandler.__new__(WebAppAndHealthHandler)
            handler.path = f'/api/data?year={bad_year}'
            handler.client_address = ('127.0.0.1', 1234)
            handler.headers = {'Cookie': self.session_cookie}
            handler.send_response = MagicMock()
            handler.send_header = MagicMock()
            handler.end_headers = MagicMock()
            handler.wfile = io.BytesIO()

            handler.do_GET()
            handler.send_response.assert_called_with(400)

    def test_handler_api_invalid_month(self):
        for bad_month in ["abc", "0", "13", "-1"]:
            handler = WebAppAndHealthHandler.__new__(WebAppAndHealthHandler)
            handler.path = f'/api/data?month={bad_month}'
            handler.client_address = ('127.0.0.1', 1234)
            handler.headers = {'Cookie': self.session_cookie}
            handler.send_response = MagicMock()
            handler.send_header = MagicMock()
            handler.end_headers = MagicMock()
            handler.wfile = io.BytesIO()

            handler.do_GET()
            handler.send_response.assert_called_with(400)

if __name__ == '__main__':
    unittest.main()
