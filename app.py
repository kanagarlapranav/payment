import logging
import os
import time
import asyncio
import json
import urllib.parse
import hmac
import html
import threading
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, logger, BASE_DIR, DASHBOARD_TOKEN
from database.db import setup_database
from telegram.request import HTTPXRequest
from bot.commands import (
    start_command, balance_command, today_command, history_command, last5_command,
    edit_command, delete_command, date_command, search_command,
    monthly_command, filter_command, sort_command, details_command,
    setbalance_command, export_command, help_command, chatid_command, amount_command,
    insights_command, budget_command, setbudget_command, digest_command, dashboard_command, menu_command,
    cafestats_command, cafeedit_command, addmenu_command, delmenu_command, restore_command, undo_command,
    geministatus_command
)
from bot.handlers import handle_image, handle_callback_query, handle_text, handle_document
from services.scheduler_service import register_scheduler_jobs

async def on_startup(app):
    """
    Startup rule:
    Restore automatically ONLY when the transactions table has zero rows including tombstones
    AND the database is not marked initialized. Otherwise log 'restore skipped' with counts.
    If restore fails on an empty database, set backup_blocked=true: no backup may be uploaded
    until a restore succeeds or the first real transaction is added. Log which path was taken.
    """
    try:
        from services.backup_service import restore_from_telegram, restore_local_fallback_if_valid, export_database_to_json, BACKUP_JSON_PATH
        from database.db import get_db_connection, LEDGER_LOCK
        from utils.dates import utc_now_iso

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM transactions")
            total_rows = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM transactions WHERE deleted_at IS NULL")
            live_rows = cur.fetchone()[0]
            tombstones = total_rows - live_rows

            cur.execute("SELECT value FROM settings WHERE key = 'database_initialized'")
            row = cur.fetchone()
            is_initialized = bool(row and row['value'] in ('1', 'true', 'True'))

        if total_rows == 0 and not is_initialized:
            logger.info("Startup check: [EMPTY & UNINITIALIZED] - Attempting automatic restore...")
            # Path 1: Cloud restore from Telegram
            restored = await restore_from_telegram(app.bot)
            if restored:
                logger.info("Startup path taken: [CLOUD RESTORE SUCCESS] - Database restored successfully from Telegram cloud backup.")
                with LEDGER_LOCK:
                    with get_db_connection() as conn:
                        now_utc = utc_now_iso()
                        conn.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('database_initialized', '1', ?)", (now_utc,))
                        conn.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('backup_blocked', '0', ?)", (now_utc,))
                        conn.commit()
                export_database_to_json()
            elif restore_local_fallback_if_valid():
                # Path 2: Local fallback with valid checksum
                logger.info("Startup path taken: [LOCAL RESTORE SUCCESS] - Restored from valid local JSON backup file.")
                with LEDGER_LOCK:
                    with get_db_connection() as conn:
                        now_utc = utc_now_iso()
                        conn.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('database_initialized', '1', ?)", (now_utc,))
                        conn.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('backup_blocked', '0', ?)", (now_utc,))
                        conn.commit()
            else:
                # Path 3: Restore failed on empty database -> set backup_blocked=true
                logger.warning("Startup path taken: [RESTORE FAILED ON EMPTY DB] - No valid cloud or local backup available. Setting backup_blocked=true.")
                with LEDGER_LOCK:
                    with get_db_connection() as conn:
                        now_utc = utc_now_iso()
                        conn.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('backup_blocked', '1', ?)", (now_utc,))
                        conn.commit()
        else:
            logger.info(f"Startup check: restore skipped (total={total_rows}, live={live_rows}, tombstones={tombstones}, initialized={is_initialized})")

        # Clean up any leftover temporary images from prior runs
        try:
            from config import IMAGE_DIR
            for f in os.listdir(IMAGE_DIR):
                if f != '.gitkeep':
                    p = os.path.join(IMAGE_DIR, f)
                    if os.path.isfile(p):
                        os.remove(p)
            logger.info("Cleaned up any stray temporary images.")
        except Exception as e:
            logger.debug(f"Image cleanup notice: {e}")

    except Exception as e:
        logger.error(f"Critical error during startup restore and initialization: {e}", exc_info=True)
        # Block backup if startup failed critically so we don't upload a corrupted/uninitialized database
        try:
            from database.db import get_db_connection, LEDGER_LOCK
            from utils.dates import utc_now_iso
            with LEDGER_LOCK:
                with get_db_connection() as conn:
                    now_utc = utc_now_iso()
                    conn.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('backup_blocked', '1', ?)", (now_utc,))
                    conn.commit()
        except Exception as set_err:
            logger.error(f"Failed to set backup_blocked flag during startup failure: {set_err}")

async def on_stop(app):
    """Executes graceful final backup before HTTP client closes, only if dirty, with 10s timeout."""
    try:
        from database.db import get_db_connection
        is_dirty = False
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM settings WHERE key = 'is_dirty'")
            row = cur.fetchone()
            if row and row['value'] == '1':
                is_dirty = True

        if not is_dirty:
            logger.info("Shutdown hook (post_stop): database is clean. Skipping final cloud backup.")
            return

        from services.backup_service import backup_to_telegram, export_database_to_json
        logger.info("Shutdown hook (post_stop): database is dirty. Executing final database backup before HTTP client closes (10s timeout)...")
        export_database_to_json()
        await backup_to_telegram(app.bot, timeout=10.0)
        logger.info("Shutdown hook: final backup completed.")
    except Exception as e:
        logger.warning(f"Shutdown backup notice: {e}")

def build_application():
    """Builds and configures the Telegram Application."""
    setup_database()
    req = HTTPXRequest(read_timeout=60.0, write_timeout=60.0, connect_timeout=30.0, pool_timeout=60.0)
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).request(req).post_init(on_startup).post_stop(on_stop).build()

    # Register PTB JobQueue background jobs (Daily Digest, Backup Retry, Tombstone Purge)
    register_scheduler_jobs(app)

    # Core commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("last5", last5_command))
    app.add_handler(CommandHandler("recent", last5_command))
    app.add_handler(CommandHandler("details", details_command))
    app.add_handler(CommandHandler("ids", details_command))
    app.add_handler(CommandHandler("date", date_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("find", search_command))
    app.add_handler(CommandHandler("amount", amount_command))
    app.add_handler(CommandHandler("amt", amount_command))
    app.add_handler(CommandHandler("monthly", monthly_command))
    app.add_handler(CommandHandler("stats", monthly_command))
    app.add_handler(CommandHandler("filter", filter_command))
    app.add_handler(CommandHandler("sort", sort_command))
    app.add_handler(CommandHandler("edit", edit_command))
    app.add_handler(CommandHandler("delete", delete_command))
    app.add_handler(CommandHandler("setbalance", setbalance_command))
    app.add_handler(CommandHandler("export", export_command))
    app.add_handler(CommandHandler("report", export_command))
    app.add_handler(CommandHandler("statement", export_command))
    app.add_handler(CommandHandler("chatid", chatid_command))
    app.add_handler(CommandHandler("help", help_command))

    # Advanced AI, Analytics & Cafeteria commands
    app.add_handler(CommandHandler("insights", insights_command))
    app.add_handler(CommandHandler("budget", budget_command))
    app.add_handler(CommandHandler("setbudget", setbudget_command))
    app.add_handler(CommandHandler("digest", digest_command))
    app.add_handler(CommandHandler("dashboard", dashboard_command))
    app.add_handler(CommandHandler(["gemini", "geministatus", "quota", "ai", "status"], geministatus_command))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("cafeteria", menu_command))
    app.add_handler(CommandHandler("cafestats", cafestats_command))
    app.add_handler(CommandHandler("cafespends", cafestats_command))
    app.add_handler(CommandHandler("cafeedit", cafeedit_command))
    app.add_handler(CommandHandler("editcafe", cafeedit_command))
    app.add_handler(CommandHandler("addmenu", addmenu_command))
    app.add_handler(CommandHandler("delmenu", delmenu_command))
    app.add_handler(CommandHandler("restore", restore_command))
    app.add_handler(CommandHandler("importbackup", restore_command))
    app.add_handler(CommandHandler("undo", undo_command))
    app.add_handler(CommandHandler("revert", undo_command))

    # Image handler (photos and documents)
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_image))

    # Backup document handler (JSON files)
    app.add_handler(MessageHandler(filters.Document.ALL & ~filters.Document.IMAGE, handle_document))

    # Text message handler (non-command messages)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Callback query handler for inline keyboards (Confirm / Cancel)
    app.add_handler(CallbackQueryHandler(handle_callback_query))

    return app

class WebAppAndHealthHandler(BaseHTTPRequestHandler):
    """Serves keep-alive health checks, one-time auth code exchange, web dashboard, and protected API endpoints."""
    
    def _send_security_headers(self, status_code: int, content_type: str = "text/plain; charset=utf-8", is_api: bool = False, extra_headers: dict = None):
        from services.dashboard_auth import get_security_headers
        self.send_response(status_code)
        self.send_header('Content-type', content_type)
        for k, v in get_security_headers(is_api=is_api).items():
            self.send_header(k, v)
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()

    def do_HEAD(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path in ('/healthz', '/health', '/', '/dashboard'):
            self._send_security_headers(200, 'text/plain; charset=utf-8')
        else:
            self._send_security_headers(404, 'text/plain; charset=utf-8')

    def do_GET(self):
        from services.dashboard_auth import exchange_code_for_session, validate_session, is_rate_limited
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query_params = urllib.parse.parse_qs(parsed.query)

        # 1. Lightweight health check endpoint for uptime monitors
        if path in ('/healthz', '/health'):
            self._send_security_headers(200, 'text/plain; charset=utf-8')
            self.wfile.write(b"OK")
            return

        # Extract client IP and protocol
        client_ip = self.headers.get('X-Forwarded-For', '').split(',')[0].strip() or self.client_address[0]
        is_https = self.headers.get('X-Forwarded-Proto', '').lower() == 'https'

        # 2. One-Time Code Auth Exchange: /auth?code=...
        if path == '/auth':
            code = query_params.get("code", [""])[0].strip()
            success, result_msg, cookie_header = exchange_code_for_session(code, client_ip=client_ip, is_https=is_https)

            if success:
                # Redirect to /dashboard with code removed from URL and HttpOnly session cookie set
                extra = {
                    'Location': '/dashboard',
                    'Set-Cookie': cookie_header
                }
                self._send_security_headers(303, 'text/html; charset=utf-8', extra_headers=extra)
                self.wfile.write(b"Redirecting to dashboard...")
                return
            else:
                # Auth failed or rate-limited
                status_code = 429 if "Too many failed" in result_msg else 401
                self._send_security_headers(status_code, 'text/html; charset=utf-8')
                error_html = (
                    f"<!DOCTYPE html><html><head><title>Access Denied</title>"
                    f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
                    f"<style>body{{font-family:sans-serif;background:#0f172a;color:#f8fafc;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;padding:20px;text-align:center;}}"
                    f".card{{background:#1e293b;padding:30px;border-radius:12px;max-width:400px;border:1px solid #334155;}}"
                    f"h2{{color:#ef4444;margin-top:0;}}p{{color:#94a3b8;line-height:1.5;}}code{{background:#0f172a;padding:4px 8px;border-radius:4px;color:#38bdf8;}}</style></head>"
                    f"<body><div class='card'><h2>🔒 Access Denied</h2><p>{html.escape(str(result_msg))}</p><p>Please run <code>/dashboard</code> in Telegram to generate a fresh 60-second login link.</p></div></body></html>"
                )
                self.wfile.write(error_html.encode('utf-8'))
                return

        # Check session cookie for protected dashboard and API routes
        cookie_header = self.headers.get("Cookie", "")
        has_session = validate_session(cookie_header)

        # 3. Web dashboard frontend UI
        if path in ('/dashboard', '/'):
            if not has_session:
                self._send_security_headers(401, 'text/html; charset=utf-8')
                unauth_html = (
                    f"<!DOCTYPE html><html><head><title>Authentication Required</title>"
                    f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
                    f"<style>body{{font-family:sans-serif;background:#0f172a;color:#f8fafc;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;padding:20px;text-align:center;}}"
                    f".card{{background:#1e293b;padding:30px;border-radius:12px;max-width:400px;border:1px solid #334155;}}"
                    f"h2{{color:#f59e0b;margin-top:0;}}p{{color:#94a3b8;line-height:1.5;}}code{{background:#0f172a;padding:4px 8px;border-radius:4px;color:#38bdf8;}}</style></head>"
                    f"<body><div class='card'><h2>🔒 Authentication Required</h2><p>Your session has expired or you are not logged in.</p><p>Please send <code>/dashboard</code> in Telegram to receive a secure single-use login link.</p></div></body></html>"
                )
                self.wfile.write(unauth_html.encode('utf-8'))
                return

            template_path = BASE_DIR / 'web' / 'templates' / 'dashboard.html'
            if os.path.exists(template_path):
                with open(template_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                self._send_security_headers(200, 'text/html; charset=utf-8')
                self.wfile.write(content.encode('utf-8'))
            else:
                self._send_security_headers(200, 'text/plain')
                self.wfile.write(b"Payment Tracker Bot is Running 24/7 OK")
            return

        # 4. Protected Dashboard Data API
        elif path == '/api/data':
            if not has_session:
                self._send_security_headers(401, 'application/json', is_api=True)
                self.wfile.write(json.dumps({"error": "Unauthorized. Valid session required. Run /dashboard in Telegram."}).encode('utf-8'))
                return

            from database.queries import (
                get_balance_setting, get_monthly_summary, get_category_summary,
                get_recent_transactions, get_all_transactions, get_month_comparison_stats,
                get_daily_spend_series, get_top_payees, get_transactions_paginated
            )
            from services.budget_service import get_budget_info

            now = datetime.now()
            if "year" in query_params:
                try:
                    year = int(query_params.get("year", [""])[0])
                    if not (1900 <= year <= 2100):
                        raise ValueError("Year out of range")
                except (ValueError, TypeError):
                    self._send_security_headers(400, 'application/json', is_api=True)
                    self.wfile.write(json.dumps({"error": "Invalid year parameter. Must be an integer between 1900 and 2100."}).encode('utf-8'))
                    return
            else:
                year = now.year

            if "month" in query_params:
                try:
                    month = int(query_params.get("month", [""])[0])
                    if not (1 <= month <= 12):
                        raise ValueError("Month out of range")
                except (ValueError, TypeError):
                    self._send_security_headers(400, 'application/json', is_api=True)
                    self.wfile.write(json.dumps({"error": "Invalid month parameter. Must be an integer between 1 and 12."}).encode('utf-8'))
                    return
            else:
                month = now.month

            balance = get_balance_setting()
            monthly = get_monthly_summary(year, month)
            cat_summary = get_category_summary(year, month)
            budget_data = get_budget_info(year, month)
            comp_stats = get_month_comparison_stats(year, month)
            daily_series = get_daily_spend_series(year, month)
            top_payees = get_top_payees(limit=5, year=year, month=month)
            txs_data = get_transactions_paginated(page=1, page_size=100, year=year, month=month)
            all_txs = get_all_transactions()

            month_names = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
            period_str = f"{month_names[month]} {year}"

            # Spending categories with percentage
            sent_categories = []
            total_spent_val = float(monthly['total_sent'] or 0.0)
            for c in cat_summary:
                if c['transaction_type'] == 'SENT':
                    amt = float(c['total_amount'])
                    pct = (amt / total_spent_val * 100) if total_spent_val > 0 else 0.0
                    sent_categories.append({
                        'category': c['category'] or 'General',
                        'amount': amt,
                        'percentage': round(pct, 1),
                        'count': int(c['count'])
                    })

            from database.db import get_db_connection
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT key, value FROM settings WHERE key IN ('backup_revision', 'last_local_backup_at', 'last_telegram_backup_at', 'is_dirty')")
                b_settings = {r['key']: r['value'] for r in cur.fetchall()}

            backup_status = {
                'revision': int(b_settings.get('backup_revision', '1')),
                'last_local_backup_at': b_settings.get('last_local_backup_at') or 'Never',
                'last_telegram_backup_at': b_settings.get('last_telegram_backup_at') or 'Never',
                'is_dirty': b_settings.get('is_dirty', '0') == '1'
            }

            payload = {
                'year': year,
                'month': month,
                'period_name': period_str,
                'current_balance': balance,
                'total_received': monthly['total_received'],
                'total_spent': monthly['total_sent'],
                'net_savings': monthly['net_savings'],
                'total_transactions': len(all_txs),
                'month_transactions_count': txs_data['total_count'],
                'comparison': comp_stats,
                'budget_info': budget_data,
                'categories': sent_categories,
                'daily_series': daily_series,
                'top_payees': top_payees,
                'recent_transactions': txs_data['transactions'],
                'backup_status': backup_status
            }

            self._send_security_headers(200, 'application/json', is_api=True)
            self.wfile.write(json.dumps(payload, default=str).encode('utf-8'))
            return

        # 5. Protected Transactions List API
        elif path == '/api/transactions':
            if not has_session:
                self._send_security_headers(401, 'application/json', is_api=True)
                self.wfile.write(json.dumps({"error": "Unauthorized. Valid session required. Run /dashboard in Telegram."}).encode('utf-8'))
                return

            from database.queries import get_transactions_paginated

            try:
                page_raw = query_params.get("page", ["1"])[0]
                page = int(page_raw)
                if page < 1:
                    raise ValueError("Page must be >= 1")
            except (ValueError, TypeError):
                self._send_security_headers(400, 'application/json', is_api=True)
                self.wfile.write(json.dumps({"error": "Invalid page parameter. Must be an integer >= 1."}).encode('utf-8'))
                return

            try:
                page_size_raw = query_params.get("page_size", ["50"])[0]
                page_size = int(page_size_raw)
                if not (1 <= page_size <= 200):
                    raise ValueError("Page size must be between 1 and 200")
            except (ValueError, TypeError):
                self._send_security_headers(400, 'application/json', is_api=True)
                self.wfile.write(json.dumps({"error": "Invalid page_size parameter. Must be an integer between 1 and 200."}).encode('utf-8'))
                return

            year, month = None, None
            if "year" in query_params:
                try:
                    year = int(query_params.get("year", [""])[0])
                    if not (1900 <= year <= 2100):
                        raise ValueError("Year out of range")
                except (ValueError, TypeError):
                    self._send_security_headers(400, 'application/json', is_api=True)
                    self.wfile.write(json.dumps({"error": "Invalid year parameter. Must be an integer between 1900 and 2100."}).encode('utf-8'))
                    return

            if "month" in query_params:
                try:
                    month = int(query_params.get("month", [""])[0])
                    if not (1 <= month <= 12):
                        raise ValueError("Month out of range")
                except (ValueError, TypeError):
                    self._send_security_headers(400, 'application/json', is_api=True)
                    self.wfile.write(json.dumps({"error": "Invalid month parameter. Must be an integer between 1 and 12."}).encode('utf-8'))
                    return

            search = query_params.get("search", [""])[0].strip() or None
            tx_type = query_params.get("type", [""])[0].strip() or None
            if tx_type and tx_type.upper() not in ('SENT', 'RECEIVED', 'TRANSFER', 'ALL'):
                self._send_security_headers(400, 'application/json', is_api=True)
                self.wfile.write(json.dumps({"error": "Invalid type parameter. Must be SENT, RECEIVED, TRANSFER, or ALL."}).encode('utf-8'))
                return

            try:
                data = get_transactions_paginated(page=page, page_size=page_size, search=search, tx_type=tx_type, year=year, month=month)
                self._send_security_headers(200, 'application/json', is_api=True)
                self.wfile.write(json.dumps(data, default=str).encode('utf-8'))
            except Exception as query_err:
                logger.error(f"Error querying paginated transactions: {query_err}", exc_info=True)
                self._send_security_headers(500, 'application/json', is_api=True)
                self.wfile.write(json.dumps({"error": "Internal server error fetching transactions."}).encode('utf-8'))
            return

        # 6. Protected CSV Export API
        elif path == '/api/export.csv':
            if not has_session:
                self._send_security_headers(401, 'text/plain; charset=utf-8', is_api=True)
                self.wfile.write(b"Unauthorized. Valid session required.")
                return

            from database.queries import get_all_transactions_asc
            import csv
            import io

            txs = get_all_transactions_asc()
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(['ID', 'Date', 'Time', 'Type', 'Amount (INR)', 'Payee / Person', 'Category', 'Bank / App', 'Reference / UTR', 'Balance After'])
            for t in txs:
                writer.writerow([
                    t['id'],
                    t['transaction_date'],
                    t['transaction_time'] or '',
                    t['transaction_type'],
                    f"{t['amount']:.2f}",
                    t['person_name'] or '',
                    t['category'] or 'General',
                    t['bank_name'] or t['payment_app'] or '',
                    t['reference_number'] or '',
                    f"{t['balance_after']:.2f}"
                ])

            csv_content = output.getvalue().encode('utf-8')
            extra = {'Content-Disposition': 'attachment; filename="payment_tracker_ledger.csv"'}
            self._send_security_headers(200, 'text/csv; charset=utf-8', is_api=True, extra_headers=extra)
            self.wfile.write(csv_content)
            return

        else:
            self._send_security_headers(404, 'text/plain; charset=utf-8')

    def log_message(self, format, *args):
        pass # Suppress HTTP access logs

def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    try:
        server = ThreadingHTTPServer(('0.0.0.0', port), WebAppAndHealthHandler)
        logger.info(f"Threading HTTP server running on port {port}")
        server.serve_forever()
    except Exception as e:
        logger.warning(f"Web/Health server error: {e}")

def main():
    """Main entry point for polling & cloud web service."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Exiting.")
        return

    # Start Threading Web Dashboard & health check server
    threading.Thread(target=start_health_server, daemon=True).start()

    # Pre-warm OCR engine in background to ensure zero cold-start delay for users
    from ocr.engine import warmup_ocr
    threading.Thread(target=warmup_ocr, daemon=True).start()

    logger.info("Initializing Telegram bot...")
    app = build_application()
    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == '__main__':
    main()
