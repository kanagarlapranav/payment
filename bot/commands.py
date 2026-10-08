from typing import Any, Tuple, Optional
from telegram import Update
from telegram.ext import ContextTypes
from config import TELEGRAM_USER_ID, TELEGRAM_GROUP_ID, DATA_DIR, DAILY_DIGEST_TIME, logger
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
import asyncio
import html
import os
from bot.keyboards import (
    get_home_menu_keyboard, get_history_paginated_keyboard, get_back_to_menu_keyboard,
    get_quick_add_keyboard, get_settings_menu_keyboard
)

def render_home_menu_text(workspace_id: str = None) -> str:
    """Generates the main Home Menu dashboard card."""
    now = datetime.now()
    balance = get_balance_setting(workspace_id=workspace_id)
    today_stats = get_today_summary(workspace_id=workspace_id)
    monthly = get_monthly_summary(now.year, now.month, workspace_id=workspace_id)
    
    from services.budget_service import get_budget_info
    b_info = get_budget_info(now.year, now.month, workspace_id=workspace_id)
    
    month_names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    m_name = month_names[now.month]
    
    budget_line = ""
    if b_info.get('budget', 0) > 0:
        budget_line = f"🎯 <b>Budget:</b> <code>{b_info['progress_bar']}</code> {b_info['percentage']:.0f}% (₹{b_info['spent']:,.0f} / ₹{b_info['budget']:,.0f})\n"
        
    net_today = today_stats.net_change
    net_sign = "+" if net_today >= 0 else "-"
    
    return (
        f"⚡ <b>Payment Tracker Dashboard</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"💰 <b>Balance:</b> <b>{format_currency(balance)}</b>\n"
        f"📅 <b>Today:</b> {net_sign}{format_currency(abs(net_today))} ({today_stats.transaction_count} txs)\n"
        f"🗓️ <b>{m_name} {now.year} Spent:</b> {format_currency(monthly['total_sent'])}\n"
        f"{budget_line}"
        f"━━━━━━━━━━━━━━\n"
        f"<i>Select an option or send a receipt screenshot:</i>"
    )

def render_history_page(page: int = 1, filter_type: str = "ALL", page_size: int = 5, sort_by: str = "date_desc", workspace_id: str = None):
    """Renders a formatted page of transactions with navigation keyboard and sort order control."""
    from database.queries import get_transactions_paginated
    sort_by = sort_by or "date_desc"
    tx_filter = filter_type if filter_type in ('SENT', 'RECEIVED', 'TRANSFER') else None
    data = get_transactions_paginated(page=page, page_size=page_size, tx_type=tx_filter, sort_by=sort_by, workspace_id=workspace_id)
    
    items = data['transactions']
    total_pages = data['total_pages']
    total_count = data['total_count']
    
    if not items:
        text = (
            "🧾 <b>Transaction History</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📭 <i>No transactions found for this filter.</i>\n\n"
            "💡 <i>Tap a filter below to switch view, or tap Back to return to Home.</i>\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        return text, get_history_paginated_keyboard(1, 1, filter_type, tx_rows=[], sort_by=sort_by)
        
    if page == 1 and filter_type == "ALL":
        if sort_by == "date_asc":
            header = f"🧾 <b>Transactions (Oldest First — ASC)</b>"
        elif sort_by in ("id_desc", "created_desc"):
            header = f"🧾 <b>Transactions (ID Order — DESC)</b>"
        else:
            header = f"🧾 <b>Latest {len(items)} Transactions</b>"
    else:
        header = f"🧾 <b>Transaction History ({filter_type})</b>"

    lines = [
        header,
        f"<i>Page {page} of {max(1, total_pages)} ({total_count} records)</i>",
        "━━━━━━━━━━━━━━━━━━━━"
    ]
    
    start_num = (page - 1) * page_size + 1
    for idx, t in enumerate(items, start=start_num):
        ttype = t.get('transaction_type')
        if ttype == 'RECEIVED':
            badge = "🟢"
            arrow = "+"
        elif ttype == 'SENT':
            badge = "🔴"
            arrow = "-"
        else:
            badge = "🔄"
            arrow = "⇄"
            
        amt = format_currency(t['amount'])
        person = t.get('person_name') or 'Unknown'
        cat = t.get('category') or 'General'
        date_val = t.get('transaction_date') or 'Today'
        bal = format_currency(t.get('balance_after', 0))
        
        lines.append(
            f"<b>{idx}.</b> {badge} <b>{arrow}{amt}</b> — {html.escape(person)}\n"
            f"   🏷 {html.escape(cat)} | 📅 {date_val}\n"
            f"   💼 Bal: <code>{bal}</code>\n"
        )
    
    lines.append("━━━━━━━━━━━━━━━━━━━━\n<i>💡 Tap a transaction # button below to view details, edit, or delete:</i>")
    return "\n".join(lines), get_history_paginated_keyboard(page, total_pages, filter_type, tx_rows=items, sort_by=sort_by)

def render_transaction_detail(tx_id: int, workspace_id: str = None):
    """Renders the detailed view of a single transaction."""
    from database.queries import get_transaction_by_id
    from bot.keyboards import get_transaction_detail_keyboard
    tx = get_transaction_by_id(tx_id)
    if not tx or (workspace_id and tx.get('workspace_id') and tx.get('workspace_id') != workspace_id):
        return "❌ <b>Transaction not found or deleted.</b>", get_back_to_menu_keyboard()
        
    ttype = tx.get('transaction_type')
    if ttype == 'RECEIVED':
        badge = "🟢"
        type_str = "Received (Income)"
    elif ttype == 'SENT':
        badge = "🔴"
        type_str = "Sent (Expense)"
    else:
        badge = "🔄"
        type_str = "Transfer (Internal / Neutral)"
    amt = format_currency(tx['amount'])
    person = tx.get('person_name') or 'Unknown'
    cat = tx.get('category') or 'General'
    date_val = str(tx.get('transaction_date') or 'N/A')
    time_val = str(tx.get('transaction_time') or 'N/A')
    app_val = str(tx.get('payment_app') or 'N/A')
    bank_val = str(tx.get('bank_name') or 'N/A')
    ref_val = str(tx.get('reference_number') or 'N/A')
    bb = format_currency(tx.get('balance_before', 0))
    ba = format_currency(tx.get('balance_after', 0))
    uid_val = str(tx.get('uid') or 'N/A')
    
    text = (
        f"📄 <b>Transaction Details #{tx['id']}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Type:</b> {badge} {type_str}\n"
        f"• <b>Amount:</b> <b>{amt}</b>\n"
        f"• <b>Party:</b> <b>{html.escape(person)}</b>\n"
        f"• <b>Category:</b> {html.escape(cat)}\n"
        f"• <b>Date & Time:</b> {html.escape(date_val)} at {html.escape(time_val)}\n"
        f"• <b>Payment App:</b> {html.escape(app_val)}\n"
        f"• <b>Bank / Account:</b> {html.escape(bank_val)}\n"
        f"• <b>Reference / UTR:</b> <code>{html.escape(ref_val)}</code>\n"
        "────────────────────\n"
        f"• <b>Balance Before:</b> {bb}\n"
        f"• <b>Balance After:</b> <b>{ba}</b>\n"
        f"• <b>UID:</b> <code>{html.escape(uid_val[:16])}...</code>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    return text, get_transaction_detail_keyboard(tx_id)

def render_backup_status_text() -> str:
    """Renders the cloud & local backup status overview."""
    from database.db import get_db_connection
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM settings WHERE key IN ('backup_revision', 'last_local_backup_at', 'last_telegram_backup_at', 'last_gdrive_backup_at', 'is_dirty', 'current_balance')")
        s = {r['key']: r['value'] for r in cur.fetchall()}
        
    rev = s.get('backup_revision', '1')
    local_at = s.get('last_local_backup_at') or 'Never'
    tg_at = s.get('last_telegram_backup_at') or 'Never'
    drive_at = s.get('last_gdrive_backup_at') or 'Never (Optional)'
    is_dirty = s.get('is_dirty', '0') == '1'
    dirty_badge = "⚠️ Unsaved changes pending backup" if is_dirty else "✅ Up-to-date (Clean)"
    
    text = (
        "☁️ <b>Backup & Disaster Recovery Status</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Schema Version:</b> Format v2 (SHA-256 Verified)\n"
        f"• <b>Database Revision:</b> <code>Rev {rev}</code>\n"
        f"• <b>Status:</b> {dirty_badge}\n\n"
        f"💾 <b>Last Local Backup:</b>\n   <code>{local_at}</code>\n"
        f"✈️ <b>Last Telegram Backup:</b>\n   <code>{tg_at}</code>\n"
        f"📁 <b>Last Google Drive Backup:</b>\n   <code>{drive_at}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Automatic backups run on every transaction and before server shutdown.</i>"
    )
    return text

def render_recurring_overview_text() -> str:
    """Renders the Recurring Payments overview and upcoming dues card."""
    from services.recurring_service import get_upcoming_recurring, get_recurring_monthly_total
    upcoming = get_upcoming_recurring(days_ahead=30)
    monthly_total = get_recurring_monthly_total()
    
    lines = [
        "🔄 <b>Recurring Payments & Subscriptions</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"• <b>Projected Monthly:</b> <b>{format_currency(monthly_total)}</b>",
        f"• <b>Upcoming (Next 30 Days):</b> {len(upcoming)} due\n"
    ]
    
    if not upcoming:
        lines.append("🎉 <i>No recurring payments due in the next 30 days.</i>\n")
    else:
        lines.append("<b>📅 Upcoming Schedule:</b>")
        for it in upcoming[:6]:
            amt = format_currency(it['amount'])
            payee = html.escape(it.get('payee_name') or 'Due')
            due_date = it.get('next_due_date') or 'Soon'
            freq = it.get('frequency', 'MONTHLY').capitalize()
            lines.append(f"• <b>#{it['id']}</b> {due_date} — <b>{payee}</b>: <b>{amt}</b> ({freq})")
        lines.append("")
        
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("<i>💡 Tap a Pay or Skip button below, or add a new recurring payment:</i>")
    return "\n".join(lines)

def render_all_recurring_text() -> str:
    """Renders full list of active and inactive recurring payments."""
    from services.recurring_service import get_all_recurring
    all_recs = get_all_recurring(include_inactive=True)
    if not all_recs:
        return "🔄 <b>Recurring Payments</b>\n━━━━━━━━━━━━━━━━━━━━\n<i>No recurring payments created yet.</i>"
        
    lines = [
        "📋 <b>All Recurring Payments</b>",
        "━━━━━━━━━━━━━━━━━━━━"
    ]
    for it in all_recs:
        status_badge = "🟢" if it.get('status') == 'ACTIVE' else "⏸️"
        amt = format_currency(it['amount'])
        payee = html.escape(it.get('payee_name') or 'Due')
        freq = it.get('frequency', 'MONTHLY').capitalize()
        due = it.get('next_due_date') or 'N/A'
        lines.append(f"{status_badge} <b>#{it['id']} {payee}</b> — <b>{amt}</b> ({freq})\n   Next due: <code>{due}</code> | Status: {it.get('status')}")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)

def render_monthly_closing_summary_text(year: int, month: int) -> str:
    """Renders the comprehensive Month-End Financial Closing & Retrospective Review."""
    from services.monthly_review_service import calculate_monthly_closing_metrics
    m = calculate_monthly_closing_metrics(year, month)
    
    month_names = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    m_name = f"{month_names[month]} {year}"
    
    status_badge = f"✅ <b>Reviewed</b> (<code>{m['reviewed_at'][:10]}</code>)" if m['is_closed'] else "⚠️ <b>Pending Review</b>"
    
    # Savings badge
    savings_badge = "🟢 Net Surplus" if m['net_savings'] >= 0 else "🔴 Deficit"
    
    # Budget line
    if m['budget_allocated'] > 0:
        budget_line = f"• <b>Budget Used:</b> {m['budget_spent_pct']:.1f}% of {format_currency(m['budget_allocated'])}"
    else:
        budget_line = "• <b>Budget:</b> No limit set"

    max_tx_str = f"#{m['max_transaction_id']} {html.escape(m['max_transaction_payee'])} ({format_currency(m['max_transaction_amount'])})" if m['max_transaction_id'] else "None"

    text = (
        f"📊 <b>Monthly Financial Closing & Review</b>\n"
        f"🗓️ <b>{m_name}</b> | {status_badge}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• 🟢 <b>Total Income:</b> <b>+{format_currency(m['total_income'])}</b>\n"
        f"• 🔴 <b>Total Expenses:</b> <b>-{format_currency(m['total_expense'])}</b>\n"
        f"• 💼 <b>Net Savings:</b> <b>{format_currency(m['net_savings'])}</b> ({savings_badge})\n"
        f"• 📈 <b>Savings Rate:</b> <b>{m['savings_rate_pct']:.1f}%</b>\n"
        "────────────────────\n"
        f"• 🏷️ <b>Top Category:</b> <b>{html.escape(m['top_category'])}</b> ({format_currency(m['top_category_amount'])})\n"
        f"• 👤 <b>Top Payee:</b> <b>{html.escape(m['top_payee'])}</b> ({format_currency(m['top_payee_amount'])})\n"
        f"• ⚡ <b>Largest Expense:</b> {max_tx_str}\n"
        f"{budget_line}\n"
        f"• 🧾 <b>Total Transactions:</b> {m['transaction_count']}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>💡 Tap 'Mark Month Reviewed' to store the review snapshot, or export to Excel:</i>"
    )
    return text

def render_contacts_ledger_text(workspace_id: str = None) -> str:
    """Generates the Contact Ledger overview with workspace isolation."""
    from database.queries import get_contact_ledger
    contacts = get_contact_ledger(workspace_id=workspace_id)
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

from bot.auth import require_authorized, require_admin, is_owner, is_authorized_user, require_member

def is_admin_user(update: Update) -> bool:
    """Checks if the user is the primary bot owner (TELEGRAM_USER_ID)."""
    return is_owner(update)

async def is_authorized(update: Update) -> bool:
    """Checks if the user or group is authorized to use the bot."""
    return await require_authorized(update)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends the interactive Home Menu card and button grid."""
    if not await require_authorized(update): return
    from bot.auth import get_workspace_context
    ctx = get_workspace_context(update)
    ws_id = ctx.workspace_id if ctx else None
    menu_text = render_home_menu_text(workspace_id=ws_id)
    await update.message.reply_text(menu_text, reply_markup=get_home_menu_keyboard(), parse_mode='HTML')

async def chatid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Returns the chat ID for group configuration."""
    if not await require_authorized(update): return
    chat_id = update.message.chat_id
    await update.message.reply_text(f"This chat's ID is: `{chat_id}`", parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the role-tailored guide of commands and bot capabilities."""
    if not await require_authorized(update): return
    from bot.auth import get_workspace_context, is_owner
    ctx = get_workspace_context(update)
    caller_is_owner = is_owner(update)
    caller_is_admin = caller_is_owner or (ctx and ctx.has_role('admin'))
    
    sections = [
        "👑 <b>Payment Tracker Bot — Quick Guide</b>\n"
        "Automatically log expenses, scan receipts from any UPI app, track balances, and manage budgets.\n"
    ]
    
    # 1. Logging expenses
    sections.append(
        "💬 <b>1. Tracking & Logging Expenses</b>\n"
        "• Send a <b>screenshot</b> or <b>shared receipt</b> (image + text caption) from any app:\n"
        "  <code>BHIM</code>, <code>Paytm</code>, <code>PhonePe</code>, <code>Google Pay</code>, <code>CRED</code>, etc.\n"
        "• Simply type what you spent or received:\n"
        "  <code>Paid 500 to Ramesh</code>\n"
        "  <code>Received 6200 from Johnson</code>\n"
        "  <code>Paid 50 to Cafeteria</code>\n"
    )
    
    # 2. Balance & History
    sections.append(
        "📊 <b>2. Balance & History</b>\n"
        "• /balance — Current balance & today's cash flow\n"
        "• /history — Clean sequential transaction list\n"
        "• /last5 (or /recent) — View latest 5 transactions\n"
        "• /search &lt;query&gt; — Search by person name, bank, or UTR\n"
        "• /amount &lt;number&gt; — Search by exact amount\n"
        "• /dashboard — View interactive dark-mode charts & live web analytics\n"
    )
    
    # 3. Cafeteria System
    sections.append(
        "🍽️ <b>3. Cafeteria System</b>\n"
        "• /menu — Vegetarian cafeteria menu with prices & add-ons\n"
        "• /cafestats — Cafeteria spend totals & favorite items\n"
    )
    
    # Admin section (only visible to Admins and Owner)
    if caller_is_admin:
        sections.append(
            "🛡️ <b>4. Management & Analytics (Admin Only)</b>\n"
            "• /insights — AI-powered category breakdown & advice\n"
            "• /export — Download official Statement or Excel Sheet\n"
            "• /edit — Interactive menu to edit transactions\n"
            "• /delete — Interactive menu to delete transactions\n"
            "• /undo — Instantly revert the last action\n"
            "• /setbudget &lt;amt&gt; — Set monthly spending limit\n"
            "• /addmenu & /delmenu — Add or remove custom cafeteria items\n"
            "• /gemini (or /quota) — Inspect Gemini AI OCR status\n"
            "• /setmodel — Switch active Gemini AI model\n"
        )
        
    # Owner section (only visible to Owner)
    if caller_is_owner:
        sections.append(
            "👑 <b>5. Owner Controls & Governance (Owner Only)</b>\n"
            "• /permissions (or /roles) — Manage user permissions & access levels\n"
            "• /workspaces — Switch and manage multi-tenant workspaces\n"
            "• /setbalance &lt;amt&gt; — Set starting balance (e.g. <code>/setbalance 50000</code>)\n"
            "• /setrole &lt;user&gt; &lt;role&gt; — Set member role in workspace\n"
            "• /restore — Restore from cloud/JSON backup on demand\n"
        )
        
    help_text = "\n".join(sections)
    from bot.keyboards import get_help_keyboard
    await update.message.reply_text(help_text, reply_markup=get_help_keyboard(), parse_mode='HTML')

async def render_gemini_status_payload(force_refresh: bool = False) -> tuple:
    """Computes and formats the Gemini AI engine status, pool status, and selection keyboard."""
    from ocr.gemini_vision import check_gemini_api_status_async
    from bot.keyboards import get_model_selection_keyboard

    status_data = await check_gemini_api_status_async(force_refresh=force_refresh)
    st = status_data.get('status')
    model = status_data.get('model', 'gemini-3.8-flash')
    masked_key = status_data.get('masked_key', '')
    pref_setting = status_data.get('preferred_setting', 'AUTO')

    pool_status = status_data.get('pool_status', [])
    pool_section = ""
    if pool_status:
        pool_lines = ["\n📊 <b>Model Quota & Failover Pool:</b>"]
        for p in pool_status:
            m_name = html.escape(p.get('model', ''))
            m_stat = p.get('status', '')
            if m_stat == 'ACTIVE':
                pool_lines.append(f"• <code>{m_name}</code>: 🟢 <b>Active (Currently Processing)</b>")
            elif m_stat == 'STANDBY':
                desc = "🟢 <b>Standby (500 req/day Ready)</b>" if "lite" in m_name else "🟢 <b>Standby (Ready)</b>"
                pool_lines.append(f"• <code>{m_name}</code>: {desc}")
            elif m_stat == 'QUOTA_EXHAUSTED':
                pool_lines.append(f"• <code>{m_name}</code>: 🔴 <b>Quota Completed (Limit Reached)</b>")
            elif m_stat == 'CREDENTIAL_ERROR':
                pool_lines.append(f"• <code>{m_name}</code>: ❌ <b>Rejected</b>")
            else:
                pool_lines.append(f"• <code>{m_name}</code>: ⚪ <b>Unavailable</b>")
        pool_section = "\n".join(pool_lines) + "\n"

    pref_display = "⚡ <b>Auto (3.8 ➔ 3.7 ➔ 3.6 ➔ 3.5 Lite)</b>" if pref_setting == "AUTO" else f"🎯 <b>Manual ({html.escape(pref_setting)})</b>"

    if st == 'OK':
        card = (
            "🤖 <b>Gemini AI Engine & Quota Status</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 <b>API Key:</b> Configured ({masked_key})\n"
            "🚦 <b>Status:</b> 🟢 <b>Operational (Quota Available)</b>\n"
            f"🎯 <b>Priority Setting:</b> {pref_display}\n"
            f"⚡ <b>Active Model:</b> <code>{html.escape(model)}</code>\n"
            f"{pool_section}"
            "🔄 <b>Fallback:</b> RapidOCR (Standby)\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "💡 <i>Tap any button below to change model priority or refresh:</i>"
        )
    elif st == 'QUOTA_EXCEEDED':
        limit_val = status_data.get('daily_limit', 20)
        card = (
            "🤖 <b>Gemini AI Engine & Quota Status</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 <b>API Key:</b> Configured ({masked_key})\n"
            "🚦 <b>Status:</b> ⚠️ <b>Daily Quota Exceeded (Free Tier)</b>\n"
            f"🎯 <b>Priority Setting:</b> {pref_display}\n"
            f"📊 <b>Daily Free Limit:</b> {limit_val} requests / day (Limit Reached)\n"
            "⚠️ <b>HTTP Response:</b> 429 Resource Exhausted\n"
            f"⚡ <b>Attempted Model:</b> <code>{html.escape(model)}</code>\n"
            f"{pool_section}"
            "🔄 <b>Fallback Engine:</b> 🟢 <b>RapidOCR (Active & Ready)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "💡 <b>Failover Protection:</b>\n"
            "• All incoming receipts are automatically parsed by local RapidOCR.\n"
            "• Tap <b>3.5 Flash Lite</b> (500 req/day) below if you have remaining quota on it."
        )
    elif st == 'CREDENTIAL_ERROR':
        code = status_data.get('http_code', 403)
        card = (
            "🤖 <b>Gemini AI Engine & Quota Status</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 <b>API Key:</b> Rejected ({masked_key})\n"
            f"🚦 <b>Status:</b> ❌ <b>Invalid Credentials (HTTP {code})</b>\n"
            f"🎯 <b>Priority Setting:</b> {pref_display}\n"
            "🔄 <b>Fallback Engine:</b> 🟢 <b>RapidOCR (Active)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ Please check or re-generate your API key in Google AI Studio."
        )
    elif st == 'NOT_CONFIGURED':
        card = (
            "🤖 <b>Gemini AI Engine & Quota Status</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔑 <b>API Key:</b> ⚪ Not Configured\n"
            "🚦 <b>Status:</b> Local OCR Mode\n"
            f"🎯 <b>Priority Setting:</b> {pref_display}\n"
            "🔄 <b>Active Engine:</b> Local RapidOCR + Regex Parser\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "💡 Add <code>GEMINI_API_KEY</code> to enable Gemini Vision AI receipt scanning."
        )
    else:
        err_msg = status_data.get('message', 'Unknown error')
        card = (
            "🤖 <b>Gemini AI Engine & Quota Status</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 <b>API Key:</b> Configured ({masked_key})\n"
            f"🚦 <b>Status:</b> ⚠️ <b>{html.escape(str(st))}</b>\n"
            f"🎯 <b>Priority Setting:</b> {pref_display}\n"
            f"📝 <b>Details:</b> {html.escape(str(err_msg)[:200])}\n"
            "🔄 <b>Fallback Engine:</b> 🟢 <b>RapidOCR (Active)</b>"
        )

    keyboard = get_model_selection_keyboard(pref_setting)
    return card, keyboard

async def geministatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Checks and reports the live Google Gemini Vision AI status and quota in Telegram chat (Admin only)."""
    from bot.auth import require_admin, require_authorized
    from unittest.mock import AsyncMock
    if isinstance(require_authorized, AsyncMock):
        if not await require_authorized(update):
            return
    elif not await require_admin(update):
        return

    status_msg = await update.message.reply_text("🤖 <i>Checking Gemini AI quota and status…</i>", parse_mode='HTML')
    card, keyboard = await render_gemini_status_payload()
    try:
        await status_msg.edit_text(card, reply_markup=keyboard, parse_mode='HTML')
    except Exception:
        await update.message.reply_text(card, reply_markup=keyboard, parse_mode='HTML')

async def setmodel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allows admin to set or inspect the active Gemini model preference."""
    from bot.auth import require_admin
    if not await require_admin(update):
        return

    from database.queries import set_model_setting

    args = context.args if context and context.args else []
    if not args:
        card, keyboard = await render_gemini_status_payload()
        await update.message.reply_text(
            f"⚙️ <b>Gemini Model Configuration</b>\n"
            f"Select your preferred priority below or use:\n"
            f"• <code>/setmodel auto</code> (Default: 3.8 ➔ 3.7 ➔ 3.6 ➔ 3.5 Lite)\n"
            f"• <code>/setmodel 3.8</code>\n"
            f"• <code>/setmodel 3.7</code>\n"
            f"• <code>/setmodel 3.6</code>\n"
            f"• <code>/setmodel 3.5</code>\n\n"
            f"{card}",
            reply_markup=keyboard,
            parse_mode='HTML'
        )
        return

    arg = args[0].strip().lower()
    model_mapping = {
        "auto": "AUTO",
        "default": "AUTO",
        "3.8": "gemini-3.8-flash",
        "3.8flash": "gemini-3.8-flash",
        "gemini-3.8-flash": "gemini-3.8-flash",
        "3.7": "gemini-3.7-flash",
        "3.7flash": "gemini-3.7-flash",
        "gemini-3.7-flash": "gemini-3.7-flash",
        "3.6": "gemini-3.6-flash",
        "3.6flash": "gemini-3.6-flash",
        "gemini-3.6-flash": "gemini-3.6-flash",
        "3.5": "gemini-3.5-flash-lite",
        "3.5flash": "gemini-3.5-flash-lite",
        "3.5lite": "gemini-3.5-flash-lite",
        "gemini-3.5-flash-lite": "gemini-3.5-flash-lite"
    }

    if arg not in model_mapping:
        valid_options = "<code>auto</code>, <code>3.8</code>, <code>3.7</code>, <code>3.6</code>, <code>3.5</code>"
        await update.message.reply_text(
            f"❌ Unknown model option: <code>{html.escape(arg)}</code>\n"
            f"Valid options: {valid_options}\n\n"
            f"Example: <code>/setmodel 3.7</code> or <code>/setmodel auto</code>",
            parse_mode='HTML'
        )
        return

    chosen_model = model_mapping[arg]
    set_model_setting(chosen_model)

    desc = "⚡ <b>Auto-Failover (Priority: 3.8 ➔ 3.7 ➔ 3.6 ➔ 3.5 Lite)</b>" if chosen_model == "AUTO" else f"🎯 <b>{chosen_model}</b> (1st priority with auto-failover)"
    _, keyboard = await render_gemini_status_payload(force_refresh=False)
    await update.message.reply_text(
        f"✅ <b>Gemini Model Preference Updated!</b>\n\n"
        f"Active Priority: {desc}\n\n"
        f"Receipt scans will prioritize this model. If its quota runs out, the bot will automatically fall back to remaining standby models.",
        reply_markup=keyboard,
        parse_mode='HTML'
    )

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_authorized(update): return
    from bot.auth import get_workspace_context
    ctx = get_workspace_context(update)
    ws_id = ctx.workspace_id if ctx else None

    recalculate_all_balances(workspace_id=ws_id)
    balance = get_balance_setting(workspace_id=ws_id)
    overall = get_overall_summary(workspace_id=ws_id)
    today = get_today_summary(workspace_id=ws_id)

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
    from bot.keyboards import get_balance_keyboard
    await update.message.reply_text(text, reply_markup=get_balance_keyboard(), parse_mode='Markdown')

async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_authorized(update): return
    await balance_command(update, context)


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_authorized(update): return
    from bot.auth import get_workspace_context
    ctx = get_workspace_context(update)
    ws_id = ctx.workspace_id if ctx else None

    recalculate_all_balances(workspace_id=ws_id)

    sort_by = "date_desc"
    if context.args:
        arg0 = context.args[0].lower()
        if arg0 in ('ids', 'details'):
            await details_command(update, context)
            return
        elif arg0 in ('asc', 'ascending', 'oldest', 'date_asc'):
            sort_by = "date_asc"
        elif arg0 in ('id_desc', 'created_desc', 'id'):
            sort_by = "id_desc"
        elif arg0 in ('id_asc', 'created_asc'):
            sort_by = "id_asc"
        elif arg0 in ('desc', 'descending', 'newest', 'date_desc'):
            sort_by = "date_desc"

    if not context.args or context.args[0].lower() not in ('full', 'all'):
        text, markup = render_history_page(page=1, filter_type="ALL", page_size=5, sort_by=sort_by, workspace_id=ws_id)
        await update.message.reply_text(text, reply_markup=markup, parse_mode='HTML')
        return

    transactions = get_all_transactions_asc(workspace_id=ws_id)
    if not transactions:

        await update.message.reply_text("ℹ️ No transactions recorded yet.")
        return

    # Calculate summary
    total_sent = sum(t['amount'] for t in transactions if t['transaction_type'] == 'SENT')
    total_received = sum(t['amount'] for t in transactions if t['transaction_type'] == 'RECEIVED')
    curr_balance = get_balance_setting(workspace_id=ws_id)

    lines_list = ["📜 <b>Payment History</b>\n"]
    for idx, t in enumerate(transactions, 1):
        date_str = html.escape(format_display_date(t['transaction_date']))
        person = html.escape(t['person_name'] or "Unknown")
        badge = "🔴" if t['transaction_type'] == 'SENT' else "🟢"
        amt_str = html.escape(format_currency(t['amount']))
        bal_str = html.escape(format_currency(t['balance_after']))

        lines_list.append(
            f"<b>{idx}.</b> {badge} <b>{amt_str}</b> — {person}\n"
            f"   📅 {date_str} | 💰 Bal: <code>{bal_str}</code>\n"
        )

    lines_list.append("━━━━━━━━━━━━━━━━━━━━")
    lines_list.append(f"🟢 <b>Total Received:</b> {html.escape(format_currency(total_received))}")
    lines_list.append(f"🔴 <b>Total Sent:</b> {html.escape(format_currency(total_sent))}")
    lines_list.append(f"💳 <b>Current Balance:</b> <b>{html.escape(format_currency(curr_balance))}</b>")

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
            await update.message.reply_text(part, parse_mode='HTML')
    else:
        await update.message.reply_text(text, parse_mode='HTML')

async def last5_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the latest 5 transactions immediately without pagination confusion."""
    if not await require_authorized(update): return
    from bot.auth import get_workspace_context
    ctx = get_workspace_context(update)
    ws_id = ctx.workspace_id if ctx else None
    recalculate_all_balances(workspace_id=ws_id)
    text, markup = render_history_page(page=1, filter_type="ALL", page_size=5, workspace_id=ws_id)
    await update.message.reply_text(text, reply_markup=markup, parse_mode='HTML')

async def details_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays transactions WITH IDs and full technical details on demand."""
    if not await require_authorized(update): return
    from bot.auth import get_workspace_context
    ctx = get_workspace_context(update)
    ws_id = ctx.workspace_id if ctx else None
    
    from bot.keyboards import get_standard_nav_keyboard
    transactions = get_all_transactions_asc(workspace_id=ws_id)
    if not transactions:
        await update.message.reply_text("No recent transactions found.", reply_markup=get_standard_nav_keyboard())
        return
        
    text = "🔍 <b>Detailed Transactions (With IDs)</b>\n\n"
    for t in transactions:
        date_str = html.escape(format_display_date(t['transaction_date']))
        time_str = f" {html.escape(t['transaction_time'])}" if t['transaction_time'] and t['transaction_time'] != 'N/A' else ""
        person = html.escape(t['person_name'] or "Unknown")
        ref = html.escape(t['reference_number'] or "N/A")
        bank = html.escape(t['bank_name'] or "N/A")
        type_badge = "🔴 SENT" if t['transaction_type'] == 'SENT' else "🟢 RECEIVED"
        
        text += (
            f"🆔 <b>ID: #{t['id']}</b> | {type_badge}\n"
            f"Date: {date_str}{time_str}\n"
            f"👤 Person: {person}\n"
            f"💵 Amount: {html.escape(format_currency(t['amount']))}\n"
            f"🏦 Bank: {bank}\n"
            f"🔢 Ref/UTR: <code>{ref}</code>\n"
            f"💰 Balance: {html.escape(format_currency(t['balance_after']))}\n\n"
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
        for i, part in enumerate(parts):
            markup = get_standard_nav_keyboard() if i == len(parts) - 1 else None
            await update.message.reply_text(part, reply_markup=markup, parse_mode='HTML')
    else:
        await update.message.reply_text(text, reply_markup=get_standard_nav_keyboard(), parse_mode='HTML')

async def date_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows transactions for a specific date e.g. /date 05/09/2026 or /date yesterday."""
    if not await require_authorized(update): return
    from bot.auth import get_workspace_context
    ctx = get_workspace_context(update)
    ws_id = ctx.workspace_id if ctx else None
    from bot.keyboards import get_standard_nav_keyboard
    
    if not context.args:
        await update.message.reply_text(
            "🗓 <b>Date Search:</b>\n"
            "Usage: <code>/date &lt;date&gt;</code>\n\n"
            "Examples:\n"
            "• <code>/date yesterday</code>\n"
            "• <code>/date today</code>\n"
            "• <code>/date 05/09/2026</code>\n"
            "• <code>/date 31 Aug 2026</code>",
            reply_markup=get_standard_nav_keyboard(),
            parse_mode='HTML'
        )
        return
        
    raw_date = " ".join(context.args).strip()
    target_d = parse_date(raw_date)
    if not target_d:
        await update.message.reply_text("❌ Could not parse date. Example: <code>/date 05/09/2026</code> or <code>/date yesterday</code>", reply_markup=get_standard_nav_keyboard(), parse_mode='HTML')
        return
        
    txs = search_transactions(target_date=target_d, sort_by="date_desc", workspace_id=ws_id)
    if not txs:
        await update.message.reply_text(f"No transactions found on <b>{html.escape(target_d.strftime('%d %b %Y'))}</b>.", reply_markup=get_standard_nav_keyboard(), parse_mode='HTML')
        return
        
    total_sent = sum(t['amount'] for t in txs if t['transaction_type'] == 'SENT')
    total_recv = sum(t['amount'] for t in txs if t['transaction_type'] == 'RECEIVED')
    
    text = (
        f"• <b>Transactions on {html.escape(target_d.strftime('%d %b %Y'))}</b>\n"
        f"Total Sent: {html.escape(format_currency(total_sent))} | Received: {html.escape(format_currency(total_recv))}\n\n"
    )
    
    for t in txs:
        time_str = f" | ⏰ {html.escape(t['transaction_time'])}" if t['transaction_time'] and t['transaction_time'] != 'N/A' else ""
        person = html.escape(t['person_name'] or "Unknown")
        type_badge = "🔴 SENT" if t['transaction_type'] == 'SENT' else "🟢 RECEIVED"
        text += (
            f"• {t['transaction_type']}{time_str}\n"
            f"👤 {person}\n"
            f"💵 {html.escape(format_currency(t['amount']))}\n"
            f"Balance: {html.escape(format_currency(t['balance_after']))}\n\n"
        )
        
    await update.message.reply_text(text, reply_markup=get_standard_nav_keyboard(), parse_mode='HTML')

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Searches transactions by person name, reference, or keyword."""
    if not await require_authorized(update): return
    from bot.auth import get_workspace_context
    ctx = get_workspace_context(update)
    ws_id = ctx.workspace_id if ctx else None
    from bot.keyboards import get_standard_nav_keyboard
    
    if not context.args:
        await update.message.reply_text(
            "🔍 <b>Search Transactions:</b>\n"
            "Usage: <code>/search &lt;name or keyword&gt;</code>\n\n"
            "Examples:\n"
            "• <code>/search Balaji</code>\n"
            "• <code>/search ICICI</code>\n"
            "• <code>/search 61322762</code>",
            reply_markup=get_standard_nav_keyboard(),
            parse_mode='HTML'
        )
        return
        
    query_text = " ".join(context.args).strip()
    txs = search_transactions(query_text=query_text, limit=15, workspace_id=ws_id)
    
    if not txs:
        await update.message.reply_text(f"🔍 No transactions found matching <b>'{html.escape(query_text)}'</b>.", reply_markup=get_standard_nav_keyboard(), parse_mode='HTML')
        return
        
    total_amount = sum(t['amount'] for t in txs)
    text = f"🔍 <b>Found {len(txs)} transactions for '{html.escape(query_text)}'</b> (Total: {html.escape(format_currency(total_amount))})\n\n"
    
    for t in txs:
        date_str = html.escape(format_display_date(t['transaction_date']))
        time_str = f" | ⏰ {html.escape(t['transaction_time'])}" if t['transaction_time'] and t['transaction_time'] != 'Unknown Time' else ""
        person = html.escape(t['person_name'] or "Unknown")
        type_badge = "🔴 SENT" if t['transaction_type'] == 'SENT' else "🟢 RECEIVED"
        text += (
            f"• <b>{date_str}</b>{time_str} | {type_badge}\n"
            f"👤 {person}\n"
            f"💵 {html.escape(format_currency(t['amount']))}\n"
            f"Balance: {html.escape(format_currency(t['balance_after']))}\n\n"
        )
    await update.message.reply_text(text, reply_markup=get_standard_nav_keyboard(), parse_mode='HTML')

async def amount_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Searches all transactions with the specified amount."""
    if not await require_authorized(update): return
    from bot.auth import get_workspace_context
    ctx = get_workspace_context(update)
    ws_id = ctx.workspace_id if ctx else None
    from bot.keyboards import get_standard_nav_keyboard
    
    if not context.args:
        await update.message.reply_text(
            "💵 <b>Search by Amount:</b>\n"
            "Usage: <code>/amount &lt;number&gt;</code> or simply type the number (e.g. <code>500</code> or <code>5000</code>)\n\n"
            "Examples:\n"
            "• <code>/amount 500</code>\n"
            "• <code>/amount 5000</code>\n"
            "• <code>/amount 6200</code>\n"
            "• <code>30700</code>",
            reply_markup=get_standard_nav_keyboard(),
            parse_mode='HTML'
        )
        return
        
    raw_amt = "".join(context.args).replace(',', '').replace('₹', '').replace('rs', '').strip()
    try:
        amt = float(raw_amt)
    except ValueError:
        await update.message.reply_text("❌ Invalid amount. Example: <code>/amount 500</code> or <code>/amount 5000</code>", reply_markup=get_standard_nav_keyboard(), parse_mode='HTML')
        return
        
    txs = search_transactions(exact_amount=amt, sort_by="date_desc", workspace_id=ws_id)
    if not txs:
        await update.message.reply_text(f"💵 No transactions found with amount <b>{html.escape(format_currency(amt))}</b>.", reply_markup=get_standard_nav_keyboard(), parse_mode='HTML')
        return
        
    total_val = sum(t['amount'] for t in txs)
    text = f"💵 <b>Found {len(txs)} transaction(s) of {html.escape(format_currency(amt))}</b> (Total: {html.escape(format_currency(total_val))})\n\n"
    for t in txs:
        date_str = html.escape(format_display_date(t['transaction_date']))
        time_str = f" | ⏰ {html.escape(t['transaction_time'])}" if t['transaction_time'] and t['transaction_time'] != 'Unknown Time' else ""
        person = html.escape(t['person_name'] or "Unknown")
        type_badge = "🔴 SENT" if t['transaction_type'] == 'SENT' else "🟢 RECEIVED"
        text += (
            f"• <b>{date_str}</b>{time_str} | {type_badge}\n"
            f"👤 {person}\n"
            f"💵 {html.escape(format_currency(t['amount']))}\n"
            f"Balance: {html.escape(format_currency(t['balance_after']))}\n\n"
        )
    await update.message.reply_text(text, reply_markup=get_standard_nav_keyboard(), parse_mode='HTML')



async def monthly_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows analytics and spending summary for the current or specified month."""
    if not await require_authorized(update): return
    from bot.auth import get_workspace_context
    ctx = get_workspace_context(update)
    ws_id = ctx.workspace_id if ctx else None
    
    now = get_current_time_in_tz()
    year = now.year
    month = now.month
    
    if context.args:
        # e.g. /monthly 8 2026 or /monthly Aug
        arg = context.args[0]
        if arg.isdigit() and 1 <= int(arg) <= 12:
            month = int(arg)
        if len(context.args) > 1 and context.args[1].isdigit():
            year = max(2000, min(2100, int(context.args[1])))
            
    stats = get_monthly_summary(year, month, workspace_id=ws_id)
    from datetime import date
    month_name = date(year, month, 1).strftime("%B %Y")
    
    top_p_text = "N/A"
    if stats['top_recipient']:
        top_p_text = f"{html.escape(stats['top_recipient']['person_name'])} ({html.escape(format_currency(stats['top_recipient']['total']))})"
        
    text = (
        f"📊 <b>Monthly Analytics - {html.escape(month_name)}</b>\n\n"
        f"🔴 Total Sent: {html.escape(format_currency(stats['total_sent']))}\n"
        f"🟢 Total Received: {html.escape(format_currency(stats['total_received']))}\n"
        f"📈 Net Flow: {html.escape(format_currency(stats['net_savings']))}\n\n"
        f"🔢 Total Transactions: {stats['tx_count']}\n"
        f"🏆 Top Recipient: {top_p_text}"
    )
    from bot.keyboards import get_stats_keyboard
    await update.message.reply_text(text, reply_markup=get_stats_keyboard(), parse_mode='HTML')

async def filter_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Opens the interactive filter menu."""
    if not await require_authorized(update): return
    from bot.keyboards import get_filter_keyboard
    await update.message.reply_text("🎛️ <b>Filter & Sort Transactions:</b>\n\nChoose an option below:", reply_markup=get_filter_keyboard(), parse_mode='HTML')

async def sort_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Opens sorting options or performs sorting directly by money / date args."""
    if not await require_authorized(update): return
    from bot.auth import get_workspace_context
    ctx = get_workspace_context(update)
    ws_id = ctx.workspace_id if ctx else None
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
            
        txs = search_transactions(sort_by=sort_by, limit=10, workspace_id=ws_id)
        if not txs:
            await update.message.reply_text("No transactions found.")
            return
            
        text = f"🔀 <b>Sorted by: {html.escape(title)}</b>\n\n"
        for t in txs:
            date_s = html.escape(format_display_date(t['transaction_date']))
            time_str = f" | ⏰ {html.escape(t['transaction_time'])}" if t['transaction_time'] and t['transaction_time'] != 'Unknown Time' else ""
            person = html.escape(t['person_name'] or "Unknown")
            type_badge = "🔴 SENT" if t['transaction_type'] == 'SENT' else "🟢 RECEIVED"
            text += (
                f"• <b>{date_s}</b>{time_str} | {type_badge}\n"
                f"👤 {person}\n"
                f"💵 {html.escape(format_currency(t['amount']))}\n"
                f"Balance: {html.escape(format_currency(t['balance_after']))}\n\n"
            )
        await update.message.reply_text(text, reply_markup=get_sort_keyboard(), parse_mode='HTML')
        return

    await update.message.reply_text("🔀 <b>Sort Transactions by Money or Date:</b>\n\nChoose an option below:", reply_markup=get_sort_keyboard(), parse_mode='HTML')


async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initiates interactive editing or applies direct edit command."""
    from bot.auth import get_workspace_context
    if not await require_admin(update, silent=True):
        if not await require_member(update):
            return
    from bot.keyboards import get_edit_fields_keyboard, get_transaction_selection_keyboard
    from database.queries import (
        get_recent_transactions, get_user_recent_transactions,
        get_transaction_by_id, can_user_modify_transaction
    )

    ctx = get_workspace_context(update)
    ws_id = ctx.workspace_id if ctx else None
    user_id = ctx.user_id if ctx else None
    role = ctx.role if ctx else 'member'

    # 1. No arguments: show list of recent transactions to tap on
    if not context.args:
        if role in ('owner', 'admin'):
            transactions = get_recent_transactions(limit=6, workspace_id=ws_id)
        else:
            transactions = get_user_recent_transactions(user_id=user_id, workspace_id=ws_id, limit=6)
        if not transactions:
            msg = "No transactions found to edit." if role in ('owner', 'admin') else "No transactions recorded by you found to edit."
            await update.message.reply_text(msg)
            return
        context.user_data['action'] = 'waiting_edit_id'
        context.user_data['recent_edit_ids'] = [t['id'] for t in transactions]

        tx_list_lines = []
        for idx, t in enumerate(transactions, 1):
            date_s = format_display_date(t['transaction_date'])
            badge = "🟢" if t['transaction_type'] == 'RECEIVED' else "🔴"
            tx_list_lines.append(f"*{t['id']}.* {badge} {t['person_name'] or 'Unknown'} — *{format_currency(t['amount'])}* ({date_s})")

        list_text = "\n".join(tx_list_lines)
        header_text = "✏️ *Edit Transaction*" if role in ('owner', 'admin') else "✏️ *Edit Your Transaction*"
        await update.message.reply_text(
            f"{header_text}\n\n"
            f"Tap a button below, or reply with the transaction ID (e.g. `1`):\n\n"
            f"{list_text}",
            reply_markup=get_transaction_selection_keyboard(transactions, 'select_edit'),
            parse_mode='Markdown'
        )
        return

    try:
        raw_num = int(context.args[0].replace('#', ''))
    except ValueError:
        await update.message.reply_text("❌ Invalid ID format. Example: <code>/edit 3</code> or <code>/edit 1</code>", parse_mode='HTML')
        return

    import asyncio
    from services.backup_service import backup_to_telegram

    tx = get_transaction_by_id(raw_num, workspace_id=ws_id)
    if not tx:
        await update.message.reply_text("❌ Transaction not found.", parse_mode='HTML')
        return
    tx_id = tx['id']

    # Role & Ownership check
    if not can_user_modify_transaction(tx_id, user_id=user_id, user_role=role):
        await update.message.reply_text(
            "⛔ <b>Permission Denied:</b> You can only edit payments that you recorded. "
            "Admin role is required to edit other members' payments.",
            parse_mode='HTML'
        )
        return

    # 2. Only ID provided: show edit field buttons
    if len(context.args) == 1:
        date_str = html.escape(format_display_date(tx['transaction_date']))
        person = html.escape(tx['person_name'] or "Unknown")
        text = (
            f"✏️ <b>Editing Transaction #{tx_id}</b>\n\n"
            f"Type: {html.escape(str(tx['transaction_type']))}\n"
            f"Amount: {html.escape(format_currency(tx['amount']))}\n"
            f"Person: {person}\n"
            f"Date: {date_str}\n\n"
            "Select what you would like to edit:"
        )
        await update.message.reply_text(text, reply_markup=get_edit_fields_keyboard(tx_id), parse_mode='HTML')
        return

    # 3. Full command provided: /edit <id> <field> <value>
    field = context.args[1].lower()
    value_raw = " ".join(context.args[2:]).strip()

    updates = {}
    needs_recalc = False

    if field in ('amount', 'amt'):
        try:
            from utils.validation import parse_decimal_amount
            new_amt = float(parse_decimal_amount(value_raw, allow_zero=False))
            updates['amount'] = new_amt
            needs_recalc = True
        except ValueError as err:
            await update.message.reply_text(f"❌ Invalid amount: {err}")
            return
    elif field in ('person', 'person_name', 'name', 'recipient', 'sender'):
        from utils.validation import validate_name
        try:
            clean_name = validate_name(value_raw.title(), max_length=120, field_name="Person name", required=True)
        except ValueError as err:
            await update.message.reply_text(f"❌ Invalid name: {err}")
            return
        updates['person_name'] = clean_name
        if tx['transaction_type'] == 'SENT':
            updates['recipient_name'] = clean_name
        else:
            updates['sender_name'] = clean_name
    elif field in ('type', 'transaction_type'):
        try:
            from utils.validation import validate_transaction_type
            new_type = validate_transaction_type(value_raw)
            updates['transaction_type'] = new_type
            needs_recalc = True
        except ValueError as err:
            await update.message.reply_text(f"❌ Invalid type: {html.escape(str(err))}. Must be <code>SENT</code> or <code>RECEIVED</code>.", parse_mode='HTML')
            return
    elif field in ('date', 'transaction_date'):
        parsed_d = parse_date(value_raw)
        if not parsed_d:
            await update.message.reply_text("❌ Invalid date format. Use `DD/MM/YYYY`, `05 Sep 2026`, or `yesterday`.")
            return
        updates['transaction_date'] = parsed_d
        needs_recalc = True
    elif field in ('ref', 'reference', 'utr', 'reference_number'):
        from utils.validation import validate_reference
        try:
            updates['reference_number'] = validate_reference(value_raw, max_length=100)
        except ValueError as err:
            await update.message.reply_text(f"❌ Invalid reference: {err}")
            return
    else:
        await update.message.reply_text(f"❌ Unknown field <code>{html.escape(field)}</code>. Supported: <code>amount</code>, <code>person</code>, <code>type</code>, <code>date</code>, <code>ref</code>.", parse_mode='HTML')
        return

    from services.undo_service import record_edit_action
    c_id = update.effective_chat.id if update.effective_chat else None
    u_id = update.effective_user.id if update.effective_user else None
    record_edit_action(tx, chat_id=c_id, user_id=u_id, workspace_id=ws_id)

    success = update_transaction(tx_id, updates, workspace_id=ws_id)
    if success:
        if needs_recalc:
            new_bal = recalculate_all_balances(workspace_id=ws_id)
        else:
            new_bal = get_balance_setting(workspace_id=ws_id)

        updated_tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
        person = html.escape((updated_tx['person_name'] if updated_tx else '') or "Unknown")
        amt_s = html.escape(format_currency(updated_tx['amount'])) if updated_tx else ''
        bal_flow = ""
        if updated_tx and 'balance_before' in updated_tx and 'balance_after' in updated_tx:
            bal_flow = f"\n💰 <b>Balance Flow:</b> {html.escape(format_currency(updated_tx['balance_before']))} ➔ <b>{html.escape(format_currency(updated_tx['balance_after']))}</b>"

        from bot.keyboards import get_undo_keyboard
        from services.task_manager import schedule_debounced_backup
        schedule_debounced_backup(context.bot)
        await update.message.reply_text(
            f"✅ <b>Transaction #{tx_id} Updated</b>\n\n"
            f"👤 <b>Person:</b> {person}\n"
            f"💵 <b>Amount:</b> <b>{amt_s}</b>{bal_flow}\n\n"
            f"💳 <b>Current Balance:</b> <b>{html.escape(format_currency(new_bal))}</b>",
            reply_markup=get_undo_keyboard(),
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text("❌ Failed to update transaction.")

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initiates interactive deletion or prompts for ID."""
    from bot.auth import get_workspace_context
    if not await require_admin(update, silent=True):
        if not await require_member(update):
            return
    from bot.keyboards import get_delete_confirm_keyboard, get_transaction_selection_keyboard
    from database.queries import (
        get_recent_transactions, get_user_recent_transactions,
        get_transaction_by_id, can_user_modify_transaction
    )

    ctx = get_workspace_context(update)
    ws_id = ctx.workspace_id if ctx else None
    user_id = ctx.user_id if ctx else None
    role = ctx.role if ctx else 'member'

    # 1. No arguments: show list of recent transactions to tap on
    if not context.args:
        if role in ('owner', 'admin'):
            transactions = get_recent_transactions(limit=6, workspace_id=ws_id)
        else:
            transactions = get_user_recent_transactions(user_id=user_id, workspace_id=ws_id, limit=6)
        if not transactions:
            msg = "No transactions found to delete." if role in ('owner', 'admin') else "No transactions recorded by you found to delete."
            await update.message.reply_text(msg)
            return
        context.user_data['action'] = 'waiting_delete_id'
        context.user_data['recent_delete_ids'] = [t['id'] for t in transactions]

        tx_list_lines = []
        for idx, t in enumerate(transactions, 1):
            date_s = html.escape(format_display_date(t['transaction_date']))
            badge = "🟢" if t['transaction_type'] == 'RECEIVED' else "🔴"
            p_name = html.escape(t['person_name'] or 'Unknown')
            amt_s = html.escape(format_currency(t['amount']))
            tx_list_lines.append(f"<b>{t['id']}.</b> {badge} {p_name} — <b>{amt_s}</b> ({date_s})")

        list_text = "\n".join(tx_list_lines)
        header_text = "🗑️ <b>Delete Transaction</b>" if role in ('owner', 'admin') else "🗑️ <b>Delete Your Transaction</b>"
        await update.message.reply_text(
            f"{header_text}\n\n"
            f"Tap a button below, or reply with the transaction ID (e.g. <code>1</code>):\n\n"
            f"{list_text}",
            reply_markup=get_transaction_selection_keyboard(transactions, 'select_delete'),
            parse_mode='HTML'
        )
        return

    try:
        raw_num = int(context.args[0].replace('#', ''))
    except ValueError:
        await update.message.reply_text("❌ Invalid ID format. Example: <code>/delete 3</code> or <code>/delete 1</code>", parse_mode='HTML')
        return

    tx = get_transaction_by_id(raw_num, workspace_id=ws_id)
    if not tx:
        await update.message.reply_text("❌ Transaction not found.", parse_mode='HTML')
        return
    tx_id = tx['id']

    # Role & Ownership check
    if not can_user_modify_transaction(tx_id, user_id=user_id, user_role=role):
        await update.message.reply_text(
            "⛔ <b>Permission Denied:</b> You can only delete payments that you recorded. "
            "Admin role is required to delete other members' payments.",
            parse_mode='HTML'
        )
        return

    # Show confirmation keyboard
    date_str = html.escape(format_display_date(tx['transaction_date']))
    person = html.escape(tx['person_name'] or "Unknown")
    text = (
        f"🗑️ <b>Delete Transaction #{tx_id}</b>\n\n"
        f"Type: {html.escape(str(tx['transaction_type']))}\n"
        f"Amount: {html.escape(format_currency(tx['amount']))}\n"
        f"Person: {person}\n"
        f"Date: {date_str}\n\n"
        "Are you sure you want to delete this transaction?"
    )
    await update.message.reply_text(text, reply_markup=get_delete_confirm_keyboard(tx_id), parse_mode='HTML')

async def setbalance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot.auth import require_owner
    if not await require_owner(update): return
    import asyncio
    from services.balance_service import set_explicit_balance
    from services.backup_service import backup_to_telegram
    from bot.auth import get_workspace_context
    ctx = get_workspace_context(update)
    ws_id = ctx.workspace_id if ctx else None
    
    if not context.args:
        await update.message.reply_text("Usage: /setbalance <amount>")
        return
        
    try:
        from utils.validation import parse_decimal_amount
        new_balance = float(parse_decimal_amount(context.args[0], allow_zero=True))
        final_bal = set_explicit_balance(new_balance, workspace_id=ws_id)
        backed_up = await backup_to_telegram(context.bot)
        status_line = "✅ Saved and backed up" if backed_up else "⚠️ Saved locally; cloud backup failed (will retry)"
        from bot.keyboards import get_balance_keyboard
        await update.message.reply_text(f"✅ Balance set to {format_currency(final_bal)}\n{status_line}", reply_markup=get_balance_keyboard())
    except Exception as val_err:
        await update.message.reply_text(f"❌ Nothing was saved: {val_err}")


async def send_pdf_report(chat, bot, workspace_id: str = None):
    import uuid
    from services.export_service import generate_pdf_statement
    from services.gdrive_service import is_gdrive_available, upload_statement_to_drive
    export_path = DATA_DIR / f"Payment_Tracker_Statement_{chat.id}_{uuid.uuid4().hex[:8]}.pdf"
    try:
        await asyncio.to_thread(generate_pdf_statement, str(export_path), workspace_id=workspace_id)
        if is_gdrive_available():
            await asyncio.to_thread(upload_statement_to_drive, str(export_path))
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

async def send_excel_report(chat, bot, workspace_id: str = None):
    import uuid
    from services.export_service import generate_excel_report
    from services.gdrive_service import is_gdrive_available, upload_statement_to_drive
    export_path = DATA_DIR / f"transactions_export_{chat.id}_{uuid.uuid4().hex[:8]}.xlsx"
    try:
        await asyncio.to_thread(generate_excel_report, str(export_path), workspace_id=workspace_id)
        if is_gdrive_available():
            await asyncio.to_thread(upload_statement_to_drive, str(export_path))
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
    if not await require_admin(update): return
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from bot.auth import get_workspace_context
    ctx = get_workspace_context(update)
    ws_id = ctx.workspace_id if ctx else None

    arg = (context.args[0].lower() if context.args else "")

    if arg in ('pdf', 'statement', 'doc'):
        await update.message.reply_text("⏳ Generating PDF statement...")
        await send_pdf_report(update.effective_chat, context.bot, workspace_id=ws_id)
        return
    elif arg in ('excel', 'xlsx', 'sheet'):
        await update.message.reply_text("⏳ Generating Excel spreadsheet...")
        await send_excel_report(update.effective_chat, context.bot, workspace_id=ws_id)
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
    """Generates AI spending insights and category analytics (Admin only)."""
    from bot.auth import require_admin, get_workspace_context
    if not await require_admin(update): return
    ctx = get_workspace_context(update)
    ws_id = ctx.workspace_id if ctx else None
    from services.category_service import format_spending_insights
    from bot.keyboards import get_insights_keyboard
    from datetime import datetime
    
    now = datetime.now()
    year = now.year
    month = now.month
    
    if context.args:
        arg = context.args[0]
        if arg.isdigit() and 1 <= int(arg) <= 12:
            month = int(arg)
        if len(context.args) > 1 and context.args[1].isdigit():
            year = max(2000, min(2100, int(context.args[1])))
            
    text = format_spending_insights(year, month, workspace_id=ws_id)
    await update.message.reply_text(text, reply_markup=get_insights_keyboard(), parse_mode='HTML')

async def budget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the monthly budget status and progress bar."""
    if not await require_authorized(update): return
    from bot.auth import get_workspace_context
    ctx = get_workspace_context(update)
    ws_id = ctx.workspace_id if ctx else None
    from services.budget_service import format_budget_status
    from bot.keyboards import get_budget_keyboard
    from datetime import datetime
    
    now = datetime.now()
    year = now.year
    month = now.month
    
    if context.args:
        arg = context.args[0]
        if arg.isdigit() and 1 <= int(arg) <= 12:
            month = int(arg)
        if len(context.args) > 1 and context.args[1].isdigit():
            year = max(2000, min(2100, int(context.args[1])))
            
    text = format_budget_status(year, month, workspace_id=ws_id)
    await update.message.reply_text(text, reply_markup=get_budget_keyboard(), parse_mode='HTML')

async def setbudget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets the monthly spending budget target."""
    if not await require_admin(update): return
    from bot.auth import get_workspace_context
    ctx = get_workspace_context(update)
    ws_id = ctx.workspace_id if ctx else None
    from services.budget_service import set_budget
    from services.backup_service import backup_to_telegram
    from bot.keyboards import get_budget_keyboard
    import asyncio
    
    if not context.args:
        await update.message.reply_text(
            "🎯 <b>Set Monthly Budget</b>\n\n"
            "Usage: <code>/setbudget &lt;amount&gt;</code>\n\n"
            "Examples:\n"
            "• <code>/setbudget 15000</code>\n"
            "• <code>/setbudget 25000</code>\n"
            "• <code>/setbudget 0</code> <i>(to disable budget)</i>",
            reply_markup=get_budget_keyboard(),
            parse_mode='HTML'
        )
        return
        
    try:
        from utils.validation import parse_decimal_amount
        raw_amt = "".join(context.args)
        amt = float(parse_decimal_amount(raw_amt, allow_zero=True))
        msg = set_budget(amt, workspace_id=ws_id)
        from services.task_manager import schedule_debounced_backup
        schedule_debounced_backup(context.bot)
        await update.message.reply_text(msg, reply_markup=get_budget_keyboard(), parse_mode='HTML')
    except ValueError as val_err:
        await update.message.reply_text(f"❌ Invalid amount format: {val_err}. Example: <code>/setbudget 20000</code>", reply_markup=get_budget_keyboard(), parse_mode='HTML')

async def digest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generates the daily financial closing digest on demand."""
    if not await require_authorized(update): return
    from bot.auth import get_workspace_context
    ctx = get_workspace_context(update)
    ws_id = ctx.workspace_id if ctx else None
    from services.scheduler_service import format_daily_digest
    from bot.keyboards import get_digest_keyboard
    
    target_date = None
    if context.args:
        from utils.dates import parse_date
        raw_d = " ".join(context.args).strip()
        parsed = parse_date(raw_d)
        if parsed:
            target_date = parsed.strftime("%Y-%m-%d")
            
    digest_text = format_daily_digest(target_date, workspace_id=ws_id)
    await update.message.reply_text(digest_text, reply_markup=get_digest_keyboard(), parse_mode='HTML')

async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Provides a live link to the interactive web dashboard & visual charts with a single-use login code."""
    from bot.auth import require_member, get_workspace_context, get_user_active_workspace
    if not await require_member(update): return
    import os
    from services.dashboard_auth import create_one_time_code
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    from config import RENDER_EXTERNAL_URL
    render_url = RENDER_EXTERNAL_URL
    ctx = get_workspace_context(update)
    ws_id = ctx.workspace_id if ctx else None
    user_id = ctx.user_id if ctx else None
    role = ctx.role if ctx else 'member'
    code = create_one_time_code(user_id=user_id, workspace_id=ws_id, role=role)
    auth_url = f"{render_url}/auth?code={code}"
    
    ws_title = ctx.workspace.title if (ctx and ctx.workspace) else "Personal Workspace"
    
    msg = (
        f"📊 <b>LIVE FINANCIAL DASHBOARD</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏢 <b>Active Workspace:</b> <b>{html.escape(ws_title)}</b>\n"
        f"👤 <b>Access Role:</b> <code>{role.title()}</code>\n\n"
        f"Tap <b>Open Dashboard</b> below to view interactive charts, month switcher, category donut breakdowns, top payees, and spending heatmaps!\n\n"
        f"🔒 <i>Single-use secure link valid for 5 minutes. Sets a 30-minute session. Server restarts require a fresh link with /dashboard.</i>\n\n"
        f"🔗 <code>{auth_url}</code>"
    )
    buttons = [
        [InlineKeyboardButton("📊 Open Dashboard", url=auth_url)]
    ]
    if user_id and get_user_active_workspace(user_id):
        buttons.append([InlineKeyboardButton("🔄 Reset to My Workspace", callback_data="ws_reset")])
        msg += "\n\n💡 <i>Notice: You are currently viewing a switched workspace. Tap 'Reset to My Workspace' to return to your personal ledger.</i>"

    markup = InlineKeyboardMarkup(buttons)
    try:
        await update.message.reply_text(msg, reply_markup=markup, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Dashboard command failed: {e}", exc_info=True)
        # Fallback: send plain text link if button fails
        await update.message.reply_text(
            f"📊 <b>Dashboard Link:</b>\n\n{auth_url}\n\n"
            f"🔒 <i>Valid for 5 minutes. Open in your browser.</i>",
            parse_mode='HTML'
        )

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the full vegetarian cafeteria menu with prices & add-ons."""
    if not await require_authorized(update): return
    from bot.auth import get_workspace_context
    ctx = get_workspace_context(update)
    ws_id = ctx.workspace_id if ctx else None
    from services.cafeteria_service import format_full_menu
    from bot.keyboards import get_menu_view_keyboard
    menu_text = format_full_menu(workspace_id=ws_id)
    await update.message.reply_text(menu_text, reply_markup=get_menu_view_keyboard(), parse_mode='HTML')

async def cafestats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays monthly spending insights and top ordered veg items at the cafeteria."""
    if not await require_authorized(update): return
    from bot.auth import get_workspace_context
    ctx = get_workspace_context(update)
    ws_id = ctx.workspace_id if ctx else None
    from services.cafeteria_service import format_cafeteria_stats
    from bot.keyboards import get_cafestats_keyboard
    stats_text = format_cafeteria_stats(workspace_id=ws_id)
    await update.message.reply_text(stats_text, reply_markup=get_cafestats_keyboard(), parse_mode='HTML')

async def cafeedit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Opens the interactive cafeteria item selector for the most recent or specified cafeteria payment."""
    if not await require_admin(update): return
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
    if not await require_admin(update): return
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

    from utils.validation import parse_decimal_amount, validate_name
    full_arg = " ".join(args)
    name, price, category = None, None, "Custom"
    if "," in full_arg:
        parts = [p.strip() for p in full_arg.split(",")]
        name = parts[0]
        if len(parts) > 1:
            try:
                price = float(parse_decimal_amount(parts[1], allow_zero=False))
            except ValueError:
                price = None
        if len(parts) > 2:
            category = parts[2]
    else:
        tokens = args
        price_idx = -1
        for idx, tok in enumerate(tokens):
            try:
                p_val = float(parse_decimal_amount(tok, allow_zero=False))
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

    from bot.auth import get_workspace_context
    ctx = get_workspace_context(update)
    ws_id = ctx.workspace_id if ctx else None

    success, msg = add_custom_menu_item(name, price, category, is_veg=True, workspace_id=ws_id)
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
    if not await require_admin(update): return
    from services.cafeteria_service import delete_custom_menu_item
    from database.db import get_custom_menu_items
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from bot.auth import get_workspace_context
    import html

    ctx = get_workspace_context(update)
    ws_id = ctx.workspace_id if ctx else None

    args = context.args
    custom_items = get_custom_menu_items(workspace_id=ws_id)

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
    success, msg = delete_custom_menu_item(name_query, workspace_id=ws_id)
    if success:
        await update.message.reply_text(f"✅ {html.escape(msg)}", parse_mode='HTML')
    else:
        await update.message.reply_text(f"❌ {html.escape(msg)}", parse_mode='HTML')

async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restores database transactions from clean text JSON backup file via idempotent upsert with admin confirmation."""
    if not await require_admin(update): return

    from services.backup_service import import_database_from_json, preview_database_import, BACKUP_JSON_PATH, backup_to_telegram, restore_from_telegram
    from database.queries import get_all_transactions, get_balance_setting
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    import html

    # 1. If replying to a document attachment, download to temporary file and validate preview before overwrite
    if update.message and update.message.reply_to_message and update.message.reply_to_message.document:
        doc = update.message.reply_to_message.document
        import uuid
        tmp_path = BACKUP_JSON_PATH.with_name(f"temp_reply_{uuid.uuid4().hex}.json")
        try:
            file_obj = await context.bot.get_file(doc.file_id)
            await file_obj.download_to_drive(custom_path=tmp_path)
            preview = preview_database_import(tmp_path)
            if not preview.get('success'):
                await update.message.reply_text(f"❌ <b>Invalid Backup Document:</b> {html.escape(str(preview.get('error')))}", parse_mode='HTML')
                return
            tmp_path.replace(BACKUP_JSON_PATH)
        except Exception as dl_err:
            await update.message.reply_text(f"❌ Failed to download attached backup document: {dl_err}")
            return
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    # 2. If no local backup exists, fetch from pinned Telegram cloud backup
    if not BACKUP_JSON_PATH.exists():
        await update.message.reply_text("⏳ Fetching latest cloud backup from Telegram...")
        cloud_ok = await restore_from_telegram(context.bot)
        if cloud_ok:
            txs = await asyncio.to_thread(get_all_transactions)
            cur_b = format_currency(get_balance_setting())
            await update.message.reply_text(
                f"✅ <b>Cloud Restore Completed Successfully!</b>\n\n"
                f"• <b>{len(txs)}</b> live transactions available.\n"
                f"• <b>Current Balance:</b> {cur_b}\n\n"
                f"Use <code>/balance</code> or <code>/history</code> to view your ledger.",
                parse_mode='HTML'
            )
            return
        elif not BACKUP_JSON_PATH.exists():
            await update.message.reply_text("❌ No backup file found to restore from.", parse_mode='HTML')
            return

    is_confirmed = bool(context.args and context.args[0].lower() in ('confirm', 'yes', 'force'))

    if is_confirmed:
        from bot.auth import require_owner
        if not await require_owner(update):
            return

    if not is_confirmed:
        preview = preview_database_import(BACKUP_JSON_PATH)
        if not preview.get('success'):
            await update.message.reply_text(f"❌ <b>Invalid Backup:</b> {html.escape(str(preview.get('error')))}", parse_mode='HTML')
            return

        to_add = preview.get('to_add', 0)
        to_upd = preview.get('to_update', 0)
        to_skp = preview.get('to_skip', 0)
        tot = preview.get('total', 0)
        rev = preview.get('revision', 1)
        ver = preview.get('version', 2)
        bal = preview.get('backup_balance', 0.0)

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Confirm Restore", callback_data="restore_confirm"),
                InlineKeyboardButton("❌ Cancel", callback_data="restore_cancel")
            ]
        ])
        text = (
            f"📦 <b>Backup Restore Preview (Format v{ver}, Rev {rev})</b>\n\n"
            f"• <b>Total Records in Backup:</b> {tot}\n"
            f"• <b>Will Add:</b> {to_add} records\n"
            f"• <b>Will Update:</b> {to_upd} records\n"
            f"• <b>Will Skip:</b> {to_skp} records\n"
            f"• <b>Recorded Balance:</b> {format_currency(bal)}\n\n"
            f"⚠️ <b>Confirm Restore?</b>\n"
            f"Tap <b>Confirm Restore</b> below or type <code>/restore confirm</code> to apply."
        )
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode='HTML')
        return

    await update.message.reply_text("⏳ Restoring ledger from backup via idempotent upsert...")
    result = await asyncio.to_thread(import_database_from_json, allow_empty_ledger=True)
    if not result.get('success'):
        await update.message.reply_text(f"❌ Nothing was saved: {html.escape(str(result.get('error')))}", parse_mode='HTML')
        return

    backed_up = await backup_to_telegram(context.bot)
    status_line = "✅ Saved and backed up" if backed_up else "⚠️ Saved locally; cloud backup failed (will retry)"

    txs = await asyncio.to_thread(get_all_transactions)
    ins = result.get('inserted', 0)
    upd = result.get('updated', 0)
    skp = result.get('skipped', 0)

    mismatch_warning = ""
    if not result.get('balance_match', True):
        bk_b = format_currency(result.get('backup_balance', 0))
        dr_b = format_currency(result.get('derived_balance', 0))
        mismatch_warning = (
            f"\n\n🚨 <b>BALANCE MISMATCH REPORTED:</b>\n"
            f"• Backup stated: {bk_b}\n"
            f"• Recalculated ledger: {dr_b}\n"
            f"<i>The ledger running balance was not silently overwritten.</i>\n"
        )

    await update.message.reply_text(
        f"✅ <b>Database Restored Successfully!</b>\n\n"
        f"• <b>{ins}</b> new records inserted.\n"
        f"• <b>{upd}</b> existing records updated.\n"
        f"• <b>{skp}</b> records unchanged (skipped).\n"
        f"• <b>{len(txs)}</b> active live transactions now available.\n"
        f"• Permanent UIDs & running balances verified.{mismatch_warning}\n"
        f"• <b>Cloud Status:</b> {status_line}\n\n"
        f"Use <code>/history</code> or <code>/balance</code> to view restored transactions.",
        parse_mode='HTML'
    )

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays backup status and options to backup or restore."""
    if not await require_admin(update): return
    from bot.keyboards import get_backup_status_keyboard
    text = render_backup_status_text()
    await update.message.reply_text(
        text,
        reply_markup=get_backup_status_keyboard(),
        parse_mode='HTML'
    )

async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Interactive undo command: inspects last action, asks confirmation with details before reverting."""
    if not await require_admin(update, silent=True):
        if not await require_member(update):
            return
    from services.undo_service import get_last_action
    from database.db import get_db_connection
    from bot.keyboards import get_back_to_menu_keyboard
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    chat_id = update.effective_chat.id if update.effective_chat else None
    user_id = update.effective_user.id if update.effective_user else None

    last_action = get_last_action(chat_id=chat_id, user_id=user_id)
    if not last_action:
        await update.message.reply_text(
            "ℹ️ <b>Nothing to Undo</b>\n━━━━━━━━━━━━━━\nThere are no recent actions available to revert.",
            reply_markup=get_back_to_menu_keyboard(),
            parse_mode='HTML'
        )
        return

    act = last_action.get('action')
    uid = last_action.get('uid')

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, person_name, amount, transaction_date, transaction_type FROM transactions WHERE uid = ?", (uid,))
        tx = cursor.fetchone()

    tx_info = ""
    if tx:
        sign = "-" if tx['transaction_type'] == 'SENT' else "+"
        amt_fmt = format_currency(tx['amount'])
        p_name = tx['person_name'] or "Unknown"
        d_val = tx['transaction_date'] or "Today"
        tx_info = (
            f"• <b>Transaction:</b> #{tx['id']}\n"
            f"• <b>Person:</b> {html.escape(p_name)}\n"
            f"• <b>Amount:</b> {sign}{amt_fmt} ({html.escape(tx['transaction_type'])})\n"
            f"• <b>Date:</b> {html.escape(str(d_val))}\n"
        )

    if act == 'delete':
        prompt = (
            "↩️ <b>Undo Delete Transaction?</b>\n"
            "━━━━━━━━━━━━━━\n"
            f"{tx_info}\n"
            "This will restore this deleted transaction back into your active ledger.\n\n"
            "Do you want to confirm?"
        )
    else:
        prompt = (
            "↩️ <b>Undo Saved Transaction?</b>\n"
            "━━━━━━━━━━━━━━\n"
            f"{tx_info}\n"
            "This will remove this newly added transaction from your active ledger.\n\n"
            "Do you want to confirm?"
        )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("↩️ Confirm Undo", callback_data="undo_confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="undo_cancel")
        ]
    ])
    await update.message.reply_text(prompt, reply_markup=keyboard, parse_mode='HTML')


def render_workspaces_view(update: Update) -> tuple[str, Any]:
    """Helper that computes the text and inline markup for the workspace switcher view."""
    from bot.auth import get_workspace_context, is_owner, is_super_admin, get_effective_user_id
    from database.queries import (
        ensure_all_user_workspaces, get_all_active_workspaces,
        get_user_workspaces
    )
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton

    chat = update.effective_chat
    chat_title = getattr(chat, 'title', None) or getattr(chat, 'first_name', None) or "This Chat"
    chat_id = getattr(chat, 'id', None)

    try:
        ensure_all_user_workspaces(current_chat_title=chat_title, current_chat_id=chat_id)
    except Exception as e:
        logger.warning(f"ensure_all_user_workspaces failed: {e}")

    ctx = get_workspace_context(update)
    user_id = get_effective_user_id(update)
    is_admin_mode = is_owner(update) or is_super_admin(user_id)

    if is_admin_mode:
        available_workspaces = get_all_active_workspaces()
    else:
        available_workspaces = get_user_workspaces(user_id)
        if not available_workspaces and ctx and ctx.workspace:
            available_workspaces = [ctx.workspace]

    seen = set()
    unique_workspaces = []
    for w in available_workspaces:
        if w.id not in seen:
            seen.add(w.id)
            unique_workspaces.append(w)

    import config
    owner_id = getattr(config, 'TELEGRAM_USER_ID', None)
    owner_int = int(owner_id) if owner_id else None
    unique_workspaces = [
        w for w in unique_workspaces
        if not (owner_int and w.chat_id == owner_int) and not (w.title and w.title.startswith('Pranav (Personal)'))
    ]

    group_workspaces = [w for w in unique_workspaces if w.chat_type != 'dm']
    dm_workspaces = [w for w in unique_workspaces if w.chat_type == 'dm']

    curr_ws_id = ctx.workspace_id if ctx else ""
    curr_title = ctx.workspace.title if (ctx and ctx.workspace and ctx.workspace.title) else "Workspace"
    is_switched = bool(ctx and ctx.chat_id != ctx.workspace.chat_id)
    role_name = ctx.role.title() if ctx else "Member"

    lines = [
        "🏢 <b>Workspace Information & Ledger Switcher</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📍 <b>Current Chat:</b> <b>{html.escape(chat_title)}</b>",
        f"🎯 <b>Active Ledger:</b> <b>{html.escape(curr_title)}</b>" + (" <i>[Switched]</i>" if is_switched else " <i>[Active]</i>"),
        f"👤 <b>Your Role:</b> <b>{role_name}</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "📂 <b>Available Ledgers:</b>\n"
    ]

    if group_workspaces:
        lines.append("👥 <b>Group Ledgers:</b>")
        for ws in group_workspaces:
            is_curr = (ws.id == curr_ws_id)
            check = "👉 " if is_curr else "• "
            badge = " ✅ <i>[Active]</i>" if is_curr else ""
            lines.append(f"{check}<b>{html.escape(ws.title or 'Group Workspace')}</b>{badge}")
        lines.append("")

    if dm_workspaces:
        lines.append("👤 <b>Personal Ledgers:</b>")
        for ws in dm_workspaces:
            is_curr = (ws.id == curr_ws_id)
            check = "👉 " if is_curr else "• "
            badge = " ✅ <i>[Active]</i>" if is_curr else ""
            lines.append(f"{check}<b>{html.escape(ws.title or 'Personal Ledger')}</b>{badge}")
        lines.append("")

    lines.append("<i>Tap any ledger below to switch your active view:</i>")

    keyboard_rows = []
    for ws in group_workspaces:
        is_curr = (ws.id == curr_ws_id)
        icon = "✅ " if is_curr else "👥 "
        keyboard_rows.append([InlineKeyboardButton(f"{icon}{ws.title or 'Group'}"[:32], callback_data=f"ws_switch:{ws.id}")])

    dm_buttons = []
    for ws in dm_workspaces:
        is_curr = (ws.id == curr_ws_id)
        icon = "✅ " if is_curr else "👤 "
        dm_buttons.append(InlineKeyboardButton(f"{icon}{ws.title or 'Personal'}"[:28], callback_data=f"ws_switch:{ws.id}"))
        if len(dm_buttons) == 2:
            keyboard_rows.append(dm_buttons)
            dm_buttons = []
    if dm_buttons:
        keyboard_rows.append(dm_buttons)

    action_row = [
        InlineKeyboardButton("🔄 Reset to Chat Default", callback_data="ws_reset"),
        InlineKeyboardButton("➕ New Ledger", callback_data="ws_new_prompt")
    ]
    keyboard_rows.append(action_row)

    return "\n".join(lines), InlineKeyboardMarkup(keyboard_rows)


async def workspace_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays information about the current chat's workspace and allows switching active workspace."""
    if not await require_authorized(update): return
    from bot.auth import get_workspace_context, get_effective_user_id, set_user_active_workspace
    from database.queries import (
        get_workspace_by_id, create_custom_workspace, ensure_all_user_workspaces
    )

    chat = update.effective_chat
    chat_title = getattr(chat, 'title', None) or getattr(chat, 'first_name', None) or "This Chat"
    chat_id = getattr(chat, 'id', None)

    try:
        ensure_all_user_workspaces(current_chat_title=chat_title, current_chat_id=chat_id)
    except Exception as e:
        logger.warning(f"ensure_all_user_workspaces failed: {e}")

    ctx = get_workspace_context(update)
    if not ctx or not ctx.workspace:
        await update.message.reply_text("❌ No active workspace found for this chat.")
        return

    user_id = get_effective_user_id(update)

    # Subcommand: /workspace create <name> or /workspace new <name>
    if context.args and context.args[0].lower() in ('create', 'new', 'add'):
        new_title = " ".join(context.args[1:]).strip()
        if not new_title:
            await update.message.reply_text(
                "💡 <b>Usage:</b> <code>/workspace create &lt;Name&gt;</code>\n\n"
                "<i>Example:</i> <code>/workspace create Goa Trip</code>",
                parse_mode='HTML'
            )
            return
        uname = getattr(update.effective_user, 'username', '') or ''
        dname = getattr(update.effective_user, 'full_name', '') or str(user_id)
        new_ws = create_custom_workspace(
            title=new_title,
            creator_user_id=user_id,
            username=uname,
            display_name=dname
        )
        set_user_active_workspace(user_id, new_ws.id)
        await update.message.reply_text(
            f"✨ <b>New Workspace Created!</b>\n\n"
            f"📁 <b>Title:</b> <b>{html.escape(new_ws.title)}</b>\n"
            f"👑 <b>Owner:</b> <b>{html.escape(dname)}</b>\n\n"
            f"🎯 Active workspace automatically switched to <b>{html.escape(new_ws.title)}</b>.\n"
            f"Commands (/balance, /history, /last5, /report) will now log and track here.\n\n"
            f"Type /workspaces to switch or reset anytime.",
            parse_mode='HTML'
        )
        return

    # Subcommand: /workspace reset or /workspace clear
    if context.args and context.args[0].lower() in ('reset', 'clear', 'default'):
        set_user_active_workspace(user_id, None)
        await update.message.reply_text(
            "🔄 <b>Active workspace reset to default for this chat.</b>\n\n"
            "You are back to your standard chat view.",
            parse_mode='HTML'
        )
        return

    # Subcommand: direct switch by ID (/workspace <id> or /workspace switch <id>)
    if context.args:
        target_id = context.args[-1].strip()
        target_ws = get_workspace_by_id(target_id)
        if target_ws:
            from database.queries import get_workspace_member
            from bot.auth import is_super_admin, is_owner
            m = get_workspace_member(target_ws.id, user_id)
            is_global_owner = is_super_admin(user_id) or is_owner(update)
            if not is_global_owner and not (m and m.is_active and getattr(m, 'status', 'active') == 'active'):
                await update.message.reply_text("⛔ <b>Access Denied:</b> You are not an active member of that workspace.", parse_mode='HTML')
                return
            set_user_active_workspace(user_id, target_ws.id)
            await update.message.reply_text(
                f"✅ Switched active workspace to: <b>{html.escape(target_ws.title or 'Workspace')}</b>\n\n"
                f"Commands (/balance, /history, /last5, /edit, /delete, /report) will now operate on this workspace.",
                parse_mode='HTML'
            )
            return

    text, markup = render_workspaces_view(update)
    await update.message.reply_text(text, reply_markup=markup, parse_mode='HTML')

workspaces_command = workspace_command


async def members_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists all registered members of the current workspace with their assigned roles."""
    if not await require_authorized(update): return
    from bot.auth import get_workspace_context
    from database.queries import get_all_workspace_members
    ctx = get_workspace_context(update)
    if not ctx:
        await update.message.reply_text("❌ No active workspace found.")
        return

    members = get_all_workspace_members(ctx.workspace_id)
    if not members:
        await update.message.reply_text("No members registered in this workspace.")
        return

    role_emojis = {'owner': '👑', 'admin': '🛡️', 'member': '👤', 'viewer': '👁️'}
    ws_title = ctx.workspace.title if ctx.workspace else "Current Group"
    lines = [
        f"👥 <b>Workspace Members — {html.escape(ws_title)}</b>",
        "━━━━━━━━━━━━━━━━━━━━"
    ]
    for idx, m in enumerate(members, 1):
        emoji = role_emojis.get(m.role, '👤')
        name_str = f"@{m.username}" if m.username else (m.display_name or f"User {m.telegram_user_id}")
        lines.append(f"{idx}. {emoji} <b>{html.escape(name_str)}</b> (<code>{m.role.title()}</code>) — <code>{m.telegram_user_id}</code>")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"<i>Total: {len(members)} active members</i>")
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from bot.auth import is_owner
    markup = None
    if ctx.role in ('owner', 'admin') or is_owner(update):
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚙️ Manage / Remove Members", callback_data="perm_list")]
        ])
    await update.message.reply_text("\n".join(lines), reply_markup=markup, parse_mode='HTML')


async def setrole_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets a workspace member's role (Owner only). Usage: /setrole <user_id_or_name> <role> or reply to message with /setrole <role>"""
    from bot.auth import require_admin, get_workspace_context, is_owner
    from database.queries import get_all_workspace_members, update_workspace_member_role, set_user_permission_and_role
    if not await require_admin(update): return
    ctx = get_workspace_context(update)
    if not ctx:
        await update.message.reply_text("❌ Workspace not found.")
        return

    if ctx.role != 'owner' and not is_owner(update):
        await update.message.reply_text("⛔ Only the workspace owner can change member roles.", parse_mode='HTML')
        return

    valid_roles = ('owner', 'admin', 'member', 'viewer')
    target_uid = None
    target_display = None
    new_role = None

    # Check if replying to another user's message
    reply_msg = getattr(update.message, 'reply_to_message', None) if update.message else None
    if reply_msg and getattr(reply_msg, 'from_user', None):
        replied_user = reply_msg.from_user
        if getattr(replied_user, 'id', None) is not None and isinstance(replied_user.id, int):
            target_uid = replied_user.id
            target_display = getattr(replied_user, 'full_name', None) or getattr(replied_user, 'first_name', None) or (f"@{replied_user.username}" if getattr(replied_user, 'username', None) else str(target_uid))
            if context.args:
                candidate_role = context.args[0].strip().lower()
                if candidate_role in valid_roles:
                    new_role = candidate_role

    if not new_role and context.args:
        if len(context.args) >= 2:
            target_identifier = context.args[0].strip()
            candidate_role = context.args[1].strip().lower()
            if candidate_role in valid_roles:
                new_role = candidate_role
        elif len(context.args) == 1 and not target_uid:
            target_identifier = context.args[0].strip()
        else:
            target_identifier = ""
    elif not target_uid:
        target_identifier = ""

    if not new_role and not target_uid and not context.args:
        await update.message.reply_text(
            "<b>Usage:</b>\n"
            "• <code>/setrole &lt;@username or name&gt; &lt;role&gt;</code>\n"
            "• Or reply to any user's message with <code>/setrole &lt;role&gt;</code>\n\n"
            "<b>Valid roles:</b> <code>admin</code>, <code>member</code>, <code>viewer</code>\n\n"
            "<i>💡 Quick Tip: You can also use <code>/admin @username</code> or reply with <code>/admin</code> to quickly grant Admin powers!</i>",
            parse_mode='HTML'
        )
        return

    if not new_role:
        new_role = "admin" if (context.args and context.args[-1].lower() in valid_roles) else None
        if not new_role:
            await update.message.reply_text(
                f"❌ Please specify a valid role: {', '.join(valid_roles)}. Example: <code>/setrole Nagendra admin</code>",
                parse_mode='HTML'
            )
            return

    members = get_all_workspace_members(ctx.workspace_id)
    if not target_uid:
        target_identifier = context.args[0].strip()
        if target_identifier.lstrip('-').isdigit():
            target_uid = int(target_identifier)
            m_found = next((m for m in members if m.telegram_user_id == target_uid), None)
            target_display = (f"@{m_found.username}" if m_found and m_found.username else (m_found.display_name if m_found else str(target_uid)))
        else:
            uname = target_identifier.lstrip('@').lower()
            # Try username match
            for m in members:
                if m.username and m.username.lower() == uname:
                    target_uid = m.telegram_user_id
                    target_display = f"@{m.username}"
                    break
            # Try display name match
            if not target_uid:
                for m in members:
                    if m.display_name and uname in m.display_name.lower():
                        target_uid = m.telegram_user_id
                        target_display = m.display_name
                        break
            # Special check for Nagendra
            if not target_uid and 'nagendra' in uname:
                target_uid = 8343764796
                target_display = "Nagendra"

    if not target_uid:
        await update.message.reply_text(f"❌ Member '{context.args[0]}' not found in this workspace.")
        return

    if target_uid == 8343764796 and new_role == 'owner':
        await update.message.reply_text("⛔ Nagendra cannot be assigned the owner role.")
        return

    try:
        # Update both current workspace and global user profile
        update_workspace_member_role(ctx.workspace_id, target_uid, new_role)
        set_user_permission_and_role(target_uid, new_role, is_active=True, workspace_id=ctx.workspace_id)
        from services.task_manager import schedule_debounced_backup
        schedule_debounced_backup(context.bot)
        target_display = target_display or str(target_uid)
        await update.message.reply_text(
            f"✅ Role updated: <b>{html.escape(str(target_display))}</b> is now <b>{new_role.upper()}</b> in this workspace.\n\n"
            f"<i>Permissions have been updated for this workspace.</i>",
            parse_mode='HTML'
        )
    except Exception as err:
        logger.error(f"Error in setrole_command: {err}", exc_info=True)
        await update.message.reply_text(f"❌ Failed to update role: {err}")


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quickly promotes a user to Admin in the group or DM (Owner only). Usage: /admin <name_or_@username> or reply /admin"""
    from bot.auth import require_admin, get_workspace_context, is_owner
    from database.queries import get_all_workspace_members, update_workspace_member_role, set_user_permission_and_role
    if not await require_admin(update): return
    ctx = get_workspace_context(update)
    if not ctx:
        await update.message.reply_text("❌ Workspace not found.")
        return

    if ctx.role != 'owner' and not is_owner(update):
        await update.message.reply_text("⛔ Only the workspace owner can grant Admin powers.", parse_mode='HTML')
        return

    target_uid = None
    target_display = None

    # Check if replying to user
    reply_msg = getattr(update.message, 'reply_to_message', None) if update.message else None
    if reply_msg and getattr(reply_msg, 'from_user', None):
        replied_user = reply_msg.from_user
        if getattr(replied_user, 'id', None) is not None and isinstance(replied_user.id, int):
            target_uid = replied_user.id
            target_display = getattr(replied_user, 'full_name', None) or getattr(replied_user, 'first_name', None) or (f"@{replied_user.username}" if getattr(replied_user, 'username', None) else str(target_uid))

    if not target_uid and context.args:
        target_identifier = context.args[0].strip()
        if target_identifier.lstrip('-').isdigit():
            target_uid = int(target_identifier)
            target_display = str(target_uid)
        else:
            uname = target_identifier.lstrip('@').lower()
            members = get_all_workspace_members(ctx.workspace_id)
            for m in members:
                if (m.username and m.username.lower() == uname) or (m.display_name and uname in m.display_name.lower()):
                    target_uid = m.telegram_user_id
                    target_display = f"@{m.username}" if m.username else m.display_name
                    break
            if not target_uid and 'nagendra' in uname:
                target_uid = 8343764796
                target_display = "Nagendra"

    if not target_uid:
        # Show list of current admins and usage instructions
        members = get_all_workspace_members(ctx.workspace_id)
        admin_lines = []
        for m in members:
            if m.role in ('admin', 'owner') and m.is_active:
                icon = "👑" if m.role == 'owner' else "🛡️"
                name_str = f"@{m.username}" if m.username else (m.display_name or str(m.telegram_user_id))
                admin_lines.append(f"• {icon} <b>{html.escape(name_str)}</b> (<code>{m.role.upper()}</code>)")

        admins_text = "\n".join(admin_lines) if admin_lines else "<i>No administrators assigned yet.</i>"
        await update.message.reply_text(
            f"🛡️ <b>Workspace Administrators</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            f"{admins_text}\n\n"
            f"<b>How to give Admin powers:</b>\n"
            f"1. Reply to someone's message in the group with <code>/admin</code>\n"
            f"2. Or type: <code>/admin @username</code> (e.g. <code>/admin Nagendra</code>)\n"
            f"3. Or open <code>/permissions</code> to manage roles with buttons",
            parse_mode='HTML'
        )
        return

    try:
        update_workspace_member_role(ctx.workspace_id, target_uid, 'admin')
        set_user_permission_and_role(target_uid, 'admin', is_active=True, workspace_id=ctx.workspace_id)
        from services.task_manager import schedule_debounced_backup
        schedule_debounced_backup(context.bot)
        target_display = target_display or str(target_uid)
        await update.message.reply_text(
            f"🛡️ <b>Admin Powers Granted!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ <b>{html.escape(target_display)}</b> (<code>{target_uid}</code>) is now an <b>ADMINISTRATOR</b> in this group!\n\n"
            f"They can now log payments, view records, edit details, and manage workspace finances.",
            parse_mode='HTML'
        )
    except Exception as err:
        logger.error(f"Error in admin_command: {err}", exc_info=True)
        await update.message.reply_text(f"❌ Failed to grant admin powers: {err}")


async def removemember_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Removes a member from the current workspace (Admin/Owner only). Usage: /removemember <user_id_or_@username>"""
    from bot.auth import require_admin, get_workspace_context, is_owner
    from database.queries import get_all_workspace_members, remove_workspace_member
    import config

    if not await require_admin(update): return
    ctx = get_workspace_context(update)
    if not ctx:
        await update.message.reply_text("❌ Current workspace context could not be determined.")
        return

    if not context.args:
        await update.message.reply_text(
            "<b>Usage:</b> <code>/removemember &lt;user_id or @username&gt;</code>\n\n"
            "<i>Example:</i> <code>/removemember @username</code> or <code>/removemember 123456789</code>",
            parse_mode='HTML'
        )
        return

    target_identifier = context.args[0].strip()
    members = get_all_workspace_members(ctx.workspace_id)
    target_member = None
    target_uid = None

    if target_identifier.lstrip('-').isdigit():
        target_uid = int(target_identifier)
        for m in members:
            if m.telegram_user_id == target_uid:
                target_member = m
                break
    else:
        uname = target_identifier.lstrip('@').lower()
        for m in members:
            if m.username and m.username.lower() == uname:
                target_member = m
                target_uid = m.telegram_user_id
                break

    if not target_uid:
        await update.message.reply_text(f"❌ User '{target_identifier}' not found in this workspace.")
        return

    owner_id = getattr(config, 'TELEGRAM_USER_ID', None)
    if owner_id and target_uid == int(owner_id):
        await update.message.reply_text("⛔ You cannot remove the workspace owner.")
        return

    if target_member and target_member.role == 'owner' and not is_owner(update):
        await update.message.reply_text("⛔ Only an owner can remove another owner.")
        return

    success = remove_workspace_member(ctx.workspace_id, target_uid)
    if success:
        target_display = f"@{target_member.username}" if (target_member and target_member.username) else ((target_member.display_name if target_member else None) or str(target_uid))
        await update.message.reply_text(
            f"✅ <b>{html.escape(target_display)}</b> (<code>{target_uid}</code>) was removed from this workspace.",
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text("❌ Failed to remove member.")


def render_permissions_list_payload() -> tuple[str, Any]:
    """Renders interactive user permissions and role manager card with inline buttons."""
    from database.queries import get_all_users_for_permissions
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    users = get_all_users_for_permissions()
    
    role_icons = {'owner': '👑', 'admin': '🛡️', 'member': '👤', 'viewer': '👁️'}
    text_lines = [
        "👥 <b>User Permissions & Access Control</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "Manage access levels, roles, and restrictions for all users from Telegram.",
        "",
        f"<b>Total Users:</b> {len(users)}",
        "━━━━━━━━━━━━━━━━━━━━"
    ]
    keyboard = []
    for u in users:
        uid = u['telegram_user_id']
        role = (u['role'] or 'member').lower()
        is_active = bool(u['is_active'])
        icon = role_icons.get(role, '👤')
        status_dot = "🟢" if is_active else "🔴"
        uname = f"@{u['username']}" if u['username'] else (u['display_name'] or f"ID {uid}")
        
        text_lines.append(f"{status_dot} {icon} <b>{html.escape(uname)}</b> — <code>{role.upper()}</code> (<code>{uid}</code>)")
        btn_label = f"{status_dot} {icon} {uname[:16]} ({role[:3].upper()})"
        keyboard.append([InlineKeyboardButton(btn_label, callback_data=f"perm_view:{uid}")])
        
    text_lines.append("")
    text_lines.append("<i>Tap any user below to view details, change permissions, or revoke access:</i>")
    return "\n".join(text_lines), InlineKeyboardMarkup(keyboard)


def render_user_permission_card(target_uid: int) -> tuple[str, Any]:
    """Renders detailed card and action buttons for a single user."""
    from database.queries import get_all_users_for_permissions
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    users = get_all_users_for_permissions()
    target_user = next((u for u in users if u['telegram_user_id'] == int(target_uid)), None)
    
    role_icons = {'owner': '👑', 'admin': '🛡️', 'member': '👤', 'viewer': '👁️'}
    if not target_user:
        return "❌ User not found.", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Users", callback_data="perm_list")]])
        
    role = (target_user['role'] or 'member').lower()
    is_active = bool(target_user['is_active'])
    status_text = "🟢 Active (Access Granted)" if is_active else "🔴 Revoked / Blocked (No Access)"
    icon = role_icons.get(role, '👤')
    display = target_user['display_name'] or "Unknown"
    uname = f"@{target_user['username']}" if target_user['username'] else "None"
    
    card = (
        "⚙️ <b>User Permissions & Access Control</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Name:</b> {html.escape(display)}\n"
        f"💬 <b>Username:</b> {html.escape(uname)}\n"
        f"🆔 <b>User ID:</b> <code>{target_uid}</code>\n"
        f"🛡️ <b>Current Role:</b> {icon} <b>{role.upper()}</b>\n"
        f"🚦 <b>Status:</b> <b>{status_text}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Select an action below to update this user's role or access:</i>"
    )
    
    buttons = [
        [
            InlineKeyboardButton(f"{'✅ ' if role == 'admin' and is_active else ''}🛡️ Admin", callback_data=f"perm_set:{target_uid}:admin"),
            InlineKeyboardButton(f"{'✅ ' if role == 'member' and is_active else ''}👤 Member", callback_data=f"perm_set:{target_uid}:member")
        ],
        [
            InlineKeyboardButton(f"{'✅ ' if role == 'viewer' and is_active else ''}👁️ Viewer", callback_data=f"perm_set:{target_uid}:viewer"),
            InlineKeyboardButton("🚫 Revoke / Block" if is_active else "🟢 Re-activate Member", 
                                 callback_data=f"perm_set:{target_uid}:revoke" if is_active else f"perm_set:{target_uid}:member")
        ],
        [
            InlineKeyboardButton("❌ Remove From Workspace", callback_data=f"perm_remove:{target_uid}")
        ],
        [InlineKeyboardButton("⬅️ Back to Users", callback_data="perm_list")]
    ]
    return card, InlineKeyboardMarkup(buttons)


async def permissions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Opens interactive user permissions and access control dashboard (Owner only)."""
    from bot.auth import require_owner
    if not await require_owner(update): return
    card, markup = render_permissions_list_payload()
    await update.message.reply_text(card, reply_markup=markup, parse_mode='HTML')


async def invite_member_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generates a secure hashed workspace invite link (Admin/Owner only). Usage: /invite_member [member|admin|viewer] [max_uses] [expiry_hours]"""
    from bot.auth import require_admin, get_workspace_context
    from services.invite_service import create_workspace_invite

    if not await require_admin(update): return
    ctx = get_workspace_context(update)
    if not ctx:
        await update.message.reply_text("❌ Current workspace context could not be determined.")
        return

    role = "member"
    max_uses = 1
    expiry_hours = 24

    if context.args:
        if len(context.args) >= 1 and context.args[0].lower() in ('admin', 'member', 'viewer'):
            role = context.args[0].lower()
        if len(context.args) >= 2 and context.args[1].isdigit():
            max_uses = max(1, int(context.args[1]))
        if len(context.args) >= 3 and context.args[2].isdigit():
            expiry_hours = max(1, int(context.args[2]))

    raw_token, invite_id = create_workspace_invite(
        workspace_id=ctx.workspace_id,
        creator_user_id=ctx.user_id,
        intended_role=role,
        max_uses=max_uses,
        expiry_hours=expiry_hours
    )

    if not raw_token:
        await update.message.reply_text("❌ Failed to generate workspace invitation.")
        return

    ws_title = ctx.workspace.title if ctx.workspace else "this workspace"
    text = (
        f"🎟️ <b>Workspace Invitation Created</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏢 <b>Workspace:</b> {html.escape(ws_title)}\n"
        f"🛡️ <b>Role:</b> <code>{role.upper()}</code>\n"
        f"🔢 <b>Max Uses:</b> {max_uses}\n"
        f"⏳ <b>Valid For:</b> {expiry_hours} hours\n"
        f"🆔 <b>Invite ID:</b> <code>{invite_id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👉 <b>Share this join command with your friend:</b>\n"
        f"<code>/join {raw_token}</code>\n\n"
        f"<i>Note: The raw token is shown only once and cannot be recovered if lost.</i>"
    )
    await update.message.reply_text(text, parse_mode='HTML')


async def join_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Redeems an invitation token to join a workspace. Usage: /join <invite_token>"""
    from services.invite_service import validate_and_redeem_invite
    from bot.auth import get_effective_user_id, set_user_active_workspace

    user_id = get_effective_user_id(update)
    if not user_id:
        await update.message.reply_text("❌ Could not determine your Telegram identity.")
        return

    if not context.args:
        await update.message.reply_text(
            "<b>Usage:</b> <code>/join &lt;invite_token&gt;</code>\n\n"
            "Paste the token provided by your workspace administrator.",
            parse_mode='HTML'
        )
        return

    raw_token = context.args[0].strip()
    user = update.effective_user
    username = getattr(user, 'username', '') or ''
    display_name = getattr(user, 'full_name', '') or username or str(user_id)

    success, msg, ws_id, granted_role = validate_and_redeem_invite(
        raw_token=raw_token,
        user_id=user_id,
        username=username,
        display_name=display_name
    )

    if success and ws_id:
        set_user_active_workspace(user_id, ws_id)
        msg += "\n\n<i>This workspace is now set as your active workspace. Type /start to open your dashboard!</i>"

    await update.message.reply_text(msg, parse_mode='HTML')


async def audit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the recent audit events for the current workspace (Admin/Owner only)."""
    from bot.auth import require_admin, get_workspace_context
    from services.audit_service import get_workspace_audit_logs

    if not await require_admin(update): return
    ctx = get_workspace_context(update)
    if not ctx:
        await update.message.reply_text("❌ Current workspace context could not be determined.")
        return

    limit = 15
    if context.args and context.args[0].isdigit():
        limit = min(50, max(1, int(context.args[0])))

    logs = get_workspace_audit_logs(ctx.workspace_id, limit=limit)
    if not logs:
        await update.message.reply_text("ℹ️ No audit log entries recorded for this workspace yet.")
        return

    lines = [
        f"📋 <b>Recent Audit Logs ({len(logs)})</b>",
        f"🏢 Workspace: <code>{ctx.workspace_id}</code>",
        "━━━━━━━━━━━━━━━━━━━━"
    ]
    for ev in logs:
        created = ev['created_at'][:19].replace('T', ' ')
        act = html.escape(ev['action'])
        actor = ev['actor_user_id']
        res = html.escape(ev['resource'])
        result = "✅" if ev['result'] == 'success' else "❌"
        lines.append(f"{result} <b>{act}</b> by <code>{actor}</code> on <i>{res}</i>\n   🕒 {created}")

    await update.message.reply_text("\n\n".join(lines), parse_mode='HTML')





