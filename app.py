import logging
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, logger
from database.db import setup_database
from telegram.request import HTTPXRequest
from bot.commands import (
    start_command, balance_command, today_command, history_command,
    edit_command, delete_command, date_command, search_command,
    monthly_command, filter_command, sort_command, details_command,
    setbalance_command, export_command, help_command, chatid_command
)
from bot.handlers import handle_image, handle_callback_query, handle_text

def main():
    """Main entry point for the bot."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Exiting.")
        return

    logger.info("Setting up database...")
    setup_database()

    logger.info("Initializing Telegram bot...")
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

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == '__main__':
    main()
