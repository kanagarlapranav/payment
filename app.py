import logging
import os
import time
import json
import urllib.request
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, logger, BASE_DIR
from database.db import setup_database
from telegram.request import HTTPXRequest
from bot.commands import (
    start_command, balance_command, today_command, history_command,
    edit_command, delete_command, date_command, search_command,
    monthly_command, filter_command, sort_command, details_command,
    setbalance_command, export_command, help_command, chatid_command, amount_command,
    insights_command, budget_command, setbudget_command, digest_command, dashboard_command, menu_command,
    cafestats_command, cafeedit_command
)
from bot.handlers import handle_image, handle_callback_query, handle_text
from services.scheduler_service import scheduler

async def on_startup(app):
    """Restores database state from cloud backup if needed on fresh container spins."""
    try:
        from services.backup_service import restore_from_telegram, export_database_to_json
        from services.balance_service import resequence_transaction_ids, recalculate_all_balances
        logger.info("Checking for cloud backup on startup...")
        restored = await restore_from_telegram(app.bot)
        if restored:
            logger.info("Cloud backup restored successfully on startup.")
        
        # Resequence IDs and recalculate balances so database is always clean & sequential
        resequence_transaction_ids()
        recalculate_all_balances()
        export_database_to_json()

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

        # Hook bot instance into background scheduler
        scheduler.set_bot(app.bot)
        scheduler.start()
    except Exception as e:
        logger.warning(f"Startup initialization notice: {e}")

def build_application():
    """Builds and configures the Telegram Application."""
    setup_database()
    req = HTTPXRequest(read_timeout=60.0, write_timeout=60.0, connect_timeout=30.0, pool_timeout=60.0)
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).request(req).post_init(on_startup).build()

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


    # Image handler (photos and documents)
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_image))

    # Text message handler (non-command messages)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Callback query handler for inline keyboards (Confirm / Cancel)
    app.add_handler(CallbackQueryHandler(handle_callback_query))

    return app

class WebAppAndHealthHandler(BaseHTTPRequestHandler):
    """Serves keep-alive health checks, live web dashboard, and API endpoints."""
    def do_GET(self):
        path = self.path.split('?')[0]

        if path in ('/dashboard', '/'):
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

        elif path == '/api/data':
            from database.queries import (
                get_balance_setting, get_monthly_summary, get_category_summary,
                get_recent_transactions, get_all_transactions
            )
            from services.budget_service import get_budget_info

            now = datetime.now()
            balance = get_balance_setting()
            monthly = get_monthly_summary(now.year, now.month)
            cat_summary = get_category_summary(now.year, now.month)
            budget_data = get_budget_info(now.year, now.month)
            recent_txs = get_recent_transactions(limit=25)
            all_txs = get_all_transactions()

            month_names = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
            period_str = f"{month_names[now.month]} {now.year}"

            # Spending categories
            sent_categories = []
            for c in cat_summary:
                if c['transaction_type'] == 'SENT':
                    sent_categories.append({
                        'category': c['category'] or 'General',
                        'amount': float(c['total_amount']),
                        'count': int(c['count'])
                    })

            payload = {
                'period_name': period_str,
                'current_balance': balance,
                'total_received': monthly['total_received'],
                'total_spent': monthly['total_sent'],
                'net_savings': monthly['net_savings'],
                'total_transactions': len(all_txs),
                'budget_info': budget_data,
                'categories': sent_categories,
                'recent_transactions': recent_txs
            }

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(payload, default=str).encode('utf-8'))

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass # Suppress HTTP access logs

def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    try:
        server = HTTPServer(('0.0.0.0', port), WebAppAndHealthHandler)
        server.serve_forever()
    except Exception as e:
        logger.warning(f"Web/Health server error: {e}")

def start_keep_alive():
    """Pings public URL every 10 minutes to prevent Render free instance from spinning down."""
    render_url = os.getenv("RENDER_EXTERNAL_URL", "https://payment-3-kldp.onrender.com")
    time.sleep(30) # Initial warmup delay
    while True:
        try:
            req = urllib.request.Request(render_url, headers={'User-Agent': 'KeepAliveBot/1.0'})
            with urllib.request.urlopen(req, timeout=20) as resp:
                logger.info(f"Keep-alive self ping to {render_url}: status {resp.status}")
        except Exception as e:
            logger.debug(f"Keep-alive ping notice: {e}")
        time.sleep(600) # Ping every 10 minutes

def main():
    """Main entry point for polling & cloud web service."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Exiting.")
        return

    # Start Web Dashboard & keep-alive server so Render stays alive 24/7
    threading.Thread(target=start_health_server, daemon=True).start()
    threading.Thread(target=start_keep_alive, daemon=True).start()

    # Pre-warm OCR engine in background to ensure zero cold-start delay for users
    from ocr.engine import warmup_ocr
    threading.Thread(target=warmup_ocr, daemon=True).start()

    logger.info("Initializing Telegram bot...")
    app = build_application()
    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == '__main__':
    main()

