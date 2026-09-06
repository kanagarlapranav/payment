from telegram import Update
from telegram.ext import ContextTypes
from config import TELEGRAM_USER_ID, TELEGRAM_GROUP_ID, DATA_DIR, logger
from database.queries import (
    get_balance_setting, get_recent_transactions, update_balance_setting,
    get_transaction_by_id, update_transaction, delete_transaction,
    search_transactions, get_monthly_summary
)
from services.balance_service import get_today_summary, recalculate_all_balances
from services.export_service import generate_excel_report
from utils.currency import format_currency, parse_amount
from utils.dates import parse_date, get_current_time_in_tz, format_display_date
import os

async def is_authorized(update: Update) -> bool:
    """Checks if the user or group is authorized to use the bot."""
    user_id = str(update.effective_user.id) if update.effective_user else ""
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    
    cfg_user = str(TELEGRAM_USER_ID).strip() if TELEGRAM_USER_ID else ""
    cfg_group = str(TELEGRAM_GROUP_ID).strip() if TELEGRAM_GROUP_ID else ""
    
    if user_id == cfg_user or (cfg_group and chat_id == cfg_group):
        return True
        
    if update.effective_message and update.effective_chat and update.effective_chat.type == 'private':
        await update.effective_message.reply_text("❌ Unauthorized user.")
    return False

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a welcome message and instructions."""
    if not await is_authorized(update): return
    
    welcome_text = (
        "👋 Welcome to the Personal Payment Tracker!\n\n"
        "Send screenshots or text messages to track expenses automatically.\n\n"
        "📊 *Main Commands:*\n"
        "/balance - Current balance & today's summary\n"
        "/history - Clean list of recent transactions\n"
        "/date <date> - Transactions on a specific date\n"
        "/search <name> - Search by person or keyword\n"
        "/filter - Interactive filtering & sorting menu\n"
        "/monthly - Monthly spending & savings analytics\n"
        "/details - View transactions with IDs & full details\n\n"
        "⚙️ *Management:*\n"
        "/edit - Interactively edit a transaction\n"
        "/delete - Interactively delete a transaction\n"
        "/setbalance <amt> - Set starting balance\n"
        "/export - Download full Excel sheet"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def chatid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Returns the chat ID for group configuration."""
    chat_id = update.message.chat_id
    await update.message.reply_text(f"This chat's ID is: `{chat_id}`", parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    await start_command(update, context)

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    
    balance = get_balance_setting()
    summary = get_today_summary()
    
    text = (
        f"💰 *Current Balance*\n"
        f"`{format_currency(balance)}`\n\n"
        f"Today's Summary:\n"
        f"Money Sent: {format_currency(summary.total_sent)}\n"
        f"Money Received: {format_currency(summary.total_received)}\n"
        f"Net: {format_currency(summary.net_change)}\n\n"
        f"Total Transactions Today: {summary.transaction_count}"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    await balance_command(update, context)


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    
    # If user asks for IDs explicitly e.g. /history ids or /history details
    if context.args and context.args[0].lower() in ('ids', 'id', 'details', 'full'):
        await details_command(update, context)
        return
        
    transactions = get_recent_transactions(limit=10)
    if not transactions:
        await update.message.reply_text("No recent transactions found.")
        return
        
    text = "📜 *Recent Transactions*\n\n"
    for t in transactions:
        date_str = format_display_date(t['transaction_date'])
        time_str = f" | ⏰ {t['transaction_time']}" if t['transaction_time'] and t['transaction_time'] != 'Unknown Time' else ""
        person = t['person_name'] or "Unknown"
        type_badge = "🔴 SENT" if t['transaction_type'] == 'SENT' else "🟢 RECEIVED"
        
        text += (
            f"• *{date_str}*{time_str} | {type_badge}\n"
            f"👤 {person}\n"
            f"💵 {format_currency(t['amount'])}\n"
            f"Balance: {format_currency(t['balance_after'])}\n\n"
        )
        
    await update.message.reply_text(text, parse_mode='Markdown')

async def details_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays transactions WITH IDs and full technical details on demand."""
    if not await is_authorized(update): return
    
    transactions = get_recent_transactions(limit=10)
    if not transactions:
        await update.message.reply_text("No recent transactions found.")
        return
        
    text = "🔍 *Detailed Transactions (With IDs)*\n\n"
    for t in transactions:
        date_str = format_display_date(t['transaction_date'])
        time_str = f" {t['transaction_time']}" if t['transaction_time'] and t['transaction_time'] != 'N/A' else ""
        person = t['person_name'] or "Unknown"
        ref = t['reference_number'] or "N/A"
        bank = t['bank_name'] or "N/A"
        type_badge = "🔴 SENT" if t['transaction_type'] == 'SENT' else "🟢 RECEIVED"
        
        text += (
            f"🆔 *ID: #{t['id']}* | {type_badge}\n"
            f"Date: {date_str}{time_str}\n"
            f"👤 Person: {person}\n"
            f"💵 Amount: {format_currency(t['amount'])}\n"
            f"🏦 Bank: {bank}\n"
            f"🔢 Ref/UTR: `{ref}`\n"
            f"💰 Balance: {format_currency(t['balance_after'])}\n\n"
        )
    await update.message.reply_text(text, parse_mode='Markdown')

async def date_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows transactions for a specific date e.g. /date 05/09/2026 or /date yesterday."""
    if not await is_authorized(update): return
    
    if not context.args:
        await update.message.reply_text(
            "🗓 *Date Search:*\n"
            "Usage: `/date <date>`\n\n"
            "Examples:\n"
            "• `/date yesterday`\n"
            "• `/date today`\n"
            "• `/date 05/09/2026`\n"
            "• `/date 31 Aug 2026`",
            parse_mode='Markdown'
        )
        return
        
    raw_date = " ".join(context.args).strip()
    target_d = parse_date(raw_date)
    if not target_d:
        await update.message.reply_text("❌ Could not parse date. Example: `/date 05/09/2026` or `/date yesterday`", parse_mode='Markdown')
        return
        
    txs = search_transactions(target_date=target_d, sort_by="date_desc")
    if not txs:
        await update.message.reply_text(f"No transactions found on *{target_d.strftime('%d %b %Y')}*.", parse_mode='Markdown')
        return
        
    total_sent = sum(t['amount'] for t in txs if t['transaction_type'] == 'SENT')
    total_recv = sum(t['amount'] for t in txs if t['transaction_type'] == 'RECEIVED')
    
    text = (
        f"• *Transactions on {target_d.strftime('%d %b %Y')}*\n"
        f"Total Sent: {format_currency(total_sent)} | Received: {format_currency(total_recv)}\n\n"
    )
    
    for t in txs:
        time_str = f" | ⏰ {t['transaction_time']}" if t['transaction_time'] and t['transaction_time'] != 'N/A' else ""
        person = t['person_name'] or "Unknown"
        type_badge = "🔴 SENT" if t['transaction_type'] == 'SENT' else "🟢 RECEIVED"
        text += (
            f"• {t['transaction_type']}{time_str}\n"
            f"👤 {person}\n"
            f"💵 {format_currency(t['amount'])}\n"
            f"Balance: {format_currency(t['balance_after'])}\n\n"
        )
        
    await update.message.reply_text(text, parse_mode='Markdown')

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Searches transactions by person name, reference, or keyword."""
    if not await is_authorized(update): return
    
    if not context.args:
        await update.message.reply_text(
            "🔍 *Search Transactions:*\n"
            "Usage: `/search <name or keyword>`\n\n"
            "Examples:\n"
            "• `/search Balaji`\n"
            "• `/search ICICI`\n"
            "• `/search 61322762`",
            parse_mode='Markdown'
        )
        return
        
    query_text = " ".join(context.args).strip()
    txs = search_transactions(query_text=query_text, limit=15)
    
    if not txs:
        await update.message.reply_text(f"🔍 No transactions found matching *'{query_text}'*.", parse_mode='Markdown')
        return
        
    total_amount = sum(t['amount'] for t in txs)
    text = f"🔍 *Found {len(txs)} transactions for '{query_text}'* (Total: {format_currency(total_amount)})\n\n"
    
    for t in txs:
        date_str = format_display_date(t['transaction_date'])
        time_str = f" | ⏰ {t['transaction_time']}" if t['transaction_time'] and t['transaction_time'] != 'Unknown Time' else ""
        person = t['person_name'] or "Unknown"
        type_badge = "🔴 SENT" if t['transaction_type'] == 'SENT' else "🟢 RECEIVED"
        text += (
            f"• *{date_str}*{time_str} | {type_badge}\n"
            f"👤 {person}\n"
            f"💵 {format_currency(t['amount'])}\n"
            f"Balance: {format_currency(t['balance_after'])}\n\n"
        )
    await update.message.reply_text(text, parse_mode='Markdown')


async def monthly_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows analytics and spending summary for the current or specified month."""
    if not await is_authorized(update): return
    
    now = get_current_time_in_tz()
    year = now.year
    month = now.month
    
    if context.args:
        # e.g. /monthly 8 2026 or /monthly Aug
        arg = context.args[0]
        if arg.isdigit() and 1 <= int(arg) <= 12:
            month = int(arg)
        if len(context.args) > 1 and context.args[1].isdigit():
            year = int(context.args[1])
            
    stats = get_monthly_summary(year, month)
    from datetime import date
    month_name = date(year, month, 1).strftime("%B %Y")
    
    top_p_text = "N/A"
    if stats['top_recipient']:
        top_p_text = f"{stats['top_recipient']['person_name']} ({format_currency(stats['top_recipient']['total'])})"
        
    text = (
        f"📊 *Monthly Analytics - {month_name}*\n\n"
        f"🔴 Total Sent: {format_currency(stats['total_sent'])}\n"
        f"🟢 Total Received: {format_currency(stats['total_received'])}\n"
        f"📈 Net Flow: {format_currency(stats['net_savings'])}\n\n"
        f"🔢 Total Transactions: {stats['tx_count']}\n"
        f"🏆 Top Recipient: {top_p_text}"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def filter_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Opens the interactive filter menu."""
    if not await is_authorized(update): return
    from bot.keyboards import get_filter_keyboard
    await update.message.reply_text("🎛️ *Filter & Sort Transactions:*\n\nChoose an option below:", reply_markup=get_filter_keyboard(), parse_mode='Markdown')

async def sort_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Opens sorting options."""
    if not await is_authorized(update): return
    from bot.keyboards import get_sort_keyboard
    await update.message.reply_text("🔀 *Choose Sorting Order:*", reply_markup=get_sort_keyboard(), parse_mode='Markdown')

async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initiates interactive editing or applies direct edit command."""
    if not await is_authorized(update): return
    from bot.keyboards import get_edit_fields_keyboard, get_transaction_selection_keyboard
    
    # 1. No arguments: show list of recent transactions to tap on
    if not context.args:
        transactions = get_recent_transactions(limit=6)
        if not transactions:
            await update.message.reply_text("No transactions found to edit.")
            return
        context.user_data['action'] = 'waiting_edit_id'
        await update.message.reply_text(
            "✏️ *Edit Transaction*\n\n"
            "Select a transaction from the list below, or send its ID number:",
            reply_markup=get_transaction_selection_keyboard(transactions, 'select_edit'),
            parse_mode='Markdown'
        )
        return
        
    try:
        tx_id = int(context.args[0].replace('#', ''))
    except ValueError:
        await update.message.reply_text("❌ Invalid ID format. Example: `/edit 3`", parse_mode='Markdown')
        return
        
    tx = get_transaction_by_id(tx_id)
    if not tx:
        await update.message.reply_text(f"❌ Transaction not found.")
        return
        
    # 2. Only ID provided: show edit field buttons
    if len(context.args) == 1:
        date_str = tx['transaction_date'] or "Today"
        person = tx['person_name'] or "Unknown"
        text = (
            f"✏️ *Editing Transaction*\n\n"
            f"Type: {tx['transaction_type']}\n"
            f"Amount: {format_currency(tx['amount'])}\n"
            f"Person: {person}\n"
            f"Date: {date_str}\n\n"
            "Select what you would like to edit:"
        )
        await update.message.reply_text(text, reply_markup=get_edit_fields_keyboard(tx_id), parse_mode='Markdown')
        return

    # 3. Full command provided: /edit <id> <field> <value>
    field = context.args[1].lower()
    value_raw = " ".join(context.args[2:]).strip()
    
    updates = {}
    needs_recalc = False
    
    if field in ('amount', 'amt'):
        new_amt = parse_amount(value_raw)
        if new_amt <= 0:
            await update.message.reply_text("❌ Invalid amount.")
            return
        updates['amount'] = new_amt
        needs_recalc = True
    elif field in ('person', 'person_name', 'name', 'recipient', 'sender'):
        updates['person_name'] = value_raw.title()
        if tx['transaction_type'] == 'SENT':
            updates['recipient_name'] = value_raw.title()
        else:
            updates['sender_name'] = value_raw.title()
    elif field in ('type', 'transaction_type'):
        new_type = value_raw.upper()
        if new_type not in ('SENT', 'RECEIVED'):
            await update.message.reply_text("❌ Type must be `SENT` or `RECEIVED`.", parse_mode='Markdown')
            return
        updates['transaction_type'] = new_type
        needs_recalc = True
    elif field in ('date', 'transaction_date'):
        parsed_d = parse_date(value_raw)
        if not parsed_d:
            await update.message.reply_text("❌ Invalid date format. Use `DD/MM/YYYY`, `05 Sep 2026`, or `yesterday`.")
            return
        updates['transaction_date'] = parsed_d
    elif field in ('ref', 'reference', 'utr', 'reference_number'):
        updates['reference_number'] = value_raw
    else:
        await update.message.reply_text(f"❌ Unknown field `{field}`. Supported: `amount`, `person`, `type`, `date`, `ref`.", parse_mode='Markdown')
        return
        
    success = update_transaction(tx_id, updates)
    if success:
        bal_msg = ""
        if needs_recalc:
            new_bal = recalculate_all_balances()
            bal_msg = f"\n💰 Updated Current Balance: {format_currency(new_bal)}"
        await update.message.reply_text(f"✅ Transaction updated successfully!{bal_msg}", parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ Failed to update transaction.")

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initiates interactive deletion or prompts for ID."""
    if not await is_authorized(update): return
    from bot.keyboards import get_delete_confirm_keyboard, get_transaction_selection_keyboard
    
    # 1. No arguments: show list of recent transactions to tap on
    if not context.args:
        transactions = get_recent_transactions(limit=6)
        if not transactions:
            await update.message.reply_text("No transactions found to delete.")
            return
        context.user_data['action'] = 'waiting_delete_id'
        await update.message.reply_text(
            "🗑️ *Delete Transaction*\n\n"
            "Select a transaction from the list below, or send its ID number:",
            reply_markup=get_transaction_selection_keyboard(transactions, 'select_delete'),
            parse_mode='Markdown'
        )
        return
        
    try:
        tx_id = int(context.args[0].replace('#', ''))
    except ValueError:
        await update.message.reply_text("❌ Invalid ID format. Example: `/delete 3`", parse_mode='Markdown')
        return
        
    tx = get_transaction_by_id(tx_id)
    if not tx:
        await update.message.reply_text(f"❌ Transaction not found.")
        return
        
    # Show confirmation keyboard
    date_str = tx['transaction_date'] or "Today"
    person = tx['person_name'] or "Unknown"
    text = (
        f"🗑️ *Delete Transaction*\n\n"
        f"Type: {tx['transaction_type']}\n"
        f"Amount: {format_currency(tx['amount'])}\n"
        f"Person: {person}\n"
        f"Date: {date_str}\n\n"
        "Are you sure you want to delete this transaction?"
    )
    await update.message.reply_text(text, reply_markup=get_delete_confirm_keyboard(tx_id), parse_mode='Markdown')

async def setbalance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    
    if not context.args:
        await update.message.reply_text("Usage: /setbalance <amount>")
        return
        
    try:
        new_balance = float(context.args[0].replace(',', ''))
        update_balance_setting(new_balance)
        await update.message.reply_text(f"✅ Balance set to {format_currency(new_balance)}")
    except ValueError:
        await update.message.reply_text("❌ Invalid amount format. Example: /setbalance 50000")

async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    
    await update.message.reply_text("Generating Excel report...")
    export_path = DATA_DIR / "transactions_export.xlsx"
    
    try:
        generate_excel_report(str(export_path))
        with open(export_path, 'rb') as f:
            await update.message.reply_document(
                document=f,
                filename="transactions_export.xlsx",
                caption="📊 Here is your transactions export."
            )
        # Cleanup
        os.remove(export_path)
    except Exception as e:
        logger.error(f"Export error: {e}")
        await update.message.reply_text("❌ Failed to generate export.")
