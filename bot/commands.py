from telegram import Update
from telegram.ext import ContextTypes
from config import TELEGRAM_USER_ID, TELEGRAM_GROUP_ID, DATA_DIR, logger
from database.queries import (
    get_balance_setting, get_recent_transactions, update_balance_setting,
    get_transaction_by_id, update_transaction, delete_transaction,
    search_transactions, get_monthly_summary, get_all_transactions_asc
)
from services.balance_service import get_today_summary, get_overall_summary, recalculate_all_balances
from services.export_service import generate_excel_report
from utils.currency import format_currency, parse_amount
from utils.dates import parse_date, get_current_time_in_tz, format_display_date
from datetime import datetime
import html
import os
from bot.keyboards import (
    get_home_menu_keyboard, get_history_paginated_keyboard, get_back_to_menu_keyboard,
    get_quick_add_keyboard, get_settings_menu_keyboard
)

def render_home_menu_text() -> str:
    """Generates the main Home Menu dashboard card."""
    now = datetime.now()
    balance = get_balance_setting()
    today_stats = get_today_summary()
    monthly = get_monthly_summary(now.year, now.month)
    
    from services.budget_service import get_budget_info
    b_info = get_budget_info(now.year, now.month)
    
    month_names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    m_name = month_names[now.month]
    
    budget_line = ""
    if b_info.get('budget', 0) > 0:
        budget_line = f"🎯 <b>Budget:</b> <code>{b_info['progress_bar']}</code> {b_info['percentage']:.0f}% (₹{b_info['spent']:,.0f} / ₹{b_info['budget']:,.0f})\n"
        
    net_today = today_stats['net_change']
    net_sign = "+" if net_today >= 0 else "-"
    
    return (
        f"⚡ <b>Payment Tracker Dashboard</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"💰 <b>Balance:</b> <b>{format_currency(balance)}</b>\n"
        f"📅 <b>Today:</b> {net_sign}{format_currency(abs(net_today))} ({today_stats['tx_count']} txs)\n"
        f"🗓️ <b>{m_name} {now.year} Spent:</b> {format_currency(monthly['total_sent'])}\n"
        f"{budget_line}"
        f"━━━━━━━━━━━━━━\n"
        f"<i>Select an option or send a receipt screenshot:</i>"
    )

def render_history_page(page: int = 1, filter_type: str = "ALL", page_size: int = 5):
    """Renders a formatted page of transactions with navigation keyboard."""
    from database.queries import get_transactions_paginated
    tx_filter = filter_type if filter_type in ('SENT', 'RECEIVED') else None
    data = get_transactions_paginated(page=page, page_size=page_size, tx_type=tx_filter)
    
    items = data['transactions']
    total_pages = data['total_pages']
    total_count = data['total_count']
    
    if not items:
        text = "🧾 <b>Transaction History</b>\n━━━━━━━━━━━━━━\n<i>No transactions found for this filter.</i>"
        return text, get_back_to_menu_keyboard()
        
    lines = [
        f"🧾 <b>Transaction History ({filter_type})</b>",
        f"<i>Page {page} of {total_pages} ({total_count} records)</i>",
        "━━━━━━━━━━━━━━"
    ]
    
    for t in items:
        is_recv = t['transaction_type'] == 'RECEIVED'
        badge = "🟢" if is_recv else "🔴"
        arrow = "+" if is_recv else "-"
        amt = format_currency(t['amount'])
        person = t.get('person_name') or 'Unknown'
        cat = t.get('category') or 'General'
        date_val = t.get('transaction_date') or 'Today'
        bal = format_currency(t.get('balance_after', 0))
        
        lines.append(
            f"<b>#{t['id']}</b> {badge} <b>{arrow}{amt}</b> — {html.escape(person)}\n"
            f"   🏷 {html.escape(cat)} | 📅 {date_val}\n"
            f"   💼 Bal: <code>{bal}</code>\n"
        )
    
    lines.append("━━━━━━━━━━━━━━")
    return "\n".join(lines), get_history_paginated_keyboard(page, total_pages, filter_type)

def render_contacts_ledger_text() -> str:
    """Generates the Contact Ledger overview."""
    from database.queries import get_contact_ledger
    contacts = get_contact_ledger()
    if not contacts:
        return "👥 <b>Contact Ledger</b>\n━━━━━━━━━━━━━━\nNo contact transactions recorded yet."
        
    lines = [
        "👥 <b>Contact Ledger & Counterparties</b>",
        "━━━━━━━━━━━━━━"
    ]
    for c in contacts[:10]:
        net = c['net_balance']
        if net > 0:
            net_str = f"🟢 Owed to you: +{format_currency(net)}"
        elif net < 0:
            net_str = f"🔴 You spent: -{format_currency(abs(net))}"
        else:
            net_str = "⚪ Settled: ₹0.00"
            
        lines.append(
            f"👤 <b>{html.escape(c['name'])}</b> ({c['tx_count']} txs)\n"
            f"   💸 Sent: {format_currency(c['total_sent'])} | Received: {format_currency(c['total_received'])}\n"
            f"   {net_str}\n"
        )
    lines.append("━━━━━━━━━━━━━━")
    return "\n".join(lines)

def is_admin_user(update: Update) -> bool:
    """Checks if the user is the primary bot owner (TELEGRAM_USER_ID)."""
    if not update or not update.effective_user:
        return False
    return update.effective_user.id == TELEGRAM_USER_ID

async def is_authorized(update: Update) -> bool:
    """Checks if the user or group is authorized to use the bot."""
    if not update:
        return False
    user_id = update.effective_user.id if update.effective_user else None
    chat_id = update.effective_chat.id if update.effective_chat else None
    
    if user_id == TELEGRAM_USER_ID or (TELEGRAM_GROUP_ID is not None and chat_id == TELEGRAM_GROUP_ID):
        return True
        
    if update.effective_message and update.effective_chat and update.effective_chat.type == 'private':
        await update.effective_message.reply_text("❌ Unauthorized user.")
    return False

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends the interactive Home Menu card and button grid."""
    if not await is_authorized(update): return
    menu_text = render_home_menu_text()
    await update.message.reply_text(menu_text, reply_markup=get_home_menu_keyboard(), parse_mode='HTML')

async def chatid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Returns the chat ID for group configuration."""
    chat_id = update.message.chat_id
    await update.message.reply_text(f"This chat's ID is: `{chat_id}`", parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the complete, comprehensive guide of everything the bot can do."""
    if not await is_authorized(update): return
    
    help_text = (
        "👑 <b>Payment Tracker Bot — Complete Guide</b>\n\n"
        "Automatically log expenses, scan receipts from any UPI app, track balances, manage budgets, and view live visual dashboards.\n\n"
        "📸 <b>1. Receipt Upload & Scanning (AI Engine)</b>\n"
        "• Send a <b>screenshot</b> or <b>shared receipt</b> (image + text caption) from any app:\n"
        "  <code>BHIM</code>, <code>Paytm</code>, <code>PhonePe</code>, <code>Google Pay</code>, <code>CRED</code>, <code>Super.money</code>, <code>NaviPay</code>, <code>YONO SBI</code>, <code>Vyom</code>.\n"
        "• Instantly extracts Amount, Person, Date, Bank, & UTR Ref No.\n"
        "• <i>Ephemeral Image Privacy:</i> Receipt images are deleted right after scanning.\n\n"
        "💬 <b>2. Natural Text Tracking</b>\n"
        "• Simply type what you spent or received:\n"
        "  <code>Paid 500 to Ramesh</code>\n"
        "  <code>Received 6200 from Johnson</code>\n"
        "  <code>Paid 5000 to Balaji yesterday</code>\n\n"
        "🎯 <b>3. Budget & Financial Health</b>\n"
        "• /budget — View monthly budget progress, remaining funds & status bar\n"
        "• /setbudget &lt;amt&gt; — Set monthly spending target (e.g. <code>/setbudget 20000</code>)\n"
        "• /insights — AI-powered category breakdown, spending percentages & advice\n"
        "• /digest — Generate today's closing financial digest (or <code>/digest YYYY-MM-DD</code>)\n"
        "• /dashboard — View interactive dark-mode charts & live web analytics\n\n"
        "📊 <b>4. Balance & History</b>\n"
        "• /balance — Current balance, total sent/received today & net flow\n"
        "• /history — Clean sequential transaction list with dates & amounts\n"
        "• /details — Detailed view with database IDs & UTR numbers\n"
        "• /date &lt;date&gt; — View transactions on a specific date\n"
        "• /search &lt;query&gt; — Search by person name, bank, or UTR\n"
        "• /amount &lt;number&gt; — Search by exact amount\n\n"
        "📈 <b>5. Analytics & Organization</b>\n"
        "• /monthly (or /stats) — Monthly total spent, income, net savings & top recipient\n"
        "• /filter — Interactive filter buttons (Today, Yesterday, Month, Sent, Received)\n"
        "• /sort — Interactive sorting menu (Amount High ➔ Low, Low ➔ High, Date)\n\n"
        "🍽️ <b>6. Cafeteria Vegetarian System</b>\n"
        "• /menu — Full vegetarian cafeteria menu with prices & add-ons\n"
        "• /cafestats — Cafeteria monthly spend totals & most ordered items\n"
        "• /cafeedit — Re-tag or edit items for recent cafeteria payments\n"
        "• <i>Features:</i> 1-item mode, 2-items combos, Plate Builder cart, custom ice cream amounts & +₹5 packing\n\n"
        "⚙️ <b>7. Management & Edits</b>\n"
        "• /edit — Interactive 1-tap menu to edit amount, name, date, type, or UTR\n"
        "• /delete — Interactive 1-tap menu to delete record & auto-recalculate\n"
        "• /undo — Instantly revert the last delete, edit, or add action\n"
        "• /setbalance &lt;amt&gt; — Set starting balance (e.g. <code>/setbalance 50000</code>)\n"
        "• /restore — Restore from cloud/JSON backup whenever needed on demand\n\n"
        "📄 <b>8. Reports & Export</b>\n"
        "• /export (or /report, /statement) — Download official <b>PDF Statement</b> or <b>Excel Sheet (.xlsx)</b>\n\n"
        "☁️ <b>Cloud Reliability:</b>\n"
        "• Every transaction, edit, and deletion is automatically backed up and synced 24/7."
    )
    await update.message.reply_text(help_text, parse_mode='HTML')

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return

    recalculate_all_balances()
    balance = get_balance_setting()
    overall = get_overall_summary()
    today = get_today_summary()

    text = (
        f"💰 *Current Balance*\n"
        f"👉 *{format_currency(balance)}*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 *Overall Summary:*\n"
        f"• 🟢 Received: {format_currency(overall.total_received)}\n"
        f"• 🔴 Sent: {format_currency(overall.total_sent)}\n"
        f"• 📈 Net: {format_currency(overall.net_change)} ({overall.transaction_count} transactions)\n\n"
        f"📅 *Today's Summary:*\n"
        f"• 🟢 Received: {format_currency(today.total_received)}\n"
        f"• 🔴 Sent: {format_currency(today.total_sent)}\n"
        f"• 📈 Net: {format_currency(today.net_change)} ({today.transaction_count} transactions)"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    await balance_command(update, context)


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return

    # If user asks for IDs or full list explicitly e.g. /history ids, /history full
    if context.args and context.args[0].lower() in ('ids', 'id', 'details'):
        await details_command(update, context)
        return
        
    recalculate_all_balances()

    if not context.args or context.args[0].lower() not in ('full', 'all'):
        text, markup = render_history_page(page=1, filter_type="ALL", page_size=5)
        await update.message.reply_text(text, reply_markup=markup, parse_mode='HTML')
        return

    transactions = get_all_transactions_asc()
    if not transactions:
        await update.message.reply_text("ℹ️ No transactions recorded yet.")
        return

    # Calculate summary
    total_sent = sum(t['amount'] for t in transactions if t['transaction_type'] == 'SENT')
    total_received = sum(t['amount'] for t in transactions if t['transaction_type'] == 'RECEIVED')
    curr_balance = get_balance_setting()

    lines_list = ["📜 *Payment History*\n"]
    for t in transactions:
        date_str = format_display_date(t['transaction_date'])
        person = t['person_name'] or "Unknown"
        badge = "🔴" if t['transaction_type'] == 'SENT' else "🟢"
        amt_str = format_currency(t['amount'])
        bal_str = format_currency(t['balance_after'])

        lines_list.append(
            f"*{t['id']}.* {badge} *{amt_str}* — {person}\n"
            f"   📅 {date_str} | 💰 Bal: `{bal_str}`\n"
        )

    lines_list.append("━━━━━━━━━━━━━━━━━━━━")
    lines_list.append(f"🟢 *Total Received:* {format_currency(total_received)}")
    lines_list.append(f"🔴 *Total Sent:* {format_currency(total_sent)}")
    lines_list.append(f"💳 *Current Balance:* *{format_currency(curr_balance)}*")

    text = "\n".join(lines_list)

    if len(text) > 4096:
        parts = []
        current = ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > 4000:
                parts.append(current)
                current = line
            else:
                current += "\n" + line if current else line
        if current:
            parts.append(current)
        for part in parts:
            await update.message.reply_text(part, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, parse_mode='Markdown')

async def details_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays transactions WITH IDs and full technical details on demand."""
    if not await is_authorized(update): return
    
    transactions = get_all_transactions_asc()
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
    
    # Split message if too long for Telegram (4096 char limit)
    if len(text) > 4096:
        parts = []
        current = ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > 4000:
                parts.append(current)
                current = line
            else:
                current += "\n" + line if current else line
        if current:
            parts.append(current)
        for part in parts:
            await update.message.reply_text(part, parse_mode='Markdown')
    else:
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

async def amount_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Searches all transactions with the specified amount."""
    if not await is_authorized(update): return
    
    if not context.args:
        await update.message.reply_text(
            "💵 *Search by Amount:*\n"
            "Usage: `/amount <number>` or simply type the number (e.g. `500` or `5000`)\n\n"
            "Examples:\n"
            "• `/amount 500`\n"
            "• `/amount 5000`\n"
            "• `/amount 6200`\n"
            "• `30700`",
            parse_mode='Markdown'
        )
        return
        
    raw_amt = "".join(context.args).replace(',', '').replace('₹', '').replace('rs', '').strip()
    try:
        amt = float(raw_amt)
    except ValueError:
        await update.message.reply_text("❌ Invalid amount. Example: `/amount 500` or `/amount 5000`", parse_mode='Markdown')
        return
        
    txs = search_transactions(exact_amount=amt, sort_by="date_desc")
    if not txs:
        await update.message.reply_text(f"💵 No transactions found with amount *{format_currency(amt)}*.", parse_mode='Markdown')
        return
        
    total_val = sum(t['amount'] for t in txs)
    text = f"💵 *Found {len(txs)} transaction(s) of {format_currency(amt)}* (Total: {format_currency(total_val)})\n\n"
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
    """Opens sorting options or performs sorting directly by money / date args."""
    if not await is_authorized(update): return
    from bot.keyboards import get_sort_keyboard
    
    # If user provided argument e.g. /sort high, /sort low, /sort amount, /sort money
    if context.args:
        arg = context.args[0].lower()
        if arg in ('high', 'highest', 'max', 'amount_desc', 'desc', 'money', 'amount'):
            sort_by = 'amount_desc'
            title = "💰 Amount (Highest First - ₹ High ➔ Low)"
        elif arg in ('low', 'lowest', 'min', 'amount_asc', 'asc'):
            sort_by = 'amount_asc'
            title = "💰 Amount (Lowest First - ₹ Low ➔ High)"
        elif arg in ('date_asc', 'oldest', 'old'):
            sort_by = 'date_asc'
            title = "🗓️ Date (Oldest First)"
        else:
            sort_by = 'date_desc'
            title = "🗓️ Date (Newest First)"
            
        txs = search_transactions(sort_by=sort_by, limit=10)
        if not txs:
            await update.message.reply_text("No transactions found.")
            return
            
        text = f"🔀 *Sorted by: {title}*\n\n"
        for t in txs:
            date_s = format_display_date(t['transaction_date'])
            time_str = f" | ⏰ {t['transaction_time']}" if t['transaction_time'] and t['transaction_time'] != 'Unknown Time' else ""
            person = t['person_name'] or "Unknown"
            type_badge = "🔴 SENT" if t['transaction_type'] == 'SENT' else "🟢 RECEIVED"
            text += (
                f"• *{date_s}*{time_str} | {type_badge}\n"
                f"👤 {person}\n"
                f"💵 {format_currency(t['amount'])}\n"
                f"Balance: {format_currency(t['balance_after'])}\n\n"
            )
        await update.message.reply_text(text, reply_markup=get_sort_keyboard(), parse_mode='Markdown')
        return

    await update.message.reply_text("🔀 *Sort Transactions by Money or Date:*\n\nChoose an option below:", reply_markup=get_sort_keyboard(), parse_mode='Markdown')


async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initiates interactive editing or applies direct edit command."""
    if not await is_authorized(update): return
    from bot.keyboards import get_edit_fields_keyboard, get_transaction_selection_keyboard
    from services.balance_service import resequence_transaction_ids
    
    resequence_transaction_ids()

    # 1. No arguments: show list of recent transactions to tap on
    if not context.args:
        transactions = get_recent_transactions(limit=6)
        if not transactions:
            await update.message.reply_text("No transactions found to edit.")
            return
        context.user_data['action'] = 'waiting_edit_id'
        context.user_data['recent_edit_ids'] = [t['id'] for t in transactions]

        tx_list_lines = []
        for idx, t in enumerate(transactions, 1):
            date_s = format_display_date(t['transaction_date'])
            badge = "🟢" if t['transaction_type'] == 'RECEIVED' else "🔴"
            tx_list_lines.append(f"*{t['id']}.* {badge} {t['person_name'] or 'Unknown'} — *{format_currency(t['amount'])}* ({date_s})")

        list_text = "\n".join(tx_list_lines)
        await update.message.reply_text(
            "✏️ *Edit Transaction*\n\n"
            f"Tap a button below, or reply with the transaction ID (e.g. `1`):\n\n"
            f"{list_text}",
            reply_markup=get_transaction_selection_keyboard(transactions, 'select_edit'),
            parse_mode='Markdown'
        )
        return

    try:
        raw_num = int(context.args[0].replace('#', ''))
    except ValueError:
        await update.message.reply_text("❌ Invalid ID format. Example: `/edit 3` or `/edit 1`", parse_mode='Markdown')
        return

    import asyncio
    from services.backup_service import backup_to_telegram

    tx = get_transaction_by_id(raw_num)
    if not tx:
        await update.message.reply_text("❌ Transaction not found.", parse_mode='Markdown')
        return
    tx_id = tx['id']

    # 2. Only ID provided: show edit field buttons
    if len(context.args) == 1:
        date_str = format_display_date(tx['transaction_date'])
        person = tx['person_name'] or "Unknown"
        text = (
            f"✏️ *Editing Transaction #{tx_id}*\n\n"
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
        needs_recalc = True
    elif field in ('ref', 'reference', 'utr', 'reference_number'):
        updates['reference_number'] = value_raw
    else:
        await update.message.reply_text(f"❌ Unknown field `{field}`. Supported: `amount`, `person`, `type`, `date`, `ref`.", parse_mode='Markdown')
        return

    from services.undo_service import record_edit_action
    record_edit_action(tx)

    success = update_transaction(tx_id, updates)
    if success:
        if needs_recalc:
            new_bal = recalculate_all_balances()
        else:
            new_bal = get_balance_setting()

        updated_tx = get_transaction_by_id(tx_id)
        person = (updated_tx['person_name'] if updated_tx else '') or "Unknown"
        amt_s = format_currency(updated_tx['amount']) if updated_tx else ''
        bal_flow = ""
        if updated_tx and 'balance_before' in updated_tx and 'balance_after' in updated_tx:
            bal_flow = f"\n💰 *Balance Flow:* {format_currency(updated_tx['balance_before'])} ➔ *{format_currency(updated_tx['balance_after'])}"

        from bot.keyboards import get_undo_keyboard
        try:
            asyncio.create_task(backup_to_telegram(context.bot))
        except Exception:
            pass
        await update.message.reply_text(
            f"✅ *Transaction #{tx_id} Updated*\n\n"
            f"👤 *Person:* {person}\n"
            f"💵 *Amount:* *{amt_s}*{bal_flow}\n\n"
            f"💳 *Current Balance:* *{format_currency(new_bal)}*",
            reply_markup=get_undo_keyboard(),
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text("❌ Failed to update transaction.")

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initiates interactive deletion or prompts for ID."""
    if not await is_authorized(update): return
    if not is_admin_user(update):
        await update.message.reply_text("❌ Only the bot owner can delete transactions.")
        return
    from bot.keyboards import get_delete_confirm_keyboard, get_transaction_selection_keyboard

    # 1. No arguments: show list of recent transactions to tap on
    if not context.args:
        transactions = get_recent_transactions(limit=6)
        if not transactions:
            await update.message.reply_text("No transactions found to delete.")
            return
        context.user_data['action'] = 'waiting_delete_id'
        context.user_data['recent_delete_ids'] = [t['id'] for t in transactions]

        tx_list_lines = []
        for idx, t in enumerate(transactions, 1):
            date_s = format_display_date(t['transaction_date'])
            badge = "🟢" if t['transaction_type'] == 'RECEIVED' else "🔴"
            tx_list_lines.append(f"*{t['id']}.* {badge} {t['person_name'] or 'Unknown'} — *{format_currency(t['amount'])}* ({date_s})")

        list_text = "\n".join(tx_list_lines)
        await update.message.reply_text(
            "🗑️ *Delete Transaction*\n\n"
            f"Tap a button below, or reply with the transaction ID (e.g. `1`):\n\n"
            f"{list_text}",
            reply_markup=get_transaction_selection_keyboard(transactions, 'select_delete'),
            parse_mode='Markdown'
        )
        return

    try:
        raw_num = int(context.args[0].replace('#', ''))
    except ValueError:
        await update.message.reply_text("❌ Invalid ID format. Example: `/delete 3` or `/delete 1`", parse_mode='Markdown')
        return

    tx = get_transaction_by_id(raw_num)
    if not tx:
        await update.message.reply_text("❌ Transaction not found.", parse_mode='Markdown')
        return
    tx_id = tx['id']

    # Show confirmation keyboard
    date_str = format_display_date(tx['transaction_date'])
    person = tx['person_name'] or "Unknown"
    text = (
        f"🗑️ *Delete Transaction #{tx_id}*\n\n"
        f"Type: {tx['transaction_type']}\n"
        f"Amount: {format_currency(tx['amount'])}\n"
        f"Person: {person}\n"
        f"Date: {date_str}\n\n"
        "Are you sure you want to delete this transaction?"
    )
    await update.message.reply_text(text, reply_markup=get_delete_confirm_keyboard(tx_id), parse_mode='Markdown')

async def setbalance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    if not is_admin_user(update):
        await update.message.reply_text("❌ Only the bot owner can set the balance.")
        return
    import asyncio
    from services.balance_service import set_explicit_balance
    from services.backup_service import backup_to_telegram
    
    if not context.args:
        await update.message.reply_text("Usage: /setbalance <amount>")
        return
        
    try:
        new_balance = float(context.args[0].replace(',', '').replace('₹', '').strip())
        final_bal = set_explicit_balance(new_balance)
        try:
            asyncio.create_task(backup_to_telegram(context.bot))
        except Exception:
            pass
        await update.message.reply_text(f"✅ Balance set to {format_currency(final_bal)}")
    except ValueError:
        await update.message.reply_text("❌ Invalid amount format. Example: /setbalance 50000")


async def send_pdf_report(chat, bot):
    from services.export_service import generate_pdf_statement
    from services.gdrive_service import is_gdrive_available, upload_statement_to_drive
    export_path = DATA_DIR / "Payment_Tracker_Statement.pdf"
    try:
        generate_pdf_statement(str(export_path))
        if is_gdrive_available():
            try:
                asyncio.create_task(asyncio.to_thread(upload_statement_to_drive, str(export_path)))
            except Exception:
                pass
        with open(export_path, 'rb') as f:
            await bot.send_document(
                chat_id=chat.id,
                document=f,
                filename="Payment_Tracker_Statement.pdf",
                caption="📄 *Here is your official PDF Account Statement.*",
                parse_mode='Markdown'
            )
    except Exception as e:
        logger.error(f"PDF Export error: {e}", exc_info=True)
        await bot.send_message(chat_id=chat.id, text="❌ Failed to generate PDF statement.")
    finally:
        if os.path.exists(export_path):
            try: os.remove(export_path)
            except OSError: pass

async def send_excel_report(chat, bot):
    from services.export_service import generate_excel_report
    from services.gdrive_service import is_gdrive_available, upload_statement_to_drive
    export_path = DATA_DIR / "transactions_export.xlsx"
    try:
        generate_excel_report(str(export_path))
        if is_gdrive_available():
            try:
                asyncio.create_task(asyncio.to_thread(upload_statement_to_drive, str(export_path)))
            except Exception:
                pass
        with open(export_path, 'rb') as f:
            await bot.send_document(
                chat_id=chat.id,
                document=f,
                filename="transactions_export.xlsx",
                caption="📊 *Here is your transactions Excel spreadsheet.*",
                parse_mode='Markdown'
            )
    except Exception as e:
        logger.error(f"Excel Export error: {e}", exc_info=True)
        await bot.send_message(chat_id=chat.id, text="❌ Failed to generate Excel export.")
    finally:
        if os.path.exists(export_path):
            try: os.remove(export_path)
            except OSError: pass

async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exports transactions as a PDF Statement or Excel spreadsheet."""
    if not await is_authorized(update): return
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    arg = (context.args[0].lower() if context.args else "")

    if arg in ('pdf', 'statement', 'doc'):
        await update.message.reply_text("⏳ Generating PDF statement...")
        await send_pdf_report(update.effective_chat, context.bot)
        return
    elif arg in ('excel', 'xlsx', 'sheet'):
        await update.message.reply_text("⏳ Generating Excel spreadsheet...")
        await send_excel_report(update.effective_chat, context.bot)
        return

    # Interactive format selection
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📄 PDF Statement", callback_data="export_file:pdf"),
            InlineKeyboardButton("📊 Excel Sheet", callback_data="export_file:excel")
        ]
    ])
    await update.message.reply_text(
        "📊 *Export Transactions & Reports*\n\nChoose your preferred format below:",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )

async def insights_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generates AI spending insights and category analytics."""
    if not await is_authorized(update): return
    from services.category_service import format_spending_insights
    from datetime import datetime
    
    now = datetime.now()
    year = now.year
    month = now.month
    
    if context.args:
        arg = context.args[0]
        if arg.isdigit() and 1 <= int(arg) <= 12:
            month = int(arg)
        if len(context.args) > 1 and context.args[1].isdigit():
            year = int(context.args[1])
            
    text = format_spending_insights(year, month)
    await update.message.reply_text(text, parse_mode='HTML')

async def budget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the monthly budget status and progress bar."""
    if not await is_authorized(update): return
    from services.budget_service import format_budget_status
    from datetime import datetime
    
    now = datetime.now()
    year = now.year
    month = now.month
    
    if context.args:
        arg = context.args[0]
        if arg.isdigit() and 1 <= int(arg) <= 12:
            month = int(arg)
        if len(context.args) > 1 and context.args[1].isdigit():
            year = int(context.args[1])
            
    text = format_budget_status(year, month)
    await update.message.reply_text(text, parse_mode='HTML')

async def setbudget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets the monthly spending budget target."""
    if not await is_authorized(update): return
    from services.budget_service import set_budget
    from services.backup_service import backup_to_telegram
    import asyncio
    
    if not context.args:
        await update.message.reply_text(
            "🎯 <b>Set Monthly Budget</b>\n\n"
            "Usage: <code>/setbudget &lt;amount&gt;</code>\n\n"
            "Examples:\n"
            "• <code>/setbudget 15000</code>\n"
            "• <code>/setbudget 25000</code>\n"
            "• <code>/setbudget 0</code> <i>(to disable budget)</i>",
            parse_mode='HTML'
        )
        return
        
    raw_amt = "".join(context.args).replace(',', '').replace('₹', '').replace('rs', '').strip()
    try:
        amt = float(raw_amt)
        msg = set_budget(amt)
        try:
            asyncio.create_task(backup_to_telegram(context.bot))
        except Exception:
            pass
        await update.message.reply_text(msg, parse_mode='HTML')
    except ValueError:
        await update.message.reply_text("❌ Invalid amount format. Example: <code>/setbudget 20000</code>", parse_mode='HTML')

async def digest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generates the daily financial closing digest on demand."""
    if not await is_authorized(update): return
    from services.scheduler_service import format_daily_digest
    
    target_date = None
    if context.args:
        from utils.dates import parse_date
        raw_d = " ".join(context.args).strip()
        parsed = parse_date(raw_d)
        if parsed:
            target_date = parsed.strftime("%Y-%m-%d")
            
    digest_text = format_daily_digest(target_date)
    await update.message.reply_text(digest_text, parse_mode='HTML')

async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Provides a live link to the interactive web dashboard & visual charts via WebApp."""
    if not await is_authorized(update): return
    import os
    from config import DASHBOARD_TOKEN
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
    
    render_url = os.getenv("RENDER_EXTERNAL_URL", "https://payment-tracker-3r8w.onrender.com").rstrip('/')
    token_param = f"?token={DASHBOARD_TOKEN}" if DASHBOARD_TOKEN else ""
    dash_url = f"{render_url}/dashboard{token_param}"
    
    msg = (
        f"📊 <b>LIVE FINANCIAL DASHBOARD</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Tap <b>Open Dashboard</b> below to view interactive charts, month switcher, category donut breakdowns, top payees, and spending heatmaps right inside Telegram!\n\n"
        f"🔗 <code>{dash_url}</code>"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Open Dashboard", web_app=WebAppInfo(url=dash_url))],
        [InlineKeyboardButton("🌐 Open in Browser", url=dash_url)]
    ])
    await update.message.reply_text(msg, reply_markup=markup, parse_mode='HTML')

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the full vegetarian cafeteria menu with prices & add-ons."""
    if not await is_authorized(update): return
    from services.cafeteria_service import format_full_menu
    from bot.keyboards import get_menu_view_keyboard
    menu_text = format_full_menu()
    await update.message.reply_text(menu_text, reply_markup=get_menu_view_keyboard(), parse_mode='HTML')

async def cafestats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays monthly spending insights and top ordered veg items at the cafeteria."""
    if not await is_authorized(update): return
    from services.cafeteria_service import format_cafeteria_stats
    stats_text = format_cafeteria_stats()
    await update.message.reply_text(stats_text, parse_mode='HTML')

async def cafeedit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Opens the interactive cafeteria item selector for the most recent or specified cafeteria payment."""
    if not await is_authorized(update): return
    from database.queries import get_cafeteria_transactions, get_transaction_by_id
    from bot.keyboards import get_cafeteria_selection_keyboard
    import html
    
    target_tx = None
    if context.args:
        clean_id = context.args[0].replace('#', '').strip()
        if clean_id.isdigit():
            target_tx = get_transaction_by_id(int(clean_id))
            
    if not target_tx:
        cafe_txs = get_cafeteria_transactions(limit=1)
        if cafe_txs:
            target_tx = cafe_txs[0]
            
    if not target_tx:
        await update.message.reply_text("❌ No recent cafeteria payments found to edit. Scan a receipt or use <code>/edit</code>.", parse_mode='HTML')
        return
        
    tx_id = target_tx['id']
    amt = float(target_tx['amount'])
    await update.message.reply_text(
        f"✏️ <b>Edit Cafeteria Order #{tx_id} (Paid {html.escape(format_currency(amt))}):</b>\n\n"
        f"<b>How many items or what did you order?</b>\n"
        f"Select an option below to update what you ordered:",
        reply_markup=get_cafeteria_selection_keyboard(tx_id, amt),
        parse_mode='HTML'
    )

async def addmenu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Adds a custom item to the cafeteria menu."""
    if not await is_authorized(update): return
    from services.cafeteria_service import add_custom_menu_item
    import html

    args = context.args
    if not args:
        context.user_data['action'] = 'waiting_add_menu_item'
        await update.message.reply_text(
            "➕ <b>Add Custom Cafeteria Menu Item</b>\n\n"
            "Please send the item in this format:\n"
            "<code>Item Name, Price, Category</code>\n\n"
            "<i>Examples:</i>\n"
            "• <code>Paneer Roll, 45, Snacks</code>\n"
            "• <code>Mango Shake, 30, Beverages</code>\n"
            "• <code>Veg Noodles, 50, Chinese</code>",
            parse_mode='HTML'
        )
        return

    full_arg = " ".join(args)
    name, price, category = None, None, "Custom"
    if "," in full_arg:
        parts = [p.strip() for p in full_arg.split(",")]
        name = parts[0]
        if len(parts) > 1:
            try:
                price = float(parts[1])
            except ValueError:
                price = None
        if len(parts) > 2:
            category = parts[2]
    else:
        tokens = args
        price_idx = -1
        for idx, tok in enumerate(tokens):
            try:
                p_val = float(tok)
                if p_val > 0:
                    price = p_val
                    price_idx = idx
                    break
            except ValueError:
                continue

        if price_idx != -1:
            name = " ".join(tokens[:price_idx])
            cat_tokens = tokens[price_idx+1:]
            if cat_tokens:
                category = " ".join(cat_tokens)
            else:
                category = "Custom"
        else:
            name = full_arg

    if not name or price is None or price <= 0:
        await update.message.reply_text(
            "❌ <b>Invalid format!</b>\n\n"
            "Usage: <code>/addmenu &lt;Item Name&gt; &lt;Price&gt; [Category]</code>\n"
            "Or: <code>/addmenu Paneer Roll, 45, Snacks</code>",
            parse_mode='HTML'
        )
        return

    success, msg = add_custom_menu_item(name, price, category, is_veg=True)
    if success:
        await update.message.reply_text(
            f"✅ <b>Custom Menu Item Added!</b>\n\n"
            f"• <b>Item:</b> 🍽️ <b>{html.escape(name)}</b>\n"
            f"• <b>Price:</b> <b>₹{price:.0f}</b>\n"
            f"• <b>Category:</b> {html.escape(category)}\n"
            f"• <b>Type:</b> 🟢 Pure Veg\n\n"
            f"This item is now available in your cafeteria auto-suggestions and plate builder! Use <code>/menu</code> to see full menu.",
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text(f"❌ Could not add item: {html.escape(msg)}", parse_mode='HTML')


async def delmenu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Removes a custom item from the cafeteria menu."""
    if not await is_authorized(update): return
    from services.cafeteria_service import delete_custom_menu_item
    from database.db import get_custom_menu_items
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    import html

    args = context.args
    custom_items = get_custom_menu_items()

    if not custom_items:
        await update.message.reply_text("ℹ️ No custom menu items found to delete. Standard predefined menu items cannot be deleted.", parse_mode='HTML')
        return

    if not args:
        keyboard = []
        for it in custom_items:
            keyboard.append([InlineKeyboardButton(f"🗑️ Delete {it['name']} (₹{it['price']:.0f})", callback_data=f"cafe_del_item:{it['id']}")])
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cafe_del_cancel")])
        await update.message.reply_text(
            "🗑️ <b>Select Custom Menu Item to Remove:</b>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
        return

    name_query = " ".join(args).strip()
    success, msg = delete_custom_menu_item(name_query)
    if success:
        await update.message.reply_text(f"✅ {html.escape(msg)}", parse_mode='HTML')
    else:
        await update.message.reply_text(f"❌ {html.escape(msg)}", parse_mode='HTML')

async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restores database transactions from clean text JSON backup file via idempotent upsert."""
    if not await is_authorized(update): return
    from services.backup_service import import_database_from_json, BACKUP_JSON_PATH
    from database.queries import get_all_transactions
    import html

    if not BACKUP_JSON_PATH.exists():
        await update.message.reply_text("❌ No text backup file found to restore from.", parse_mode='HTML')
        return

    await update.message.reply_text("⏳ Restoring ledger from backup via idempotent upsert...")
    result = import_database_from_json()
    if not result.get('success'):
        await update.message.reply_text(f"❌ Restore failed: {html.escape(str(result.get('error')))}", parse_mode='HTML')
        return

    txs = get_all_transactions()
    ins = result.get('inserted', 0)
    upd = result.get('updated', 0)
    await update.message.reply_text(
        f"✅ <b>Database Restored Successfully!</b>\n\n"
        f"• <b>{ins}</b> new records inserted.\n"
        f"• <b>{upd}</b> existing records updated.\n"
        f"• <b>{len(txs)}</b> active live transactions now available.\n"
        f"• Permanent UIDs & running balances verified.\n\n"
        f"Use <code>/history</code> or <code>/balance</code> to view restored transactions.",
        parse_mode='HTML'
    )

async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reverts the last delete, edit, or add action performed."""
    if not await is_authorized(update): return
    from services.undo_service import perform_undo
    from services.backup_service import backup_to_telegram
    import asyncio

    success, msg = perform_undo()
    if success:
        try:
            asyncio.create_task(backup_to_telegram(context.bot))
        except Exception:
            pass
        await update.message.reply_text(msg, parse_mode='HTML')
    else:
        await update.message.reply_text(f"ℹ️ {msg}", parse_mode='HTML')






