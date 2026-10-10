"""
Navigation callback handlers and dispatch table for the interactive bot menus.
"""

import asyncio
import html
import logging
from datetime import date
from telegram import Update
from telegram.ext import ContextTypes

from bot.commands import (
    render_home_menu_text,
    render_backup_status_text,
    render_contacts_ledger_text,
    render_monthly_closing_summary_text,
    render_recurring_overview_text,
    render_all_recurring_text,
    render_gemini_status_payload,
)
from bot.keyboards import (
    get_home_menu_keyboard,
    get_balance_keyboard,
    get_add_menu_keyboard,
    get_more_menu_keyboard,
    get_back_to_menu_keyboard,
    get_quick_add_keyboard,
    get_backup_status_keyboard,
    get_stats_keyboard,
    get_budget_keyboard,
    get_insights_keyboard,
    get_digest_keyboard,
    get_cafestats_keyboard,
    get_recurring_menu_keyboard,
    get_monthly_closing_keyboard,
)
from bot.auth import is_owner
from database.queries import (
    get_top_payees,
    get_transactions_by_date,
    get_monthly_summary,
)
from services.balance_service import (
    get_overall_summary,
    get_today_summary,
)
from utils.currency import format_currency
from utils.dates import get_current_time_in_tz

logger = logging.getLogger(__name__)


async def nav_home(query, context, parts, ws_id, ws_ctx, update=None):
    caller_id = query.from_user.id if query.from_user else None
    view_user_id = None if (ws_ctx and ws_ctx.role in ('owner', 'admin')) else caller_id
    is_switched = bool(ws_ctx and ws_ctx.workspace and getattr(ws_ctx.workspace, 'chat_id', None) is not None and ws_ctx.chat_id != ws_ctx.workspace.chat_id)
    text = render_home_menu_text(workspace_id=ws_id, user_id=view_user_id, switched=is_switched)
    await query.edit_message_text(text, reply_markup=get_home_menu_keyboard(), parse_mode='HTML')


async def nav_balance(query, context, parts, ws_id, ws_ctx, update=None):
    caller_id = query.from_user.id if query.from_user else None
    view_user_id = None if (ws_ctx and ws_ctx.role in ('owner', 'admin')) else caller_id
    overall = get_overall_summary(workspace_id=ws_id, user_id=view_user_id)
    today_stats = get_today_summary(workspace_id=ws_id, user_id=view_user_id)
    balance = overall.current_balance
    text = (
        "💰 <b>Live Account Balance</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"💳 <b>Current Balance:</b> <b>{format_currency(balance)}</b>\n\n"
        f"📅 <b>Today's Cash Flow:</b>\n"
        f"• 🟢 Received: +{format_currency(today_stats.total_received)}\n"
        f"• 🔴 Spent: -{format_currency(today_stats.total_sent)}\n"
        f"• 📈 Net Change: {format_currency(today_stats.net_change)}\n\n"
        f"📊 <b>All-Time Totals:</b>\n"
        f"• Total Received: {format_currency(overall.total_received)}\n"
        f"• Total Spent: {format_currency(overall.total_sent)}\n"
        f"• Total Records: {overall.transaction_count}\n"
        "━━━━━━━━━━━━━━"
    )
    await query.edit_message_text(text, reply_markup=get_balance_keyboard(), parse_mode='HTML')


async def nav_today(query, context, parts, ws_id, ws_ctx, update=None):
    caller_id = query.from_user.id if query.from_user else None
    view_user_id = None if (ws_ctx and ws_ctx.role in ('owner', 'admin')) else caller_id
    today_stats = get_today_summary(workspace_id=ws_id, user_id=view_user_id)
    today_date = get_current_time_in_tz().date()
    txs = get_transactions_by_date(today_date, workspace_id=ws_id, user_id=view_user_id)
    lines = [
        "📅 <b>Today's Transactions</b>",
        "━━━━━━━━━━━━━━"
    ]
    if not txs:
        lines.append("<i>No transactions logged today yet.</i>\n\n💡 Tip: Send a receipt screenshot or type <code>120 dosa</code>.")
    else:
        for idx, t in enumerate(txs, 1):
            badge = "🟢" if t['transaction_type'] == 'RECEIVED' else "🔴"
            arrow = "+" if t['transaction_type'] == 'RECEIVED' else "-"
            lines.append(
                f"<b>{idx}.</b> {badge} <b>{arrow}{format_currency(t['amount'])}</b> — {html.escape(t.get('person_name') or 'Unknown')}\n"
                f"   🏷 {html.escape(t.get('category') or 'General')} | 💼 Bal: <code>{format_currency(t.get('balance_after', 0))}</code>\n"
            )
    lines.append("━━━━━━━━━━━━━━")
    lines.append(f"🔴 Spent: {format_currency(today_stats.total_sent)} | 🟢 Recv: {format_currency(today_stats.total_received)}")
    await query.edit_message_text("\n".join(lines), reply_markup=get_balance_keyboard(), parse_mode='HTML')


async def nav_history(query, context, parts, ws_id, ws_ctx, update=None):
    from bot.commands import render_history_page
    caller_id = query.from_user.id if query.from_user else None
    view_user_id = None if (ws_ctx and ws_ctx.role in ('owner', 'admin')) else caller_id
    page = int(parts[2]) if len(parts) > 2 else 1
    ft = parts[3] if len(parts) > 3 else "ALL"
    sb = parts[4] if len(parts) > 4 else "date_desc"
    text, markup = render_history_page(page=page, filter_type=ft, page_size=5, sort_by=sb, workspace_id=ws_id, user_id=view_user_id)
    await query.edit_message_text(text, reply_markup=markup, parse_mode='HTML')


async def nav_history_noop(query, context, parts, ws_id, ws_ctx, update=None):
    pass


async def nav_add(query, context, parts, ws_id, ws_ctx, update=None):
    text = (
        "➕ <b>Add New Transaction</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Choose an input method to record your payment:\n\n"
        "• 📸 <b>Scan Receipt:</b> Send a screenshot or photo\n"
        "• ⌨️ <b>Manual Entry:</b> Log with guided prompts\n"
        "• 💬 <b>Natural Text:</b> Type quick natural messages\n"
        "• ⚡ <b>Quick Add:</b> Tap frequent payees & menu items\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    await query.edit_message_text(text, reply_markup=get_add_menu_keyboard(), parse_mode='HTML')


async def nav_more(query, context, parts, ws_id, ws_ctx, update=None):
    text = (
        "⚙️ <b>More Features & Tools</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Select an option to manage your account:\n\n"
        "• 🎯 <b>Budgets:</b> Set and track monthly targets\n"
        "• 🍽️ <b>Cafeteria:</b> Canteen dishes & order plates\n"
        "• ☁️ <b>Backup Status:</b> Cloud DR & recovery health\n"
        "• 🌐 <b>Web Dashboard:</b> Visual analytics & charts\n"
        "• 👥 <b>Contacts:</b> Counterparty ledger & balances\n"
        "• ℹ️ <b>Help & Commands:</b> Bot commands index\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    await query.edit_message_text(text, reply_markup=get_more_menu_keyboard(), parse_mode='HTML')


async def nav_add_scan(query, context, parts, ws_id, ws_ctx, update=None):
    text = (
        "📸 <b>Scan Payment Receipt</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Upload a receipt screenshot or photo directly into this chat.\n\n"
        "Supported apps: Google Pay, PhonePe, Paytm, CRED, BHIM, Amazon Pay, Super.money, and bank alerts.\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    await query.edit_message_text(text, reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')


async def nav_add_manual(query, context, parts, ws_id, ws_ctx, update=None):
    context.user_data['action'] = 'waiting_quick_text'
    text = (
        "⌨️ <b>Manual Transaction Entry</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Enter transaction details in format:\n"
        "<code>[Amount] [Payee]</code> (e.g. <code>250 Grocery</code>)\n"
        "or <code>+5000 Salary</code> for income.\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    await query.edit_message_text(text, reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')


async def nav_add_text(query, context, parts, ws_id, ws_ctx, update=None):
    text = (
        "💬 <b>Natural Text Examples</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Type any of the following directly into chat:\n\n"
        "• <code>120 dosa</code> (logs ₹120 to Dosa)\n"
        "• <code>+500 from Amit</code> (logs ₹500 received)\n"
        "• <code>-45 tea</code> (logs ₹45 sent)\n"
        "• <code>Paid 1500 to Electricity Bill yesterday</code>\n"
        "• <code>Spent 350 on Uber</code>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    await query.edit_message_text(text, reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')


async def nav_quickadd(query, context, parts, ws_id, ws_ctx, update=None):
    top_p = get_top_payees(limit=3, workspace_id=ws_id)
    text = (
        "⚡ <b>One-Tap Quick Entry</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Tap a shortcut button below or type a short text message directly:\n\n"
        "• <code>120 dosa</code> (logs ₹120 to Dosa)\n"
        "• <code>+500 salary</code> (logs ₹500 income)\n"
        "• <code>-45 tea</code> (logs ₹45 expense)\n"
        "• <code>Paid 200 to Ramesh</code>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    await query.edit_message_text(text, reply_markup=get_quick_add_keyboard(top_payees=top_p), parse_mode='HTML')


async def nav_backup_status(query, context, parts, ws_id, ws_ctx, update=None):
    text = render_backup_status_text()
    await query.edit_message_text(text, reply_markup=get_backup_status_keyboard(), parse_mode='HTML')


async def nav_restore_info(query, context, parts, ws_id, ws_ctx, update=None):
    text = (
        "📥 <b>Database Restore & Recovery</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "To restore from a backup file:\n\n"
        "1. Send or forward your <code>payment_tracker_backup.json</code> file directly to this chat.\n"
        "2. The bot will automatically validate the SHA-256 checksum and preview incoming changes.\n"
        "3. Confirm the import to restore the ledger.\n\n"
        "Or use command:\n"
        "<code>/restore</code> (downloads latest cloud backup)\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    await query.edit_message_text(text, reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')


async def nav_help_info(query, context, parts, ws_id, ws_ctx, update=None):
    text = (
        "ℹ️ <b>Payment Tracker Commands</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• /start — Interactive App Menu\n"
        "• /balance — Live Balance & Today Summary\n"
        "• /today — Today's Transaction Breakdown\n"
        "• /history — Interactive Paginated Ledger\n"
        "• /stats — Monthly Spending Analytics\n"
        "• /budget — Set & View Monthly Budget\n"
        "• /cafeteria — Cafeteria Menu & Order Tagging\n"
        "• /dashboard — Secure Web Dashboard Link\n"
        "• /setbalance — Set Starting Balance\n"
        "• /undo — Revert Last Transaction Action\n"
        "• /export — Download Statement (CSV/PDF/Excel)\n"
        "• /help — Full Reference Guide\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    await query.edit_message_text(text, reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')


async def nav_settings(query, context, parts, ws_id, ws_ctx, update=None):
    text = (
        "⚙️ <b>More Features & Tools</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• <b>Currency:</b> INR (₹, Indian number formatting)\n"
        "• <b>Timezone:</b> Asia/Kolkata (IST)\n"
        "• <b>Daily Closing Digest:</b> 10:00 PM IST\n"
        "• <b>Cloud Backup:</b> Dual Telegram & Drive\n"
        "• <b>Privacy:</b> Images deleted upon scanning\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    await query.edit_message_text(text, reply_markup=get_more_menu_keyboard(), parse_mode='HTML')


async def nav_contacts(query, context, parts, ws_id, ws_ctx, update=None):
    text = render_contacts_ledger_text(workspace_id=ws_id)
    await query.edit_message_text(text, reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')


async def nav_dash_info(query, context, parts, ws_id, ws_ctx, update=None):
    from config import RENDER_EXTERNAL_URL, TELEGRAM_USER_ID
    from services.dashboard_auth import create_one_time_code
    from bot.auth import is_super_admin
    base_url = RENDER_EXTERNAL_URL
    user_id = query.from_user.id if query.from_user else None
    user_is_owner = bool(ws_ctx and ws_ctx.role == 'owner') or is_super_admin(user_id) or (user_id is not None and str(user_id) == str(TELEGRAM_USER_ID))
    if user_is_owner:
        code = create_one_time_code(
            user_id=user_id,
            workspace_id=ws_id,
            role=(ws_ctx.role if ws_ctx else 'owner')
        )
        dash_link = f"{base_url}/auth?code={code}"
        note = "🔒 <i>Single-use login link generated (valid 60 seconds).</i>\n\n"
    else:
        dash_link = f"{base_url}/dashboard"
        note = "🔒 <i>Requires owner login. Run /dashboard in private chat to log in.</i>\n\n"
    text = (
        "🌐 <b>Web Analytics Dashboard</b>\n"
        "━━━━━━━━━━━━━━\n"
        "Access real-time visual charts, month-over-month comparisons, category donut charts, daily spend bars, and CSV export:\n\n"
        f"{note}"
        f"🔗 <a href='{dash_link}'>Open Live Dashboard</a>\n"
        "━━━━━━━━━━━━━━"
    )
    await query.edit_message_text(text, reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML', disable_web_page_preview=True)


async def nav_digest_info(query, context, parts, ws_id, ws_ctx, update=None):
    text = (
        "📊 <b>Daily Closing Digest</b>\n"
        "━━━━━━━━━━━━━━\n"
        "The bot automatically sends a closing financial summary every night at <b>10:00 PM IST</b>.\n\n"
        "To generate a digest on demand, use:\n"
        "<code>/digest</code> (today) or <code>/digest YYYY-MM-DD</code>\n"
        "━━━━━━━━━━━━━━"
    )
    await query.edit_message_text(text, reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')


async def nav_recurring(query, context, parts, ws_id, ws_ctx, update=None):
    from services.recurring_service import get_upcoming_recurring
    upcoming = await asyncio.to_thread(get_upcoming_recurring, 30, workspace_id=ws_id)
    text = render_recurring_overview_text(workspace_id=ws_id)
    caller_role = ws_ctx.role if ws_ctx else 'viewer'
    await query.edit_message_text(text, reply_markup=get_recurring_menu_keyboard(upcoming_items=upcoming, role=caller_role), parse_mode='HTML')


async def nav_rec_all(query, context, parts, ws_id, ws_ctx, update=None):
    text = render_all_recurring_text(workspace_id=ws_id)
    await query.edit_message_text(text, reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')


async def nav_rec_add(query, context, parts, ws_id, ws_ctx, update=None):
    caller_role = ws_ctx.role if ws_ctx else 'viewer'
    if caller_role not in ('admin', 'owner'):
        await query.answer("⛔ Access Restricted: Admin role required to add recurring payments.", show_alert=True)
        return
    context.user_data['action'] = 'waiting_rec_add'
    text = (
        "➕ <b>Add Recurring Payment</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Please reply with the details in the format:\n"
        "<code>Payee, Amount, Frequency</code>\n\n"
        "Examples:\n"
        "• <code>Netflix, 649, Monthly</code>\n"
        "• <code>House Rent, 15000, Monthly</code>\n"
        "• <code>SIP Mutual Fund, 5000, Monthly</code>\n"
        "• <code>Gym Membership, 12000, Yearly</code>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    await query.edit_message_text(text, reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')


async def nav_month_close(query, context, parts, ws_id, ws_ctx, update=None):
    from services.monthly_review_service import get_monthly_review
    now_dt = get_current_time_in_tz()
    y = max(2000, min(2100, int(parts[2]))) if len(parts) > 2 and parts[2].isdigit() else now_dt.year
    m = max(1, min(12, int(parts[3]))) if len(parts) > 3 and parts[3].isdigit() else now_dt.month
    rev = await asyncio.to_thread(get_monthly_review, y, m, workspace_id=ws_id)
    text = await asyncio.to_thread(render_monthly_closing_summary_text, y, m, workspace_id=ws_id)
    caller_role = ws_ctx.role if ws_ctx else 'viewer'
    await query.edit_message_text(text, reply_markup=get_monthly_closing_keyboard(y, m, is_closed=bool(rev), role=caller_role), parse_mode='HTML')


async def nav_stats(query, context, parts, ws_id, ws_ctx, update=None):
    now_dt = get_current_time_in_tz()
    stats = get_monthly_summary(now_dt.year, now_dt.month, workspace_id=ws_id)
    month_name = date(now_dt.year, now_dt.month, 1).strftime("%B %Y")
    top_p_text = "N/A"
    if stats['top_recipient']:
        top_p_text = f"{stats['top_recipient']['person_name']} ({format_currency(stats['top_recipient']['total'])})"
    text = (
        f"📊 <b>Monthly Analytics - {html.escape(month_name)}</b>\n\n"
        f"🔴 Total Sent: <b>{html.escape(format_currency(stats['total_sent']))}</b>\n"
        f"🟢 Total Received: <b>{html.escape(format_currency(stats['total_received']))}</b>\n"
        f"📈 Net Flow: <b>{html.escape(format_currency(stats['net_savings']))}</b>\n\n"
        f"🔢 Total Transactions: <b>{stats['tx_count']}</b>\n"
        f"🏆 Top Recipient: {html.escape(top_p_text)}"
    )
    await query.edit_message_text(text, reply_markup=get_stats_keyboard(), parse_mode='HTML')


async def nav_budget(query, context, parts, ws_id, ws_ctx, update=None):
    from services.budget_service import format_budget_status
    now_dt = get_current_time_in_tz()
    text = format_budget_status(now_dt.year, now_dt.month, workspace_id=ws_id)
    await query.edit_message_text(text, reply_markup=get_budget_keyboard(), parse_mode='HTML')


async def nav_insights(query, context, parts, ws_id, ws_ctx, update=None):
    from services.category_service import format_spending_insights
    now_dt = get_current_time_in_tz()
    text = format_spending_insights(now_dt.year, now_dt.month, workspace_id=ws_id)
    await query.edit_message_text(text, reply_markup=get_insights_keyboard(), parse_mode='HTML')


async def nav_digest(query, context, parts, ws_id, ws_ctx, update=None):
    from services.scheduler_service import format_daily_digest
    text = format_daily_digest(None, workspace_id=ws_id)
    await query.edit_message_text(text, reply_markup=get_digest_keyboard(), parse_mode='HTML')


async def nav_cafe(query, context, parts, ws_id, ws_ctx, update=None):
    from services.cafeteria_service import format_cafeteria_stats
    text = format_cafeteria_stats(workspace_id=ws_id)
    await query.edit_message_text(text, reply_markup=get_cafestats_keyboard(), parse_mode='HTML')


async def nav_gemini(query, context, parts, ws_id, ws_ctx, update=None):
    caller_role = ws_ctx.role if ws_ctx else 'viewer'
    card, keyboard = await render_gemini_status_payload(force_refresh=False, role=caller_role)
    await query.edit_message_text(card, reply_markup=keyboard, parse_mode='HTML')


NAV_DISPATCH_TABLE = {
    "home": nav_home,
    "balance": nav_balance,
    "today": nav_today,
    "history": nav_history,
    "history_noop": nav_history_noop,
    "add": nav_add,
    "more": nav_more,
    "add_scan": nav_add_scan,
    "add_manual": nav_add_manual,
    "add_text": nav_add_text,
    "quickadd": nav_quickadd,
    "backup_status": nav_backup_status,
    "restore_info": nav_restore_info,
    "help_info": nav_help_info,
    "settings": nav_settings,
    "contacts": nav_contacts,
    "dash_info": nav_dash_info,
    "digest_info": nav_digest_info,
    "recurring": nav_recurring,
    "rec_all": nav_rec_all,
    "rec_add": nav_rec_add,
    "month_close": nav_month_close,
    "stats": nav_stats,
    "budget": nav_budget,
    "insights": nav_insights,
    "digest": nav_digest,
    "cafe": nav_cafe,
    "gemini": nav_gemini,
    "gemini_status": nav_gemini,
}


async def handle_nav_callback(query, context, parts, ws_id, ws_ctx, update=None) -> None:
    """Dispatches interactive navigation callback queries."""
    nav_target = parts[1] if len(parts) > 1 else "home"
    try:
        handler = NAV_DISPATCH_TABLE.get(nav_target)
        if handler:
            await handler(query, context, parts, ws_id, ws_ctx, update)
    except Exception as nav_err:
        if "Message is not modified" not in str(nav_err):
            logger.warning(f"Nav error ({nav_target}): {nav_err}")
