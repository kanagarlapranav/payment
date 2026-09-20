import logging
import os
import time
import json
import urllib.parse
import hmac
import threading
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, logger, BASE_DIR, DASHBOARD_TOKEN
from database.db import setup_database
from telegram.request import HTTPXRequest
from bot.commands import (
    start_command, balance_command, today_command, history_command,
    edit_command, delete_command, date_command, search_command,
    monthly_command, filter_command, sort_command, details_command,
    setbalance_command, export_command, help_command, chatid_command, amount_command,
    insights_command, budget_command, setbudget_command, digest_command, dashboard_command, menu_command,
    cafestats_command, cafeedit_command, addmenu_command, delmenu_command, restore_command, undo_command
)
from bot.handlers import handle_image, handle_callback_query, handle_text
from services.scheduler_service import scheduler

async def on_startup(app):
    """Restores database state from cloud backup only if database is completely empty on fresh container spins."""
    try:
        from services.backup_service import restore_from_telegram, import_database_from_json, export_database_to_json, BACKUP_JSON_PATH
        from database.db import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM transactions")
            total_rows = cur.fetchone()[0]

        if total_rows > 0:
            logger.info(f"Startup check: [SKIPPED RESTORE] - Local database has {total_rows} records.")
        else:
            logger.info("Startup check: [EMPTY DB] - Fresh container spin detected. Attempting cloud restore from Telegram...")
            restored = await restore_from_telegram(app.bot)
            if restored:
                logger.info("Startup check: [RESTORE SUCCESS] - Database restored successfully from cloud backup.")
                export_database_to_json()
            elif BACKUP_JSON_PATH.exists():
                res = import_database_from_json(BACKUP_JSON_PATH)
                if res.get('success'):
                    logger.info("Startup check: [LOCAL RESTORE] - Restored from local backup file.")
                else:
                    logger.warning("Startup check: [EMPTY & NO BACKUP] - Local backup file present but import failed.")
            else:
                logger.warning("Startup check: [EMPTY & NO BACKUP] - Starting with brand new database.")

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
        logger.warning(f"Startup initialization notice: {e}")

    # Hook bot instance into background scheduler in its own decoupled block
    try:
        scheduler.set_bot(app.bot)
        scheduler.start()
        logger.info("Background scheduler started successfully.")
    except Exception as sched_err:
        logger.error(f"Scheduler startup error: {sched_err}")

async def on_shutdown(app):
    """Executes graceful final backup on SIGTERM or container stop."""
    try:
        from services.backup_service import backup_to_telegram, export_database_to_json
        logger.info("Executing graceful pre-shutdown database backup...")
        export_database_to_json()
        await backup_to_telegram(app.bot)
        logger.info("Pre-shutdown backup completed.")
    except Exception as e:
        logger.warning(f"Shutdown backup notice: {e}")

def build_application():
    """Builds and configures the Telegram Application."""
    setup_database()
    req = HTTPXRequest(read_timeout=60.0, write_timeout=60.0, connect_timeout=30.0, pool_timeout=60.0)
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).request(req).post_init(on_startup).post_shutdown(on_shutdown).build()

    # Core commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("history", history_command))
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

    # Text message handler (non-command messages)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Callback query handler for inline keyboards (Confirm / Cancel)
    app.add_handler(CallbackQueryHandler(handle_callback_query))

    return app

class WebAppAndHealthHandler(BaseHTTPRequestHandler):
    """Serves keep-alive health checks, live web dashboard, and protected API endpoints."""
    
    def do_HEAD(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path in ('/healthz', '/health', '/', '/dashboard'):
            self.send_response(200)
            self.send_header('Content-type', 'text/plain; charset=utf-8')
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query_params = urllib.parse.parse_qs(parsed.query)

        # 1. Lightweight health check endpoint for uptime monitors
        if path in ('/healthz', '/health'):
            self.send_response(200)
            self.send_header('Content-type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(b"OK")
            return

        # 2. Web dashboard frontend UI
        elif path in ('/dashboard', '/'):
            template_path = BASE_DIR / 'web' / 'templates' / 'dashboard.html'
            if os.path.exists(template_path):
                with open(template_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(content.encode('utf-8'))
            else:
                self.send_response(200)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(b"Payment Tracker Bot is Running 24/7 OK")

        # 3. Protected Dashboard Data API
        elif path == '/api/data':
            dash_token = DASHBOARD_TOKEN or os.getenv("DASHBOARD_TOKEN", "")

            supplied_header = self.headers.get("X-Dash-Token", "")
            supplied_query = query_params.get("token", [""])[0]
            supplied = supplied_header or supplied_query

            if not dash_token or not hmac.compare_digest(supplied, dash_token):
                self.send_response(401)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Unauthorized. Provide valid X-Dash-Token or ?token="}).encode('utf-8'))
                return

            from database.queries import (
                get_balance_setting, get_monthly_summary, get_category_summary,
                get_recent_transactions, get_all_transactions, get_month_comparison_stats,
                get_daily_spend_series, get_top_payees, get_transactions_paginated
            )
            from services.budget_service import get_budget_info

            now = datetime.now()
            try:
                year = int(query_params.get("year", [now.year])[0])
            except (ValueError, TypeError):
                year = now.year
                
            try:
                month = int(query_params.get("month", [now.month])[0])
            except (ValueError, TypeError):
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
                'recent_transactions': txs_data['transactions']
            }

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(payload, default=str).encode('utf-8'))

        # 4. CSV Export API
        elif path == '/api/export.csv':
            dash_token = DASHBOARD_TOKEN or os.getenv("DASHBOARD_TOKEN", "")
            supplied_header = self.headers.get("X-Dash-Token", "")
            supplied_query = query_params.get("token", [""])[0]
            supplied = supplied_header or supplied_query

            if not dash_token or not hmac.compare_digest(supplied, dash_token):
                self.send_response(401)
                self.end_headers()
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
            self.send_response(200)
            self.send_header('Content-type', 'text/csv; charset=utf-8')
            self.send_header('Content-Disposition', 'attachment; filename="payment_tracker_ledger.csv"')
            self.end_headers()
            self.wfile.write(csv_content)

        else:
            self.send_response(404)
            self.end_headers()

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
