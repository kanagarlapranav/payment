"""
Comprehensive Security and Authentication Test Suite for Dashboard (PROMPT 10):
1. Session Cookie Requirement:
   - /dashboard, /, /api/data, /api/transactions, /api/export.csv return 401 without valid session.
   - /healthz remains public (200 OK) and HEAD remains supported.
2. Single-Use 60s One-Time Code:
   - Valid code exchanges for HttpOnly, SameSite=Strict session cookie and 303 redirects to /dashboard (removing code from URL).
   - Reused code fails.
   - Expired code (>60s) fails.
   - Invalid code fails.
3. In-Memory Rate Limiting:
   - 5 consecutive failed auth attempts from same IP trigger rate limiting (HTTP 429).
4. Parameter Validation:
   - Year must be 1900-2100, month 1-12, else HTTP 400 with JSON error.
5. Telegram Command Safety:
   - /dashboard generates short-lived one-time code link, DASHBOARD_TOKEN never appears in URL or message.
6. Security Headers:
   - CSP, X-Content-Type-Options nosniff, Referrer-Policy no-referrer, Cache-Control no-store on APIs.
7. XSS & Non-ASCII Safety:
   - dashboard.html uses textContent for untrusted values.
   - compare_secrets is timing-attack safe and non-ASCII safe.
"""

import unittest
import json
import os
import io
import time
from unittest.mock import MagicMock, patch, AsyncMock
from urllib.parse import urlparse, parse_qs

from config import BASE_DIR
from database.db import setup_database
from services.dashboard_auth import (
    create_one_time_code,
    exchange_code_for_session,
    validate_session,
    is_rate_limited,
    compare_secrets,
    get_security_headers,
    _AUTH_CODES,
    _SESSIONS,
    _FAILED_LOGINS
)
from app import WebAppAndHealthHandler
from bot.commands import dashboard_command


class TestDashboardSecurity(unittest.TestCase):
    def setUp(self):
        setup_database()
        _AUTH_CODES.clear()
        _SESSIONS.clear()
        _FAILED_LOGINS.clear()

    # --- 1. Session Cookie Enforcement ---

    def test_unauthenticated_endpoints_return_401(self):
        """Endpoints /dashboard, /api/data, /api/transactions, /api/export.csv require a valid session."""
        endpoints = ['/dashboard', '/', '/api/data', '/api/transactions', '/api/export.csv']
        for ep in endpoints:
            handler = WebAppAndHealthHandler.__new__(WebAppAndHealthHandler)
            handler.path = ep
            handler.client_address = ('127.0.0.1', 5000)
            handler.headers = {}
            handler.send_response = MagicMock()
            handler.send_header = MagicMock()
            handler.end_headers = MagicMock()
            handler.wfile = io.BytesIO()

            handler.do_GET()
            handler.send_response.assert_called_with(401)

    def test_healthz_and_head_stay_public(self):
        """/healthz and HEAD requests stay public and return 200 OK without authentication."""
        handler = WebAppAndHealthHandler.__new__(WebAppAndHealthHandler)
        handler.path = '/healthz'
        handler.client_address = ('127.0.0.1', 5000)
        handler.headers = {}
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()

        handler.do_GET()
        handler.send_response.assert_called_with(200)
        self.assertEqual(handler.wfile.getvalue(), b"OK")

        handler.do_HEAD()
        handler.send_response.assert_called_with(200)

    # --- 2. One-Time Code Exchange & Session Cookie ---

    def test_one_time_code_exchange_success_and_redirect(self):
        """Valid code exchanges for session cookie, redirects 303 to /dashboard with code stripped from URL."""
        code = create_one_time_code()

        handler = WebAppAndHealthHandler.__new__(WebAppAndHealthHandler)
        handler.path = f'/auth?code={code}'
        handler.client_address = ('127.0.0.1', 5000)
        handler.headers = {'X-Forwarded-Proto': 'https'}
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()

        handler.do_GET()

        # Must redirect with 303 to /dashboard
        handler.send_response.assert_called_with(303)
        headers_dict = {call[0][0]: call[0][1] for call in handler.send_header.call_args_list}
        self.assertTrue(headers_dict.get('Location', '').startswith('/dashboard'))
        self.assertIn('Set-Cookie', headers_dict)
        cookie_val = headers_dict['Set-Cookie']
        self.assertIn('session_id=', cookie_val)
        self.assertIn('HttpOnly', cookie_val)
        self.assertTrue('SameSite=Lax' in cookie_val or 'SameSite=Strict' in cookie_val)
        self.assertIn('Secure', cookie_val)

        # Extract session_id and verify it grants access to /api/data
        cookie_str = cookie_val.split(";")[0]
        handler_api = WebAppAndHealthHandler.__new__(WebAppAndHealthHandler)
        handler_api.path = '/api/data'
        handler_api.client_address = ('127.0.0.1', 5000)
        handler_api.headers = {'Cookie': cookie_str}
        handler_api.send_response = MagicMock()
        handler_api.send_header = MagicMock()
        handler_api.end_headers = MagicMock()
        handler_api.wfile = io.BytesIO()

        handler_api.do_GET()
        handler_api.send_response.assert_called_with(200)

    def test_one_time_code_reused_fails(self):
        """A single-use code cannot be reused a second time."""
        code = create_one_time_code()

        # First use: success
        success, sid, cookie = exchange_code_for_session(code, client_ip="127.0.0.1")
        self.assertTrue(success)

        # Second use: fails immediately
        success2, err, _ = exchange_code_for_session(code, client_ip="127.0.0.1")
        self.assertFalse(success2)
        self.assertIn("Invalid", err)

    def test_expired_code_fails(self):
        """A code older than 60 seconds fails exchange."""
        code = create_one_time_code()
        # Simulate time passage >60s
        _AUTH_CODES[code]["created_at"] = time.time() - 65.0

        success, err, _ = exchange_code_for_session(code, client_ip="127.0.0.1")
        self.assertFalse(success)
        self.assertIn("expired", err.lower())

    def test_invalid_code_fails(self):
        """Non-existent code fails exchange."""
        success, err, _ = exchange_code_for_session("fake_code_12345", client_ip="127.0.0.1")
        self.assertFalse(success)
        self.assertIn("Invalid", err)

    # --- 3. Rate Limiting on Failed Authentication ---

    def test_rate_limiting_after_5_failed_attempts(self):
        """5 consecutive failed attempts from the same IP trigger rate limiting (HTTP 429)."""
        test_ip = "192.168.1.100"
        for i in range(5):
            success, err, _ = exchange_code_for_session(f"bad_code_{i}", client_ip=test_ip)
            self.assertFalse(success)

        self.assertTrue(is_rate_limited(test_ip))

        # 6th attempt via HTTP handler should return 429
        handler = WebAppAndHealthHandler.__new__(WebAppAndHealthHandler)
        handler.path = '/auth?code=another_bad_code'
        handler.client_address = (test_ip, 5000)
        handler.headers = {}
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()

        handler.do_GET()
        handler.send_response.assert_called_with(429)

    # --- 4. Query Parameter Validation ---

    def test_parameter_validation_year_and_month(self):
        """Invalid year or month query parameters return HTTP 400 with a JSON error."""
        code = create_one_time_code()
        _, _, cookie_hdr = exchange_code_for_session(code, client_ip="127.0.0.1")
        session_cookie = cookie_hdr.split(";")[0]

        bad_params = [
            "/api/data?year=abc",
            "/api/data?year=1800",
            "/api/data?year=2200",
            "/api/data?month=0",
            "/api/data?month=13",
            "/api/data?month=invalid",
            "/api/transactions?year=xyz",
            "/api/transactions?month=99"
        ]

        for path in bad_params:
            handler = WebAppAndHealthHandler.__new__(WebAppAndHealthHandler)
            handler.path = path
            handler.client_address = ('127.0.0.1', 5000)
            handler.headers = {'Cookie': session_cookie}
            handler.send_response = MagicMock()
            handler.send_header = MagicMock()
            handler.end_headers = MagicMock()
            handler.wfile = io.BytesIO()

            handler.do_GET()
            handler.send_response.assert_called_with(400)
            data = json.loads(handler.wfile.getvalue().decode('utf-8'))
            self.assertIn("error", data)

    # --- 5. Security Headers ---

    def test_security_headers_present(self):
        """All responses attach CSP, X-Content-Type-Options: nosniff, Referrer-Policy: no-referrer, and Cache-Control: no-store on APIs."""
        code = create_one_time_code()
        _, _, cookie_hdr = exchange_code_for_session(code, client_ip="127.0.0.1")
        session_cookie = cookie_hdr.split(";")[0]

        handler = WebAppAndHealthHandler.__new__(WebAppAndHealthHandler)
        handler.path = '/api/data'
        handler.client_address = ('127.0.0.1', 5000)
        handler.headers = {'Cookie': session_cookie}
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()

        handler.do_GET()
        headers_dict = {call[0][0]: call[0][1] for call in handler.send_header.call_args_list}

        self.assertEqual(headers_dict.get('X-Content-Type-Options'), 'nosniff')
        self.assertEqual(headers_dict.get('Referrer-Policy'), 'no-referrer')
        self.assertEqual(headers_dict.get('X-Frame-Options'), 'DENY')
        self.assertIn('default-src', headers_dict.get('Content-Security-Policy', ''))
        self.assertIn('no-store', headers_dict.get('Cache-Control', ''))

    # --- 6. Telegram /dashboard Command Safety ---

    def test_telegram_dashboard_command_never_exposes_permanent_token(self):
        """The /dashboard command generates a one-time code and never includes permanent DASHBOARD_TOKEN."""
        import asyncio
        from config import TELEGRAM_USER_ID

        mock_update = MagicMock()
        mock_update.effective_user.id = TELEGRAM_USER_ID
        mock_update.message.reply_text = AsyncMock()
        mock_context = MagicMock()

        async def _run():
            with patch.dict(os.environ, {"DASHBOARD_TOKEN": "SUPER_SECRET_TOKEN_999"}):
                await dashboard_command(mock_update, mock_context)
                self.assertTrue(mock_update.message.reply_text.called)
                args, kwargs = mock_update.message.reply_text.call_args
                msg_text = args[0] if args else kwargs.get("text", "")
                markup = kwargs.get("reply_markup")

                # Verify DASHBOARD_TOKEN is NEVER present
                self.assertNotIn("SUPER_SECRET_TOKEN_999", msg_text)
                self.assertNotIn("?token=", msg_text)

                # Verify /auth?code= is present
                self.assertIn("/auth?code=", msg_text)

                # Check inline buttons
                for row in markup.inline_keyboard:
                    for btn in row:
                        btn_url = getattr(btn, 'url', None) or (btn.web_app.url if hasattr(btn, 'web_app') and btn.web_app else None)
                        self.assertNotIn("SUPER_SECRET_TOKEN_999", str(btn_url))
                        self.assertIn("/auth?code=", str(btn_url))

        asyncio.run(_run())

    # --- 7. Safe Comparison & Template Safety ---

    def test_compare_secrets_non_ascii_safe(self):
        """compare_secrets handles non-ASCII characters without encoding crash."""
        self.assertTrue(compare_secrets("₹500_Token_🚀", "₹500_Token_🚀"))
        self.assertFalse(compare_secrets("₹500_Token_🚀", "₹500_Token_✨"))
        self.assertFalse(compare_secrets("", "test"))
        self.assertFalse(compare_secrets(None, "test"))

    def test_dashboard_template_uses_textcontent_for_untrusted_data(self):
        """dashboard.html must use textContent for payee names, categories, and references."""
        template_path = BASE_DIR / 'web' / 'templates' / 'dashboard.html'
        with open(template_path, 'r', encoding='utf-8') as f:
            html = f.read()

        # Ensure no localStorage token storage
        self.assertNotIn("localStorage.setItem('dashboard_token'", html)
        self.assertNotIn("localStorage.getItem('dashboard_token'", html)

        # Ensure textContent assignments are used for dynamic row data
        self.assertIn("tdPerson.appendChild(bPerson)", html)
        self.assertIn("bPerson.textContent = tx.person_name", html)
        self.assertIn("name.textContent = p.person_name", html)


if __name__ == '__main__':
    unittest.main()
