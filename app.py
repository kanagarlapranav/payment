import logging
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, logger
from database.db import setup_database
from telegram.request import HTTPXRequest
from bot.commands import (
    start_command, balance_command, today_command, history_command,
    edit_command, delete_command, date_command, search_command,
    monthly_command, filter_command, sort_command, details_command,
    setbalance_command, export_command, help_command, chatid_command, amount_command
)
from bot.handlers import handle_image, handle_callback_query, handle_text

def build_application():
    """Builds and configures the Telegram Application."""
    setup_database()
    req = HTTPXRequest(read_timeout=60.0, write_timeout=60.0, connect_timeout=30.0, pool_timeout=60.0)
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).request(req).build()

    # Command handlers
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
    app.add_handler(CommandHandler("chatid", chatid_command))
    app.add_handler(CommandHandler("help", help_command))


    # Image handler (photos and documents)
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_image))

    # Text message handler (non-command messages)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Callback query handler for inline keyboards (Confirm / Cancel)
    app.add_handler(CallbackQueryHandler(handle_callback_query))

    return app

import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Payment Tracker Bot is Running 24/7 OK")

    def log_message(self, format, *args):
        pass # Suppress HTTP access logs

def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    try:
        server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
        server.serve_forever()
    except Exception as e:
        logger.warning(f"Health server error: {e}")

def main():
    """Main entry point for polling & cloud web service."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Exiting.")
        return

    # Start dummy HTTP server in background thread so Free Cloud Tiers (Render/Koyeb) stay alive for free
    threading.Thread(target=start_health_server, daemon=True).start()

    logger.info("Initializing Telegram bot...")
    app = build_application()
    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == '__main__':
    main()

