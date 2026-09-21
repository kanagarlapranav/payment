from telegram import Update
from telegram.ext import ContextTypes
import os
import uuid
import json
import asyncio
import html
import re
from datetime import datetime, timedelta, date
from config import TELEGRAM_USER_ID, IMAGE_DIR, logger
from bot.auth import (
    require_authorized, require_admin, is_owner, is_authorized_user,
    get_callback_policy, ADMIN_CALLBACK_ACTIONS, READ_ONLY_CALLBACK_ACTIONS
)
from bot.commands import (
    is_authorized, is_admin_user, render_home_menu_text, render_history_page,
    render_contacts_ledger_text, render_transaction_detail, render_backup_status_text
)
from bot.keyboards import (
    get_confirmation_keyboard, get_edit_fields_keyboard, get_delete_confirm_keyboard,
    get_filter_keyboard, get_sort_keyboard, get_home_menu_keyboard, get_back_to_menu_keyboard,
    get_confirmation_card_keyboard, get_edit_pending_fields_keyboard, get_category_picker_keyboard,
    get_quick_undo_keyboard, get_quick_add_keyboard, get_history_paginated_keyboard,
    get_add_menu_keyboard, get_more_menu_keyboard, get_transaction_detail_keyboard,
    get_backup_status_keyboard
)
from ocr.extractor import perform_ocr
from ocr.gemini_vision import is_gemini_available, extract_transaction_with_gemini
from services.gdrive_service import is_gdrive_available, upload_receipt_to_drive, upload_backup_to_drive
from services.transaction_service import process_transaction, commit_transaction
from services.balance_service import recalculate_all_balances, get_today_summary, get_overall_summary
from services.backup_service import backup_to_telegram
from database.queries import (
    get_transaction_by_id, get_transaction_by_reference, update_transaction, delete_transaction,
    search_transactions, get_monthly_summary, get_recent_transactions, get_balance_setting,
    get_payee_category, remember_payee_category, find_potential_duplicate, get_top_payees,
    get_daily_spend_series, get_month_comparison_stats, get_transactions_paginated, get_contact_ledger,
    get_category_summary
)
from utils.currency import parse_amount, format_currency
from utils.dates import parse_date, get_current_time_in_tz, format_display_date

# In-memory store for pending transactions awaiting confirmation
pending_transactions = {}

def format_receipt_card(transaction, dup_warning: str = None) -> str:
    """Formats the polished receipt confirmation card requested by user."""
    amt_str = format_currency(transaction.amount)
    arrow = "←" if transaction.transaction_type == "RECEIVED" else "→"
    person = transaction.person_name or "Unknown"
    cat = transaction.category or "General"
    
    if hasattr(transaction.transaction_date, 'strftime'):
        d_str = transaction.transaction_date.strftime("%d %b")
    else:
        d_str = str(transaction.transaction_date or "Today")
    if transaction.transaction_time:
        d_str += f", {transaction.transaction_time}"

    bank_name = transaction.bank_name or transaction.payment_app or "UPI"
    ref_suffix = f" · Ref …{str(transaction.reference_number)[-4:]}" if transaction.reference_number and len(str(transaction.reference_number)) >= 4 else ""
    
    lines = [
        "🧾 <b>Payment detected</b>",
        "━━━━━━━━━━━━━━",
        f"💸 <b>{amt_str}</b> {arrow} <b>{html.escape(person)}</b>",
        f"🏷 {html.escape(cat)}   📅 {html.escape(d_str)}",
        f"🏦 {html.escape(bank_name)}{html.escape(ref_suffix)}"
    ]
    if dup_warning:
        lines.append(f"\n{dup_warning}")
    return "\n".join(lines)

def parse_short_entry(text: str):
    """Parses short quick entries like '120 dosa', '+500 salary', '-25 snacks', 'coffee 15'."""
    from utils.validation import parse_decimal_amount
    text = text.strip()
    # Patterns like "+500 salary", "500 salary", "120 dosa", "-20 tea"
    m = re.match(r'^([+-]?)\s*(?:₹|rs\.?)?\s*(\d+(?:\.\d+)?)\s+(?:for\s+|to\s+|from\s+)?(.+)$', text, re.IGNORECASE)
    if not m:
        # Also try reverse: "dosa 120", "coffee 15"
        m_rev = re.match(r'^(.+?)\s+([+-]?)\s*(?:₹|rs\.?)?\s*(\d+(?:\.\d+)?)$', text, re.IGNORECASE)
        if m_rev:
            name = m_rev.group(1).strip()
            sign = m_rev.group(2)
            try:
                amt = float(parse_decimal_amount(m_rev.group(3), allow_zero=False))
            except ValueError:
                return None
            tx_type = "RECEIVED" if sign == '+' else "SENT"
            return amt, tx_type, name
        return None
    sign = m.group(1)
    try:
        amt = float(parse_decimal_amount(m.group(2), allow_zero=False))
    except ValueError:
        return None
    name = m.group(3).strip()
    tx_type = "RECEIVED" if sign == '+' else "SENT"
    return amt, tx_type, name

async def deliver_response(status_msg, message, text: str, reply_markup=None, parse_mode='HTML'):
    """Safely updates status_msg or sends a new reply if edit_text fails, ensuring no stuck status messages."""
    try:
        await status_msg.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        return
    except Exception as err:
        logger.warning(f"Could not edit status message with parse_mode={parse_mode}: {err}. Retrying with clean text...")
        clean_text = re.sub(r'<[^>]+>', '', text)
        try:
            await status_msg.edit_text(clean_text, reply_markup=reply_markup)
            return
        except Exception as edit_err:
            logger.warning(f"Could not edit status message without formatting ({edit_err}); deleting status and sending reply.")
            try:
                await status_msg.delete()
            except Exception:
                pass
            try:
                await message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            except Exception:
                try:
                    await message.reply_text(clean_text, reply_markup=reply_markup)
                except Exception as final_err:
                    logger.error(f"Failed to deliver message: {final_err}")

ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
MAX_IMAGE_FILE_SIZE = 20 * 1024 * 1024  # 20 MB

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles incoming images (screenshots) with RapidOCR + Gemini Vision AI fallback & cross-verification."""
    if not await require_admin(update): return
    
    message = update.message
    chat_id = str(message.chat_id)
    message_id = str(message.message_id)
    caption = message.caption or ""
    
    # 1. Image validation & extension check
    file_id = None
    ext = ".jpg"
    
    if message.photo:
        photo_obj = message.photo[-1]
        if photo_obj.file_size and photo_obj.file_size > MAX_IMAGE_FILE_SIZE:
            await message.reply_text("⚠️ Image file is too large (exceeds 20MB limit).")
            return
        file_id = photo_obj.file_id
        ext = ".jpg"
    elif message.document:
        doc = message.document
        if doc.file_size and doc.file_size > MAX_IMAGE_FILE_SIZE:
            await message.reply_text("⚠️ Image file is too large (exceeds 20MB limit).")
            return
        doc_mime = (doc.mime_type or "").lower()
        orig_ext = os.path.splitext(doc.file_name or "")[1].lower()
        if orig_ext in ALLOWED_IMAGE_EXTENSIONS:
            ext = orig_ext
        elif doc_mime in {'image/jpeg', 'image/jpg'}:
            ext = '.jpg'
        elif doc_mime == 'image/png':
            ext = '.png'
        elif doc_mime == 'image/webp':
            ext = '.webp'
        else:
            await message.reply_text("⚠️ Unsupported image format. Allowed formats: JPG, PNG, WEBP.")
            return
        file_id = doc.file_id
    else:
        return # Ignore non-images
        
    status_msg = await message.reply_text("🔍 Reading receipt…")
    image_path = None
    
    try:
        # Safe UUID-based filename (never use Telegram filename as local path)
        filename = f"rcpt_{uuid.uuid4().hex}{ext}"
        image_path = IMAGE_DIR / filename
        
        # Download image with resilient timeout
        for attempt in range(2):
            try:
                file = await context.bot.get_file(file_id, read_timeout=30.0, connect_timeout=15.0)
                await file.download_to_drive(custom_path=image_path, read_timeout=30.0, connect_timeout=15.0)
                break
            except Exception as dl_err:
                if attempt == 1:
                    raise dl_err
                await asyncio.sleep(1)
        
        # Send typing action
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass

        # Run OCR first with thread executor & timeout for deterministic cross-verification
        ocr_text = await asyncio.to_thread(perform_ocr, str(image_path))

        transaction = None
        confidence = 0

        # Tier 1: Try Google Gemini Vision AI if API key is configured
        if is_gemini_available():
            try:
                logger.info("Attempting Gemini Vision extraction with OCR cross-verification...")
                g_tx, g_conf = await asyncio.to_thread(
                    extract_transaction_with_gemini, str(image_path), caption, ocr_text
                )
                if g_tx and g_conf >= 40:
                    transaction = g_tx
                    confidence = g_conf
                    transaction.telegram_message_id = message_id
                    transaction.telegram_chat_id = chat_id
                    transaction.original_image_path = str(image_path)
            except Exception as gem_err:
                logger.warning(f"Gemini Vision error, falling back to local OCR parser: {gem_err}")

        # Tier 2: Fallback to local RapidOCR + Regex Heuristic Parser
        if not transaction or not transaction.amount:
            if not ocr_text or not ocr_text.strip():
                await deliver_response(
                    status_msg, message,
                    "❌ Could not extract any readable text from the image. Please upload a clearer screenshot."
                )
                return

            transaction, confidence = await asyncio.to_thread(
                process_transaction, ocr_text, str(image_path), message_id, chat_id, caption
            )

        # Basic validation
        if not transaction or not transaction.amount or transaction.amount <= 0:
            await deliver_response(
                status_msg, message,
                "⚠️ Could not detect a valid amount from the receipt.\n\n💡 <b>Tip:</b> You can log it instantly by typing:\n<code>120 dosa</code> or <code>Paid 500 to Ramesh</code>",
                parse_mode='HTML'
            )
            return
            
        if not transaction.transaction_type or transaction.transaction_type == "UNKNOWN":
            await deliver_response(
                status_msg, message,
                f"⚠️ Transaction type (SENT/RECEIVED) could not be determined reliably.\nAmount found: {format_currency(transaction.amount)}"
            )
            return

        # 1. Payee Category Memory: check if payee category is remembered from past
        rem_cat = await asyncio.to_thread(get_payee_category, transaction.person_name)
        if rem_cat:
            transaction.category = rem_cat
        elif not transaction.category or transaction.category == 'General':
            from services.cafeteria_service import is_cafeteria_payment
            if is_cafeteria_payment(transaction.person_name, transaction.upi_id, transaction.ocr_text):
                transaction.category = "Food & Dining"

        # 2. Duplicate Check: compare reference number or amount/person/date against existing rows
        dup = await asyncio.to_thread(
            find_potential_duplicate,
            amount=transaction.amount,
            reference_number=transaction.reference_number,
            person_name=transaction.person_name,
            tx_date=str(transaction.transaction_date) if transaction.transaction_date else None
        )
        dup_warning = f"⚠️ Similar to #{dup['id']} ({dup.get('match_reason', 'duplicate')}) — duplicate?" if dup else None

        # 3. Store in pending for interactive confirmation
        pending_id = uuid.uuid4().hex[:10]
        pending_transactions[pending_id] = transaction

        # 4. Render the polished Confirmation Card requested by user
        card_text = format_receipt_card(transaction, dup_warning)
        card_markup = get_confirmation_card_keyboard(pending_id, duplicate_warning=bool(dup))

        await deliver_response(status_msg, message, card_text, reply_markup=card_markup, parse_mode='HTML')

    except Exception as e:
        logger.error(f"Error handling image: {e}", exc_info=True)
        await deliver_response(status_msg, message, "❌ Error processing image. Please try again or type the expense manually.")
    finally:
        # Guarantee deletion of temporary file in finally block
        if image_path and os.path.exists(image_path):
            try:
                os.remove(image_path)
                logger.info(f"Cleaned up temporary image in finally: {image_path}")
            except OSError as cleanup_err:
                logger.warning(f"Could not delete temp image {image_path}: {cleanup_err}")


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles button presses from inline keyboards with central authorization policy."""
    query = update.callback_query
    if not query or not query.data:
        return

    data = query.data
    parts = data.split(":") if ":" in data else [data]
    action = parts[0]
    
    # Check policy before any database access or data exposure
    policy = get_callback_policy(action)
    if policy is None:
        # Unknown or stale callback data gets a friendly refusal, not a crash
        try:
            await query.answer("ℹ️ This button or menu is no longer active.", show_alert=True)
        except Exception:
            pass
        return

    if policy == 'admin':
        if not await require_admin(update):
            return
    elif policy == 'read_only':
        if not await require_authorized(update):
            return

    try:
        await query.answer()
    except Exception:
        pass
    
    data = query.data
    parts = data.split(":") if ":" in data else [data]
    action = parts[0]
    
    # --- 0. Interactive Home Menu Navigation ---
    if action == "nav":
        nav_target = parts[1] if len(parts) > 1 else "home"
        try:
            if nav_target == "home":
                text = render_home_menu_text()
                await query.edit_message_text(text, reply_markup=get_home_menu_keyboard(), parse_mode='HTML')
            elif nav_target == "balance":
                balance = get_balance_setting()
                today_stats = get_today_summary()
                overall = get_overall_summary()
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
                from telegram import InlineKeyboardButton
                extra_btn = [InlineKeyboardButton("➕ Quick Add", callback_data="nav:quickadd")]
                await query.edit_message_text(text, reply_markup=get_back_to_menu_keyboard(extra_btn), parse_mode='HTML')
            elif nav_target == "today":
                from database.queries import get_transactions_by_date
                today_stats = get_today_summary()
                today_date = get_current_time_in_tz().date()
                txs = get_transactions_by_date(today_date)
                lines = [
                    "📅 <b>Today's Transactions</b>",
                    "━━━━━━━━━━━━━━"
                ]
                if not txs:
                    lines.append("<i>No transactions logged today yet.</i>\n\n💡 Tip: Send a receipt screenshot or type <code>120 dosa</code>.")
                else:
                    for t in txs:
                        badge = "🟢" if t['transaction_type'] == 'RECEIVED' else "🔴"
                        arrow = "+" if t['transaction_type'] == 'RECEIVED' else "-"
                        lines.append(
                            f"<b>#{t['id']}</b> {badge} <b>{arrow}{format_currency(t['amount'])}</b> — {html.escape(t.get('person_name') or 'Unknown')}\n"
                            f"   🏷 {html.escape(t.get('category') or 'General')} | 💼 Bal: <code>{format_currency(t.get('balance_after', 0))}</code>\n"
                        )
                lines.append("━━━━━━━━━━━━━━")
                lines.append(f"🔴 Spent: {format_currency(today_stats.total_sent)} | 🟢 Recv: {format_currency(today_stats.total_received)}")
                from telegram import InlineKeyboardButton
                extra_btn = [InlineKeyboardButton("➕ Quick Add", callback_data="nav:quickadd")]
                await query.edit_message_text("\n".join(lines), reply_markup=get_back_to_menu_keyboard(extra_btn), parse_mode='HTML')
            elif nav_target == "history":
                page = int(parts[2]) if len(parts) > 2 else 1
                ft = parts[3] if len(parts) > 3 else "ALL"
                text, markup = render_history_page(page=page, filter_type=ft, page_size=5)
                await query.edit_message_text(text, reply_markup=markup, parse_mode='HTML')
            elif nav_target == "history_noop":
                pass
            elif nav_target == "add":
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
            elif nav_target == "more":
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
            elif nav_target == "add_scan":
                text = (
                    "📸 <b>Scan Payment Receipt</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "Upload a receipt screenshot or photo directly into this chat.\n\n"
                    "Supported apps: Google Pay, PhonePe, Paytm, CRED, BHIM, Amazon Pay, Super.money, and bank alerts.\n"
                    "━━━━━━━━━━━━━━━━━━━━"
                )
                await query.edit_message_text(text, reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')
            elif nav_target == "add_manual":
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
            elif nav_target == "add_text":
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
            elif nav_target == "quickadd":
                top_p = get_top_payees(limit=3)
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
            elif nav_target == "backup_status":
                text = render_backup_status_text()
                await query.edit_message_text(text, reply_markup=get_backup_status_keyboard(), parse_mode='HTML')
            elif nav_target == "restore_info":
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
            elif nav_target == "help_info":
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
            elif nav_target == "settings":
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
            elif nav_target == "contacts":
                text = render_contacts_ledger_text()
                await query.edit_message_text(text, reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')
            elif nav_target == "dash_info":
                from config import DASHBOARD_TOKEN
                base_url = "https://payment-tracker-3r8w.onrender.com"
                token_str = f"?token={DASHBOARD_TOKEN}" if DASHBOARD_TOKEN else ""
                dash_link = f"{base_url}/dashboard{token_str}"
                text = (
                    "🌐 <b>Web Analytics Dashboard</b>\n"
                    "━━━━━━━━━━━━━━\n"
                    "Access real-time visual charts, month-over-month comparisons, category donut charts, daily spend bars, and CSV export:\n\n"
                    f"🔗 <a href='{dash_link}'>Open Live Dashboard</a>\n"
                    "━━━━━━━━━━━━━━"
                )
                await query.edit_message_text(text, reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML', disable_web_page_preview=True)
            elif nav_target == "digest_info":
                text = (
                    "📊 <b>Daily Closing Digest</b>\n"
                    "━━━━━━━━━━━━━━\n"
                    "The bot automatically sends a closing financial summary every night at <b>10:00 PM IST</b>.\n\n"
                    "To generate a digest on demand, use:\n"
                    "<code>/digest</code> (today) or <code>/digest YYYY-MM-DD</code>\n"
                    "━━━━━━━━━━━━━━"
                )
                await query.edit_message_text(text, reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')
            elif nav_target == "recurring":
                from services.recurring_service import get_upcoming_recurring
                from bot.commands import render_recurring_overview_text
                from bot.keyboards import get_recurring_menu_keyboard
                upcoming = await asyncio.to_thread(get_upcoming_recurring, 30)
                text = render_recurring_overview_text()
                await query.edit_message_text(text, reply_markup=get_recurring_menu_keyboard(upcoming_items=upcoming), parse_mode='HTML')
            elif nav_target == "rec_all":
                from bot.commands import render_all_recurring_text
                text = render_all_recurring_text()
                await query.edit_message_text(text, reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')
            elif nav_target == "rec_add":
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
            elif nav_target == "month_close":
                now_dt = get_current_time_in_tz()
                y = int(parts[2]) if len(parts) > 2 else now_dt.year
                m = int(parts[3]) if len(parts) > 3 else now_dt.month
                from bot.commands import render_monthly_closing_summary_text
                from bot.keyboards import get_monthly_closing_keyboard
                from services.monthly_review_service import get_monthly_review
                rev = await asyncio.to_thread(get_monthly_review, y, m)
                text = await asyncio.to_thread(render_monthly_closing_summary_text, y, m)
                await query.edit_message_text(text, reply_markup=get_monthly_closing_keyboard(y, m, is_closed=bool(rev)), parse_mode='HTML')
        except Exception as nav_err:
            if "Message is not modified" not in str(nav_err):
                logger.warning(f"Nav error ({nav_target}): {nav_err}")
        return

    # --- 1. Transaction Detail, Duplicate & Backup Actions ---
    elif action == "close_month":
        y = int(parts[1])
        m = int(parts[2])
        from services.monthly_review_service import close_and_record_monthly_review
        from bot.commands import render_monthly_closing_summary_text
        from bot.keyboards import get_monthly_closing_keyboard
        await asyncio.to_thread(close_and_record_monthly_review, y, m)
        text = await asyncio.to_thread(render_monthly_closing_summary_text, y, m)
        await query.edit_message_text(text, reply_markup=get_monthly_closing_keyboard(y, m, is_closed=True), parse_mode='HTML')
        return

    elif action == "rec_paid":
        rec_id = int(parts[1])
        from services.recurring_service import mark_recurring_paid, get_recurring_by_id
        from bot.keyboards import get_quick_undo_keyboard
        tx_id, next_due = await asyncio.to_thread(mark_recurring_paid, rec_id)
        rec = await asyncio.to_thread(get_recurring_by_id, rec_id)
        text = (
            f"✅ <b>Recurring Payment Logged to Ledger!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Payee:</b> {html.escape(rec['payee_name'])}\n"
            f"• <b>Amount:</b> {format_currency(rec['amount'])}\n"
            f"• <b>Transaction Created:</b> #{tx_id}\n"
            f"• <b>New Next Due Date:</b> <code>{next_due}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        await query.edit_message_text(text, reply_markup=get_quick_undo_keyboard(tx_id), parse_mode='HTML')
        return

    elif action == "rec_skip":
        rec_id = int(parts[1])
        from services.recurring_service import skip_recurring_due, get_recurring_by_id
        next_due = await asyncio.to_thread(skip_recurring_due, rec_id)
        rec = await asyncio.to_thread(get_recurring_by_id, rec_id)
        text = (
            f"⏭️ <b>Recurring Cycle Skipped</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Payee:</b> {html.escape(rec['payee_name'])}\n"
            f"• <b>New Next Due Date:</b> <code>{next_due}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        await query.edit_message_text(text, reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')
        return

    elif action == "rec_pause":
        rec_id = int(parts[1])
        from services.recurring_service import update_recurring_status
        await asyncio.to_thread(update_recurring_status, rec_id, 'PAUSED')
        await query.edit_message_text("⏸️ <b>Recurring payment paused.</b>", reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')
        return

    elif action == "rec_resume":
        rec_id = int(parts[1])
        from services.recurring_service import update_recurring_status
        await asyncio.to_thread(update_recurring_status, rec_id, 'ACTIVE')
        await query.edit_message_text("▶️ <b>Recurring payment resumed.</b>", reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')
        return

    elif action == "rec_del":
        rec_id = int(parts[1])
        from services.recurring_service import delete_recurring_payment
        await asyncio.to_thread(delete_recurring_payment, rec_id)
        await query.edit_message_text("🗑️ <b>Recurring payment deleted.</b>", reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')
        return

    elif action == "restore_confirm":
        from services.backup_service import import_database_from_json, backup_to_telegram
        from database.queries import get_all_transactions, get_balance_setting
        from utils.formatting import format_currency
        await query.edit_message_text("⏳ <b>Restoring ledger from backup...</b>", parse_mode='HTML')
        result = await asyncio.to_thread(import_database_from_json)
        if not result.get('success'):
            await query.edit_message_text(f"❌ <b>Restore Failed:</b> {html.escape(str(result.get('error')))}", parse_mode='HTML')
            return
        await backup_to_telegram(context.bot)
        txs = await asyncio.to_thread(get_all_transactions)
        cur_b = format_currency(get_balance_setting())
        await query.edit_message_text(
            f"✅ <b>Database Restored Successfully!</b>\n\n"
            f"• <b>{len(txs)}</b> live transactions available.\n"
            f"• <b>Current Balance:</b> {cur_b}\n\n"
            f"Use <code>/balance</code> or <code>/history</code> to view your ledger.",
            reply_markup=get_back_to_menu_keyboard(),
            parse_mode='HTML'
        )
        return

    elif action == "restore_cancel":
        await query.edit_message_text("❌ <b>Restore Cancelled.</b>", reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')
        return

    # --- 1. Transaction Detail, Duplicate & Backup Actions ---
    elif action == "tx_view":
        tx_id = int(parts[1])
        text, markup = render_transaction_detail(tx_id)
        await query.edit_message_text(text, reply_markup=markup, parse_mode='HTML')
        return

    elif action == "dup_tx":
        tx_id = int(parts[1])
        orig_tx = get_transaction_by_id(tx_id)
        if not orig_tx:
            await query.edit_message_text("❌ Transaction not found.", reply_markup=get_back_to_menu_keyboard())
            return
        
        from database.models import Transaction
        dup_pending_id = uuid.uuid4().hex[:10]
        dup_tx = Transaction(
            amount=orig_tx['amount'],
            transaction_type=orig_tx['transaction_type'],
            person_name=orig_tx.get('person_name'),
            category=orig_tx.get('category') or 'General',
            payment_app=orig_tx.get('payment_app'),
            bank_name=orig_tx.get('bank_name'),
            confidence=95
        )
        pending_transactions[dup_pending_id] = dup_tx
        card_text = format_receipt_card(dup_tx, dup_warning=f"📋 Duplicating Transaction #{tx_id}")
        await query.edit_message_text(card_text, reply_markup=get_confirmation_card_keyboard(dup_pending_id), parse_mode='HTML')
        return

    elif action == "edit_tx":
        tx_id = int(parts[1])
        await query.edit_message_text(
            f"✏️ <b>Edit Transaction #{tx_id}:</b>\nChoose which field you want to modify:",
            reply_markup=get_edit_fields_keyboard(tx_id),
            parse_mode='HTML'
        )
        return

    elif action == "delete_tx":
        tx_id = int(parts[1])
        await query.edit_message_text(
            f"🗑️ <b>Delete Transaction #{tx_id}?</b>\nAre you sure you want to delete this transaction?",
            reply_markup=get_delete_confirm_keyboard(tx_id),
            parse_mode='HTML'
        )
        return

    elif action == "backup_now":
        await query.edit_message_text("⏳ <i>Exporting database and uploading cloud backup...</i>", parse_mode='HTML')
        from services.backup_service import export_database_to_json, backup_to_telegram
        export_database_to_json()
        success = await backup_to_telegram(context.bot, force=True)
        if success:
            text = "✅ <b>Cloud Backup Completed Successfully!</b>\n\n" + render_backup_status_text()
            await query.edit_message_text(text, reply_markup=get_backup_status_keyboard(), parse_mode='HTML')
        else:
            await query.edit_message_text("⚠️ Cloud backup failed or no active records to backup.", reply_markup=get_back_to_menu_keyboard())
        return

    elif action == "qa_payee":
        payee = parts[1]
        tt = parts[2] if len(parts) > 2 else "SENT"
        context.user_data['action'] = 'waiting_payee_amount'
        context.user_data['quick_payee'] = payee
        context.user_data['quick_type'] = tt
        arrow = "to" if tt == "SENT" else "from"
        await query.edit_message_text(
            f"💵 <b>Quick Add:</b> Enter amount {arrow} <b>{html.escape(payee)}</b> (e.g. <code>250</code>):",
            reply_markup=get_back_to_menu_keyboard(),
            parse_mode='HTML'
        )
        return

    # --- 1. Redesigned Receipt Card Actions ---
    elif action == "save_p":
        pending_id = parts[1]
        transaction = pending_transactions.pop(pending_id, None)
        if not transaction:
            await query.edit_message_text("❌ Receipt confirmation expired.", reply_markup=get_back_to_menu_keyboard())
            return
        
        # Remember payee preference in DB if available
        if transaction.person_name and transaction.category:
            remember_payee_category(transaction.person_name, transaction.category)
            
        success = commit_transaction(transaction)
        if success:
            # Check budget alerts (Requirement 5: After saving a transaction, add a one-line alert if a budget crosses 80% or 100%)
            from services.budget_service import get_budget_info
            now = get_current_time_in_tz()
            b_info = get_budget_info(now.year, now.month)
            budget_alert = ""
            if b_info.get('budget', 0) > 0:
                pct = b_info.get('percentage', 0)
                spent = b_info.get('spent', 0)
                limit = b_info.get('budget', 0)
                if pct >= 100:
                    budget_alert = f"\n\n🚨 <b>Alert:</b> Budget exceeded! <b>{pct:.0f}%</b> (₹{spent:,.0f} / ₹{limit:,.0f})"
                elif pct >= 80:
                    budget_alert = f"\n\n⚠️ <b>Alert:</b> Budget reached <b>{pct:.0f}%</b> (₹{spent:,.0f} / ₹{limit:,.0f})"

            date_display = transaction.transaction_date.strftime("%d %b") if hasattr(transaction.transaction_date, 'strftime') else (str(transaction.transaction_date) if transaction.transaction_date else "Today")
            if transaction.transaction_time:
                date_display += f", {transaction.transaction_time}"
            arrow = "←" if transaction.transaction_type == "RECEIVED" else "→"
            
            saved_text = (
                f"✅ <b>Payment Saved! #{transaction.id}</b>\n"
                "━━━━━━━━━━━━━━\n"
                f"💸 <b>{format_currency(transaction.amount)}</b> {arrow} <b>{html.escape(transaction.person_name or 'Unknown')}</b>\n"
                f"🏷 {html.escape(transaction.category or 'General')}   📅 {html.escape(date_display)}\n"
                f"💼 <b>Balance:</b> <b>{format_currency(transaction.balance_after)}</b>"
                f"{budget_alert}"
            )
            await query.edit_message_text(saved_text, reply_markup=get_quick_undo_keyboard(transaction.id), parse_mode='HTML')
            from services.task_manager import schedule_debounced_backup
            schedule_debounced_backup(context.bot)
        else:
            await query.edit_message_text("⚠️ Transaction could not be saved (possibly duplicate).", reply_markup=get_back_to_menu_keyboard())
        return

    elif action == "edit_p":
        pending_id = parts[1]
        await query.edit_message_text(
            "✏️ <b>Select Field to Edit:</b>\n"
            "Choose which field of the detected receipt you want to change:",
            reply_markup=get_edit_pending_fields_keyboard(pending_id),
            parse_mode='HTML'
        )
        return

    elif action == "ep_field":
        pending_id = parts[1]
        field = parts[2]
        context.user_data['action'] = 'waiting_edit_pending_value'
        context.user_data['pending_id'] = pending_id
        context.user_data['pending_field'] = field
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel Edit", callback_data=f"ep_back:{pending_id}")]])
        prompts = {
            'amount': "Enter the new amount (e.g. <code>150</code>):",
            'person': "Enter the payee / person name:",
            'date': "Enter the date (e.g. <code>yesterday</code> or <code>19/09/2026</code>):",
            'type': "Enter type (<code>SENT</code> or <code>RECEIVED</code>):"
        }
        await query.edit_message_text(f"✏️ {prompts.get(field, 'Enter new value:')}", reply_markup=cancel_markup, parse_mode='HTML')
        return

    elif action == "ep_back":
        pending_id = parts[1]
        transaction = pending_transactions.get(pending_id)
        if not transaction:
            await query.edit_message_text("❌ Transaction expired.", reply_markup=get_back_to_menu_keyboard())
            return
        dup = find_potential_duplicate(
            amount=transaction.amount,
            reference_number=transaction.reference_number,
            person_name=transaction.person_name,
            tx_date=str(transaction.transaction_date) if transaction.transaction_date else None
        )
        dup_warning = f"⚠️ Similar to #{dup['id']} ({dup.get('match_reason', 'duplicate')}) — duplicate?" if dup else None
        card_text = format_receipt_card(transaction, dup_warning)
        await query.edit_message_text(card_text, reply_markup=get_confirmation_card_keyboard(pending_id, duplicate_warning=bool(dup)), parse_mode='HTML')
        return

    elif action == "cat_p":
        pending_id = parts[1]
        await query.edit_message_text(
            "🏷️ <b>Select Category:</b>\n"
            "Tap a category below. Your preference will be remembered for this payee in future receipts:",
            reply_markup=get_category_picker_keyboard(pending_id),
            parse_mode='HTML'
        )
        return

    elif action == "set_pcat":
        pending_id = parts[1]
        new_cat = parts[2]
        transaction = pending_transactions.get(pending_id)
        if transaction:
            transaction.category = new_cat
            if transaction.person_name:
                remember_payee_category(transaction.person_name, new_cat)
            dup = find_potential_duplicate(
                amount=transaction.amount,
                reference_number=transaction.reference_number,
                person_name=transaction.person_name,
                tx_date=str(transaction.transaction_date) if transaction.transaction_date else None
            )
            dup_warning = f"⚠️ Similar to #{dup['id']} ({dup.get('match_reason', 'duplicate')}) — duplicate?" if dup else None
            card_text = format_receipt_card(transaction, dup_warning)
            await query.edit_message_text(card_text, reply_markup=get_confirmation_card_keyboard(pending_id, duplicate_warning=bool(dup)), parse_mode='HTML')
        else:
            await query.edit_message_text("❌ Transaction expired.", reply_markup=get_back_to_menu_keyboard())
        return

    elif action == "cancel_p":
        pending_id = parts[1]
        pending_transactions.pop(pending_id, None)
        await query.edit_message_text("❌ Receipt discarded.", reply_markup=get_back_to_menu_keyboard())
        return

    # --- 2. Quick Undo & Quick Add Actions ---
    elif action == "undo_tx":
        tx_id = int(parts[1])
        tx = get_transaction_by_id(tx_id)
        if not tx:
            await query.edit_message_text("❌ Transaction not found or already removed.", reply_markup=get_back_to_menu_keyboard())
            return
        deleted = delete_transaction(tx_id)
        if deleted:
            new_bal = recalculate_all_balances()
            from services.backup_service import backup_to_telegram
            backed_up = await backup_to_telegram(context.bot)
            status_line = "✅ Saved and backed up" if backed_up else "⚠️ Saved locally; cloud backup failed (will retry)"
            await query.edit_message_text(
                f"↩️ <b>Transaction #{tx_id} Undone!</b>\n"
                "━━━━━━━━━━━━━━\n"
                f"Payment reverted from your ledger.\n"
                f"💼 <b>Current Balance:</b> <b>{format_currency(new_bal)}</b>\n\n"
                f"<b>Status:</b> {status_line}",
                reply_markup=get_back_to_menu_keyboard(),
                parse_mode='HTML'
            )
        else:
            await query.edit_message_text("❌ Nothing was saved: Transaction not found.", reply_markup=get_back_to_menu_keyboard())
        return

    elif action == "quick_add":
        from utils.validation import parse_decimal_amount, validate_name
        try:
            amt = float(parse_decimal_amount(parts[1], allow_zero=False))
            payee = validate_name(parts[2], max_length=120)
        except ValueError as err:
            await query.edit_message_text(f"❌ Invalid quick entry: {err}")
            return
        cat = parts[3] if len(parts) > 3 else "Food & Dining"
        from database.models import Transaction
        now_dt = get_current_time_in_tz()
        tx = Transaction(
            transaction_type="SENT",
            amount=amt,
            person_name=payee,
            recipient_name=payee,
            category=cat,
            transaction_date=now_dt.date(),
            transaction_time=now_dt.strftime("%I:%M %p"),
            payment_app="One-Tap Quick Entry"
        )
        success = commit_transaction(tx)
        if success:
            await query.edit_message_text(
                f"✅ <b>Payment Saved! #{tx.id}</b>\n"
                "━━━━━━━━━━━━━━\n"
                f"💸 <b>{format_currency(amt)}</b> → <b>{html.escape(payee)}</b>\n"
                f"🏷 {html.escape(cat)}   📅 Today, {now_dt.strftime('%I:%M %p')}\n"
                f"💼 <b>Balance:</b> <b>{format_currency(tx.balance_after)}</b>",
                reply_markup=get_quick_undo_keyboard(tx.id),
                parse_mode='HTML'
            )
            from services.task_manager import schedule_debounced_backup
            schedule_debounced_backup(context.bot)
        return

    # 1. OCR Confirmation / Cancellation (Legacy fallback)
    elif action in ("confirm_tx", "cancel_tx"):
        tx_id = parts[1]
        transaction = pending_transactions.get(tx_id)
        
        if not transaction:
            await query.edit_message_text("❌ Transaction expired or no longer available.")
            return
            
        if action == "confirm_tx":
            success = commit_transaction(transaction)
            if success:
                response = format_success_message(transaction)
                await query.edit_message_text(response, parse_mode='HTML')
                from services.task_manager import schedule_debounced_backup
                schedule_debounced_backup(context.bot)
            else:
                await query.edit_message_text("⚠️ Transaction already recorded.")
            del pending_transactions[tx_id]
            
        elif action == "cancel_tx":
            await query.edit_message_text("❌ Transaction cancelled.")
            del pending_transactions[tx_id]
            
    # 2. Transaction Selection for Edit / Delete
    elif action == "select_edit":
        tx_id = int(parts[1])
        tx = get_transaction_by_id(tx_id)
        if not tx:
            await query.edit_message_text("❌ Transaction not found.")
            return
        date_str = tx['transaction_date'] or "Today"
        person = tx['person_name'] or "Unknown"
        text = (
            f"✏️ <b>Editing Transaction #{tx_id}</b>\n\n"
            f"• <b>Type:</b> {html.escape(str(tx['transaction_type']))}\n"
            f"• <b>Amount:</b> {html.escape(format_currency(tx['amount']))}\n"
            f"• <b>Person:</b> {html.escape(str(person))}\n"
            f"• <b>Date:</b> {html.escape(str(date_str))}\n\n"
            "Select what you would like to edit:"
        )
        await query.edit_message_text(text, reply_markup=get_edit_fields_keyboard(tx_id), parse_mode='HTML')
        
    elif action == "select_delete":
        if not is_admin_user(update):
            await query.answer("❌ Only the bot owner can delete transactions.", show_alert=True)
            return
        tx_id = int(parts[1])
        tx = get_transaction_by_id(tx_id)
        if not tx:
            await query.edit_message_text("❌ Transaction not found.")
            return
        date_str = tx['transaction_date'] or "Today"
        person = tx['person_name'] or "Unknown"
        text = (
            f"🗑️ <b>Delete Transaction #{tx_id}</b>\n\n"
            f"• <b>Type:</b> {html.escape(str(tx['transaction_type']))}\n"
            f"• <b>Amount:</b> {html.escape(format_currency(tx['amount']))}\n"
            f"• <b>Person:</b> {html.escape(str(person))}\n"
            f"• <b>Date:</b> {html.escape(str(date_str))}\n\n"
            "Are you sure you want to delete this transaction?"
        )
        await query.edit_message_text(text, reply_markup=get_delete_confirm_keyboard(tx_id), parse_mode='HTML')

    # 3. Field Selection for Editing
    elif action == "edit_field":
        field = parts[1]
        tx_id = int(parts[2])
        
        context.user_data['action'] = 'waiting_edit_value'
        context.user_data['edit_tx_id'] = tx_id
        context.user_data['edit_field'] = field
        
        prompts = {
            'amount': "💵 Please enter the <b>new amount</b> (e.g. <code>500</code> or <code>1250.50</code>):",
            'person': "👤 Please enter the <b>new name</b> (e.g. <code>Ramesh</code>):",
            'type': "🔄 Please enter the <b>new type</b> (<code>SENT</code> or <code>RECEIVED</code>):",
            'date': "📅 Please enter the <b>new date</b> (e.g. <code>05/09/2026</code> or <code>yesterday</code>):",
            'ref': "🔢 Please enter the <b>new reference/UTR number</b>:"
        }
        prompt = prompts.get(field, "Please enter the new value:")
        await query.edit_message_text(
            f"✏️ <b>Editing Transaction #{tx_id}</b>\n\n{prompt}",
            parse_mode='HTML'
        )
        
    elif action == "edit_cancel":
        context.user_data.pop('action', None)
        context.user_data.pop('edit_tx_id', None)
        context.user_data.pop('edit_field', None)
        await query.edit_message_text("❌ Editing cancelled.")

    # 4. Delete Confirmation
    elif action == "delete_confirm":
        if not is_admin_user(update):
            await query.answer("❌ Only the bot owner can delete transactions.", show_alert=True)
            return
        tx_id = int(parts[1])
        tx = get_transaction_by_id(tx_id)
        if not tx:
            await query.edit_message_text("❌ Transaction not found.")
            return

        amt_str = format_currency(tx['amount'])
        person = tx['person_name'] or "Unknown"

        from services.undo_service import record_delete_action
        chat_id = update.effective_chat.id if update.effective_chat else None
        user_id = update.effective_user.id if update.effective_user else None
        record_delete_action(tx, chat_id=chat_id, user_id=user_id)

        success = delete_transaction(tx_id)
        if success:
            new_bal = recalculate_all_balances()
            from services.backup_service import backup_to_telegram
            backed_up = await backup_to_telegram(context.bot)
            status_line = "✅ Saved and backed up" if backed_up else "⚠️ Saved locally; cloud backup failed (will retry)"

            if is_gdrive_available():
                from config import DATA_DIR
                from services.task_manager import create_tracked_task
                bkp = DATA_DIR / 'backup_transactions.json'
                if os.path.exists(bkp):
                    create_tracked_task(asyncio.to_thread(upload_backup_to_drive, str(bkp)), name="gdrive_backup_upload")
            from bot.keyboards import get_undo_keyboard
            await query.edit_message_text(
                f"🗑️ <b>Transaction #{tx_id} Deleted</b>\n\n"
                f"• <b>Amount:</b> {html.escape(amt_str)}\n"
                f"• <b>Person:</b> {html.escape(str(person))}\n\n"
                f"💰 <b>Updated Current Balance:</b> <b>{html.escape(format_currency(new_bal))}</b>\n\n"
                f"<b>Status:</b> {status_line}",
                reply_markup=get_undo_keyboard(),
                parse_mode='HTML'
            )
        else:
            await query.edit_message_text("❌ Nothing was saved: Failed to delete transaction.")

    elif action == "delete_cancel":
        if not is_admin_user(update):
            await query.answer("❌ Only the bot owner can cancel deletions.", show_alert=True)
            return
        context.user_data.pop('action', None)
        await query.edit_message_text("❌ Deletion cancelled.")

    elif action == "undo_action":
        if not is_admin_user(update):
            await query.answer("❌ Only the bot owner can undo changes.", show_alert=True)
            return
        from services.undo_service import perform_undo
        chat_id = update.effective_chat.id if update.effective_chat else None
        user_id = update.effective_user.id if update.effective_user else None
        success, msg = perform_undo(chat_id=chat_id, user_id=user_id)
        if success:
            from services.backup_service import backup_to_telegram
            backed_up = await backup_to_telegram(context.bot)
            status_line = "✅ Saved and backed up" if backed_up else "⚠️ Saved locally; cloud backup failed (will retry)"
            await query.edit_message_text(f"{msg}\n\n<b>Status:</b> {status_line}", parse_mode='HTML')
        else:
            await query.edit_message_text(f"❌ Nothing was saved: {msg}", parse_mode='HTML')


    # 4b. Correct Amount for Existing Transaction
    elif action == "correct_amount":
        tx_id = int(parts[1])
        new_amt = float(parts[2])
        tx = get_transaction_by_id(tx_id)
        if not tx:
            await query.edit_message_text("❌ Transaction not found.")
            return
        success = update_transaction(tx_id, {'amount': new_amt})
        if success:
            new_bal = recalculate_all_balances()
            from services.task_manager import schedule_debounced_backup
            schedule_debounced_backup(context.bot)
            if is_gdrive_available():
                from config import DATA_DIR
                from services.task_manager import create_tracked_task
                bkp = DATA_DIR / 'backup_transactions.json'
                if os.path.exists(bkp):
                    create_tracked_task(asyncio.to_thread(upload_backup_to_drive, str(bkp)), name="gdrive_backup_upload")
            await query.edit_message_text(
                f"✅ <b>Transaction updated successfully!</b>\n\n"
                f"• <b>Amount corrected to:</b> <b>{html.escape(format_currency(new_amt))}</b>\n"
                f"💰 <b>Updated Current Balance:</b> <code>{html.escape(format_currency(new_bal))}</code>",
                parse_mode='HTML'
            )
        else:
            await query.edit_message_text("❌ Failed to update transaction.")

    # 4c. Export Format Selection (PDF / Excel)
    elif action == "export_file":
        fmt = parts[1]
        if fmt == "pdf":
            from bot.commands import send_pdf_report
            await query.edit_message_text("⏳ Generating official PDF statement...")
            await send_pdf_report(query.message.chat, context.bot)
        elif fmt == "excel":
            from bot.commands import send_excel_report
            await query.edit_message_text("⏳ Generating Excel spreadsheet...")
            await send_excel_report(query.message.chat, context.bot)

    # 5. Interactive Filter Callbacks
    elif action == "filter":
        filter_type = parts[1]
        now = get_current_time_in_tz()
        
        if filter_type == "today":
            txs = search_transactions(target_date=now.date(), sort_by="date_desc")
            title = f"📅 Today's Transactions ({now.strftime('%d %b %Y')})"
        elif filter_type == "yesterday":
            y_date = now.date() - timedelta(days=1)
            txs = search_transactions(target_date=y_date, sort_by="date_desc")
            title = f"📅 Yesterday's Transactions ({y_date.strftime('%d %b %Y')})"
        elif filter_type == "this_month":
            txs = search_transactions(year=now.year, month=now.month, sort_by="date_desc")
            title = f"🗓️ This Month's Transactions ({now.strftime('%B %Y')})"
        elif filter_type == "monthly_stats":
            stats = get_monthly_summary(now.year, now.month)
            top_p = f"{stats['top_recipient']['person_name']} ({format_currency(stats['top_recipient']['total'])})" if stats['top_recipient'] else "N/A"
            text = (
                f"📊 <b>Monthly Analytics - {now.strftime('%B %Y')}</b>\n\n"
                f"🔴 <b>Total Sent:</b> {html.escape(format_currency(stats['total_sent']))}\n"
                f"🟢 <b>Total Received:</b> {html.escape(format_currency(stats['total_received']))}\n"
                f"📈 <b>Net Savings:</b> {html.escape(format_currency(stats['net_savings']))}\n\n"
                f"🔢 <b>Total Transactions:</b> {stats['tx_count']}\n"
                f"🏆 <b>Top Recipient:</b> {html.escape(str(top_p))}"
            )
            await query.edit_message_text(text, reply_markup=get_filter_keyboard(), parse_mode='HTML')
            return
        elif filter_type == "type_sent":
            txs = search_transactions(tx_type="SENT", limit=15, sort_by="date_desc")
            title = "🔴 Recent Sent Transactions"
        elif filter_type == "type_received":
            txs = search_transactions(tx_type="RECEIVED", limit=15, sort_by="date_desc")
            title = "🟢 Recent Received Transactions"
        elif filter_type == "show_ids":
            txs = get_recent_transactions(limit=10)
            if not txs:
                await query.edit_message_text("No transactions found.")
                return
            text = "🔍 <b>Transactions (With IDs & Ref)</b>\n\n"
            for t in txs:
                text += (
                    f"🆔 <b>ID: #{t['id']}</b> | {html.escape(str(t['transaction_type']))}\n"
                    f"📅 {html.escape(str(t['transaction_date']))} | 👤 {html.escape(str(t['person_name'] or 'N/A'))}\n"
                    f"💵 {html.escape(format_currency(t['amount']))} | 🔢 Ref: <code>{html.escape(str(t['reference_number'] or 'N/A'))}</code>\n\n"
                )
            await query.edit_message_text(text, reply_markup=get_filter_keyboard(), parse_mode='HTML')
            return
        elif filter_type == "open_sort":
            await query.edit_message_text("🔀 <b>Choose Sorting Order:</b>", reply_markup=get_sort_keyboard(), parse_mode='HTML')
            return
        else:
            txs = []
            title = "Filtered Transactions"
            
        if not txs:
            await query.edit_message_text(f"No transactions found for <b>{html.escape(title)}</b>.", reply_markup=get_filter_keyboard(), parse_mode='HTML')
            return
            
        total_amt = sum(t['amount'] for t in txs)
        text = f"📜 <b>{html.escape(title)}</b> (Total: {html.escape(format_currency(total_amt))})\n\n"
        for t in txs[:10]:
            person = t['person_name'] or "Unknown"
            date_s = format_display_date(t['transaction_date'])
            time_str = f" | ⏰ {t['transaction_time']}" if t['transaction_time'] and t['transaction_time'] != 'Unknown Time' else ""
            type_badge = "🔴 SENT" if t['transaction_type'] == 'SENT' else "🟢 RECEIVED"
            text += f"• <b>{html.escape(date_s)}</b>{html.escape(time_str)} | {type_badge}\n👤 {html.escape(str(person))} | 💵 {html.escape(format_currency(t['amount']))}\n\n"
        await query.edit_message_text(text, reply_markup=get_filter_keyboard(), parse_mode='HTML')

    # 6. Sorting Callbacks
    elif action == "sort":
        sort_by = parts[1]
        if sort_by == "back_filters":
            await query.edit_message_text("🎛️ <b>Filter & Sort Transactions:</b>\n\nChoose an option below:", reply_markup=get_filter_keyboard(), parse_mode='HTML')
            return
            
        sort_names = {
            "date_desc": "Date (Newest first)",
            "date_asc": "Date (Oldest first)",
            "amount_desc": "Amount (Highest first)",
            "amount_asc": "Amount (Lowest first)"
        }
        txs = search_transactions(sort_by=sort_by, limit=10)
        if not txs:
            await query.edit_message_text("No transactions found.")
            return
            
        text = f"🔀 <b>Sorted by: {html.escape(sort_names.get(sort_by, sort_by))}</b>\n\n"
        for t in txs:
            date_s = format_display_date(t['transaction_date'])
            time_str = f" | ⏰ {t['transaction_time']}" if t['transaction_time'] and t['transaction_time'] != 'Unknown Time' else ""
            person = t['person_name'] or "Unknown"
            type_badge = "🔴 SENT" if t['transaction_type'] == 'SENT' else "🟢 RECEIVED"
            text += (
                f"• <b>{html.escape(date_s)}</b>{html.escape(time_str)} | {type_badge}\n"
                f"👤 {html.escape(str(person))}\n"
                f"💵 {html.escape(format_currency(t['amount']))}\n"
                f"Balance: {html.escape(format_currency(t['balance_after']))}\n\n"
            )
        await query.edit_message_text(text, reply_markup=get_sort_keyboard(), parse_mode='HTML')

    # 7. Cafeteria Menu Callbacks
    elif action == "cafe_pick":
        tx_id = int(parts[1])
        item_name = parts[2]
        tx = get_transaction_by_id(tx_id)
        if tx:
            new_name = f"VIKRAMAN NAIR K (Cafeteria: {item_name})"
            update_transaction(tx_id, {'person_name': new_name, 'category': 'Food & Dining'})
            from services.task_manager import schedule_debounced_backup
            schedule_debounced_backup(context.bot)
            amt_s = format_currency(tx['amount'])
            bal_s = format_currency(tx['balance_after'])
            from bot.keyboards import get_cafeteria_tagged_keyboard
            await query.edit_message_text(
                f"🍽️ <b>Cafeteria Order Tagged!</b>\n\n"
                f"• <b>Ordered Item:</b> 🍽️ <b>{html.escape(item_name)}</b>\n"
                f"• <b>Merchant:</b> VIKRAMAN NAIR K\n"
                f"• <b>Amount:</b> <b>{html.escape(amt_s)}</b>\n"
                f"• <b>Category:</b> 🍔 Food & Dining\n"
                f"• <b>Balance:</b> {html.escape(bal_s)}\n\n"
                f"✅ Successfully saved to your transaction record!",
                reply_markup=get_cafeteria_tagged_keyboard(tx_id),
                parse_mode='HTML'
            )
        else:
            await query.edit_message_text(f"🍽️ Tagged order: <b>{html.escape(item_name)}</b>", parse_mode='HTML')

    elif action == "cafe_mode":
        tx_id = int(parts[1])
        mode = parts[2]
        tx = get_transaction_by_id(tx_id)
        amt = float(tx['amount']) if tx else 0.0
        
        if mode == "1":
            from bot.keyboards import get_cafeteria_single_item_keyboard
            await query.edit_message_text(
                f"🍽️ <b>Single Item Selection (Paid {html.escape(format_currency(amt))}):</b>\n\n"
                f"Pick the single item you ordered below or enter custom amount:",
                reply_markup=get_cafeteria_single_item_keyboard(tx_id, amt),
                parse_mode='HTML'
            )
        elif mode == "2":
            from bot.keyboards import get_cafeteria_two_items_keyboard
            await query.edit_message_text(
                f"🍽️ <b>Two Items Mode (Paid {html.escape(format_currency(amt))}):</b>\n\n"
                f"Pick your combination below or open the plate builder:",
                reply_markup=get_cafeteria_two_items_keyboard(tx_id, amt),
                parse_mode='HTML'
            )
        elif mode == "cart":
            from bot.keyboards import get_cafeteria_cart_keyboard
            cart = context.user_data.get(f'cafe_cart_{tx_id}', [])
            total_cart = sum(it['price'] for it in cart)
            cart_lines = "\n".join([f"• {it['name']} — ₹{it['price']:.0f}" for it in cart]) if cart else "<i>(Plate is empty. Tap items below to add)</i>"
            await query.edit_message_text(
                f"🛒 <b>CAFETERIA PLATE BUILDER</b>\n"
                f"Paid Bill: <b>{html.escape(format_currency(amt))}</b>\n\n"
                f"<b>Items in Plate:</b>\n{cart_lines}\n\n"
                f"<b>Plate Sum:</b> <b>{html.escape(format_currency(total_cart))}</b> / {html.escape(format_currency(amt))}\n\n"
                f"Tap items below to add to your plate:",
                reply_markup=get_cafeteria_cart_keyboard(tx_id, amt, cart),
                parse_mode='HTML'
            )
        elif mode == "browse":
            from bot.keyboards import get_cafeteria_selection_keyboard
            await query.edit_message_text(
                f"📋 <b>Browse Cafeteria Menu (Paid {html.escape(format_currency(amt))}):</b>\n\n"
                f"Select a category below to see all items:",
                reply_markup=get_cafeteria_selection_keyboard(tx_id, amt),
                parse_mode='HTML'
            )

    elif action == "cafe_custom_prompt":
        tx_id = int(parts[1])
        item_type = parts[2]
        context.user_data['action'] = 'waiting_cafe_custom_amount'
        context.user_data['cafe_tx_id'] = tx_id
        context.user_data['cafe_item'] = item_type
        icon = "🍨" if "ice" in item_type.lower() else "✏️"
        await query.edit_message_text(
            f"{icon} <b>Enter Amount for {html.escape(item_type)}:</b>\n\n"
            f"Please reply with the amount (e.g. <code>40</code>, <code>60</code>, <code>120</code>) or item name with price:",
            parse_mode='HTML'
        )

    elif action == "cafe_cart_add":
        tx_id = int(parts[1])
        item_name = parts[2]
        item_price = float(parts[3])
        tx = get_transaction_by_id(tx_id)
        amt = float(tx['amount']) if tx else 0.0
        
        cart_key = f'cafe_cart_{tx_id}'
        if cart_key not in context.user_data:
            context.user_data[cart_key] = []
        context.user_data[cart_key].append({'name': item_name, 'price': item_price})
        
        cart = context.user_data[cart_key]
        total_cart = sum(it['price'] for it in cart)
        cart_lines = "\n".join([f"• {it['name']} — ₹{it['price']:.0f}" for it in cart])
        
        from bot.keyboards import get_cafeteria_cart_keyboard
        await query.edit_message_text(
            f"🛒 <b>CAFETERIA PLATE BUILDER</b>\n"
            f"Paid Bill: <b>{html.escape(format_currency(amt))}</b>\n\n"
            f"<b>Items in Plate ({len(cart)}):</b>\n{cart_lines}\n\n"
            f"<b>Plate Sum:</b> <b>{html.escape(format_currency(total_cart))}</b> / {html.escape(format_currency(amt))}\n\n"
            f"Tap more items or tap <b>Save Plate</b> when done:",
            reply_markup=get_cafeteria_cart_keyboard(tx_id, amt, cart),
            parse_mode='HTML'
        )

    elif action == "cafe_cart_clear":
        tx_id = int(parts[1])
        tx = get_transaction_by_id(tx_id)
        amt = float(tx['amount']) if tx else 0.0
        context.user_data[f'cafe_cart_{tx_id}'] = []
        from bot.keyboards import get_cafeteria_cart_keyboard
        await query.edit_message_text(
            f"🛒 <b>Plate Cleared!</b>\n"
            f"Paid Bill: <b>{html.escape(format_currency(amt))}</b>\n\n"
            f"Tap items below to build your plate:",
            reply_markup=get_cafeteria_cart_keyboard(tx_id, amt, []),
            parse_mode='HTML'
        )

    elif action == "cafe_cart_done":
        tx_id = int(parts[1])
        cart = context.user_data.pop(f'cafe_cart_{tx_id}', [])
        tx = get_transaction_by_id(tx_id)
        if tx and cart:
            item_names = " + ".join([it['name'] for it in cart])
            new_name = f"VIKRAMAN NAIR K (Cafeteria: {item_names})"
            update_transaction(tx_id, {'person_name': new_name, 'category': 'Food & Dining'})
            from services.task_manager import schedule_debounced_backup
            schedule_debounced_backup(context.bot)
            amt_s = format_currency(tx['amount'])
            bal_s = format_currency(tx['balance_after'])
            from bot.keyboards import get_cafeteria_tagged_keyboard
            await query.edit_message_text(
                f"🍽️ <b>Cafeteria Plate Saved!</b>\n\n"
                f"• <b>Ordered Items:</b> 🍽️ <b>{html.escape(item_names)}</b>\n"
                f"• <b>Merchant:</b> VIKRAMAN NAIR K\n"
                f"• <b>Bill Amount:</b> <b>{html.escape(amt_s)}</b>\n"
                f"• <b>Category:</b> 🍔 Food & Dining\n"
                f"• <b>Balance:</b> {html.escape(bal_s)}\n\n"
                f"✅ Successfully saved to your transaction record!",
                reply_markup=get_cafeteria_tagged_keyboard(tx_id),
                parse_mode='HTML'
            )
        elif tx:
            from bot.keyboards import get_cafeteria_selection_keyboard
            await query.edit_message_text(
                "⚠️ No items were in the plate. Choose an item below:",
                reply_markup=get_cafeteria_selection_keyboard(tx_id, float(tx['amount'])),
                parse_mode='HTML'
            )

    elif action == "cafe_cat":
        tx_id = int(parts[1])
        cat_name = parts[2]
        from bot.keyboards import get_cafeteria_category_keyboard
        tx = get_transaction_by_id(tx_id)
        amt_str = format_currency(tx['amount']) if tx else ""
        await query.edit_message_text(
            f"🍽️ <b>Cafeteria Menu — {html.escape(cat_name)}</b>\n"
            f"Bill Amount: <b>{html.escape(amt_str)}</b>\n\n"
            f"Tap any item to tag it:",
            reply_markup=get_cafeteria_category_keyboard(tx_id, cat_name),
            parse_mode='HTML'
        )

    elif action == "cafe_back":
        tx_id = int(parts[1])
        from bot.keyboards import get_cafeteria_selection_keyboard
        tx = get_transaction_by_id(tx_id)
        amt = float(tx['amount']) if tx else 0.0
        await query.edit_message_text(
            f"🍽️ <b>Cafeteria Menu Selection (Paid {html.escape(format_currency(amt))}):</b>\n\n"
            f"<b>How many items or what did you order?</b>\n"
            f"Select an option below to tag your order:",
            reply_markup=get_cafeteria_selection_keyboard(tx_id, amt),
            parse_mode='HTML'
        )

    elif action == "cafe_addon":
        tx_id = int(parts[1])
        addon = parts[2]
        tx = get_transaction_by_id(tx_id)
        if tx:
            current_name = tx['person_name'] or "VIKRAMAN NAIR K (Cafeteria)"
            updated_name = f"{current_name} + {addon}"
            update_transaction(tx_id, {'person_name': updated_name, 'category': 'Food & Dining'})
            from bot.keyboards import get_cafeteria_tagged_keyboard
            await query.edit_message_text(
                f"🍽️ <b>Add-on Tagged:</b> +₹5 {html.escape(addon)}\n"
                f"• Updated Merchant: {html.escape(updated_name)}\n"
                f"• Total Amount: <b>{html.escape(format_currency(tx['amount']))}</b>\n\n"
                f"✅ Updated record!",
                reply_markup=get_cafeteria_tagged_keyboard(tx_id),
                parse_mode='HTML'
            )

    elif action == "cafe_edit":
        tx_id = int(parts[1])
        tx = get_transaction_by_id(tx_id)
        if tx:
            from bot.keyboards import get_cafeteria_selection_keyboard
            amt = float(tx['amount'])
            await query.edit_message_text(
                f"✏️ <b>Edit Cafeteria Order for Transaction #{tx_id} (Paid {html.escape(format_currency(amt))}):</b>\n\n"
                f"<b>How many items or what did you order?</b>\n"
                f"Select an option below to update your order:",
                reply_markup=get_cafeteria_selection_keyboard(tx_id, amt),
                parse_mode='HTML'
            )
        else:
            await query.edit_message_text("❌ Transaction not found.")

    elif action == "cafe_stats":
        from services.cafeteria_service import format_cafeteria_stats
        stats_text = format_cafeteria_stats()
        await query.message.reply_text(stats_text, parse_mode='HTML')

    elif action == "cafe_view_menu":
        from services.cafeteria_service import format_full_menu
        from bot.keyboards import get_menu_view_keyboard
        menu_text = format_full_menu()
        await query.message.reply_text(menu_text, reply_markup=get_menu_view_keyboard(), parse_mode='HTML')

    elif action == "cafe_menu_add_prompt":
        context.user_data['action'] = 'waiting_add_menu_item'
        await query.message.reply_text(
            "➕ <b>Add Custom Menu Item</b>\n\n"
            "Please send the item details in this format:\n"
            "<code>Item Name, Price, Category</code>\n\n"
            "<i>Examples:</i>\n"
            "• <code>Paneer Roll, 45, Snacks</code>\n"
            "• <code>Mango Lassi, 35, Beverages</code>\n"
            "• <code>Veg Noodles, 50, Chinese</code>",
            parse_mode='HTML'
        )

    elif action == "cafe_menu_del_prompt":
        from database.db import get_custom_menu_items
        custom_items = get_custom_menu_items()
        if not custom_items:
            await query.message.reply_text(
                "ℹ️ No custom menu items found to delete. Predefined standard items cannot be removed.",
                reply_markup=get_back_to_menu_keyboard(),
                parse_mode='HTML'
            )
        else:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = []
            for it in custom_items:
                keyboard.append([InlineKeyboardButton(f"🗑️ Delete {it['name']} (₹{it['price']:.0f})", callback_data=f"cafe_del_item:{it['id']}")])
            keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cafe_del_cancel")])
            await query.message.reply_text(
                "🗑️ <b>Select Custom Menu Item to Remove:</b>",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )

    elif action == "cafe_del_item":
        item_id = int(parts[1])
        from database.db import delete_custom_menu_item_by_id
        success, name = delete_custom_menu_item_by_id(item_id)
        if success:
            await query.edit_message_text(f"✅ Removed custom item: <b>{html.escape(name)}</b> from cafeteria menu.", reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')
        else:
            await query.edit_message_text("❌ Failed to remove menu item.", reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')

    elif action == "cafe_del_cancel":
        await query.edit_message_text("❌ Menu item deletion cancelled.", reply_markup=get_back_to_menu_keyboard())


    elif action == "cafe_edit_last":
        from database.queries import get_cafeteria_transactions
        from bot.keyboards import get_cafeteria_selection_keyboard
        cafe_txs = get_cafeteria_transactions(limit=1)
        if cafe_txs:
            tx = cafe_txs[0]
            tx_id = tx['id']
            amt = float(tx['amount'])
            await query.message.reply_text(
                f"✏️ <b>Edit Cafeteria Order #{tx_id} (Paid {html.escape(format_currency(amt))}):</b>\n\n"
                f"Select an option below to update your order:",
                reply_markup=get_cafeteria_selection_keyboard(tx_id, amt),
                parse_mode='HTML'
            )
        else:
            await query.message.reply_text("❌ No recent cafeteria payments found.")

    elif action == "cafe_skip":
        tx_id = int(parts[1])
        tx = get_transaction_by_id(tx_id)
        amt_s = format_currency(tx['amount']) if tx else ""
        from bot.keyboards import get_cafeteria_tagged_keyboard
        await query.edit_message_text(
            f"✅ <b>Cafeteria Payment Recorded</b> ({html.escape(amt_s)})\n"
            f"Tagged as: 🍔 <b>Food & Dining</b> (General)",
            reply_markup=get_cafeteria_tagged_keyboard(tx_id),
            parse_mode='HTML'
        )




def format_success_message(t) -> str:
    """Formats transaction confirmation message in clean, robust HTML with category badge & budget alerts."""
    icon = "🔴 Payment Sent" if t.transaction_type == 'SENT' else "🟢 Payment Received"
    person_label = "To" if t.transaction_type == 'SENT' else "From"
    person_name = html.escape(str(t.person_name or "Unknown"))

    if hasattr(t.transaction_date, 'strftime'):
        date_str = t.transaction_date.strftime("%d %b %Y")
    elif t.transaction_date:
        date_str = str(t.transaction_date)
    else:
        date_str = "Today"
    date_str = html.escape(date_str)

    time_part = f" ({html.escape(t.transaction_time)})" if t.transaction_time and t.transaction_time not in ('N/A', 'Unknown Time') else ""
    bank_part = f"\n🏦 <b>Bank:</b> {html.escape(t.bank_name)}" if t.bank_name else ""
    ref_part = f"\n🔢 <b>Ref / UTR:</b> <code>{html.escape(str(t.reference_number))}</code>" if t.reference_number else ""
    app_part = f" • <i>{html.escape(t.payment_app)}</i>" if t.payment_app and t.payment_app not in ('Generic', '') else ""

    # Category styling
    from services.category_service import get_category_icon
    cat_val = getattr(t, 'category', 'General') or 'General'
    cat_icon = get_category_icon(cat_val)
    cat_part = f"\n🏷️ <b>Category:</b> {cat_icon} {html.escape(cat_val)}"

    bal_before = html.escape(format_currency(t.balance_before))
    bal_after = html.escape(format_currency(t.balance_after))
    amt = html.escape(format_currency(t.amount))

    # Proactive budget alert check
    from services.budget_service import check_budget_alert
    budget_alert = check_budget_alert(t.amount, t.transaction_type)

    return (
        f"✅ <b>{icon}</b>\n\n"
        f"👤 <b>{person_label}:</b> {person_name}\n"
        f"💵 <b>Amount:</b> <b>{amt}</b>{app_part}\n"
        f"📅 <b>Date:</b> {date_str}{time_part}"
        f"{cat_part}"
        f"{bank_part}"
        f"{ref_part}\n\n"
        f"💰 <b>Balance:</b> {bal_before} ➔ <b>{bal_after}</b>"
        f"{budget_alert}"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles incoming text messages, interactive steps, and payment parsing."""
    if not await require_authorized(update): return
    if not update.message or not update.message.text: return
    
    text = update.message.text.strip()
    chat_id = str(update.message.chat_id)
    message_id = str(update.message.message_id)
    
    # Normalize commands e.g. \\date, /search, /monthly, /filter, /sort, /details, /undo
    from bot.commands import (
        edit_command, delete_command, history_command, balance_command,
        date_command, search_command, monthly_command, filter_command, sort_command, details_command, amount_command,
        undo_command
    )
    cmd_lower = text.lower()
    
    if cmd_lower in (r'\\undo', 'undo', '/undo', 'revert', '/revert'):
        await undo_command(update, context)
        return
    elif cmd_lower in (r'\\edit', 'edit', '/edit'):
        await edit_command(update, context)
        return
    elif cmd_lower in (r'\\delete', 'delete', '/delete'):
        await delete_command(update, context)
        return
    elif cmd_lower in (r'\\history', 'history', '/history'):
        await history_command(update, context)
        return
    elif cmd_lower in (r'\\balance', 'balance', '/balance'):
        await balance_command(update, context)
        return
    elif cmd_lower.startswith((r'\\date', 'date ', '/date ')):
        parts = text.split(maxsplit=1)
        context.args = [parts[1]] if len(parts) > 1 else []
        await date_command(update, context)
        return
    elif cmd_lower.startswith((r'\\search', 'search ', '/search ')):
        parts = text.split(maxsplit=1)
        context.args = [parts[1]] if len(parts) > 1 else []
        await search_command(update, context)
        return
    elif cmd_lower.startswith((r'\\amount', 'amount ', '/amount ')):
        parts = text.split(maxsplit=1)
        context.args = [parts[1]] if len(parts) > 1 else []
        await amount_command(update, context)
        return
    elif cmd_lower in (r'\\monthly', 'monthly', '/monthly', 'stats', '/stats'):
        await monthly_command(update, context)
        return
    elif cmd_lower in (r'\\filter', 'filter', '/filter'):
        await filter_command(update, context)
        return
    elif cmd_lower in (r'\\sort', 'sort', '/sort'):
        await sort_command(update, context)
        return
    elif cmd_lower in (r'\\details', 'details', '/details', 'ids', '/ids'):
        await details_command(update, context)
        return
    elif cmd_lower in (r'\\menu', 'menu', '/menu', 'cafeteria', '/cafeteria', 'canteen', '/canteen'):
        from bot.commands import menu_command
        await menu_command(update, context)
        return
    elif cmd_lower in (r'\\cafestats', 'cafestats', '/cafestats', 'cafespends', '/cafespends'):
        from bot.commands import cafestats_command
        await cafestats_command(update, context)
        return
    elif cmd_lower.startswith((r'\\cafeedit', 'cafeedit', '/cafeedit', 'editcafe', '/editcafe')):
        parts = text.split(maxsplit=1)
        context.args = [parts[1]] if len(parts) > 1 else []
        from bot.commands import cafeedit_command
        await cafeedit_command(update, context)
        return
        
    # Quick standalone amount search: if user just sends a number like "5000" or "400"
    clean_num = text.replace(',', '').replace('₹', '').strip()
    if clean_num.isdigit() and len(clean_num) >= 2:
        val = float(clean_num)
        if 'action' not in context.user_data:
            txs = search_transactions(exact_amount=val)
            if txs:
                context.args = [clean_num]
                await amount_command(update, context)
                return

    # Check if user is in an interactive state (all pending actions are admin mutations)
    pending_action = context.user_data.get('action')
    if pending_action:
        if not await require_admin(update):
            return
    
    if pending_action == 'waiting_edit_id':
        clean_id_str = text.replace('#', '').strip()
        if clean_id_str.isdigit():
            num = int(clean_id_str)
            recent_ids = context.user_data.get('recent_edit_ids') or [t['id'] for t in get_recent_transactions(limit=10)]
            tx = None
            if 1 <= num <= len(recent_ids):
                tx = get_transaction_by_id(recent_ids[num - 1])
            if not tx:
                tx = get_transaction_by_id(num)
            if tx:
                tx_id = tx['id']
                context.user_data.pop('action', None)
                context.user_data.pop('recent_edit_ids', None)
                date_str = tx['transaction_date'] or "Today"
                person = tx['person_name'] or "Unknown"
                msg = (
                    f"✏️ <b>Editing Transaction #{tx_id}</b>\n\n"
                    f"• <b>Type:</b> {html.escape(str(tx['transaction_type']))}\n"
                    f"• <b>Amount:</b> {html.escape(format_currency(tx['amount']))}\n"
                    f"• <b>Person:</b> {html.escape(str(person))}\n"
                    f"• <b>Date:</b> {html.escape(str(date_str))}\n\n"
                    "Select what you would like to edit:"
                )
                await update.message.reply_text(msg, reply_markup=get_edit_fields_keyboard(tx_id), parse_mode='HTML')
                return
            else:
                await update.message.reply_text("❌ Transaction not found. Please send a valid number (1-6) or ID:")
                return
                
    elif pending_action == 'waiting_delete_id':
        clean_id_str = text.replace('#', '').strip()
        if clean_id_str.isdigit():
            num = int(clean_id_str)
            recent_ids = context.user_data.get('recent_delete_ids') or [t['id'] for t in get_recent_transactions(limit=10)]
            tx = None
            if 1 <= num <= len(recent_ids):
                tx = get_transaction_by_id(recent_ids[num - 1])
            if not tx:
                tx = get_transaction_by_id(num)
            if tx:
                tx_id = tx['id']
                context.user_data.pop('action', None)
                context.user_data.pop('recent_delete_ids', None)
                date_str = tx['transaction_date'] or "Today"
                person = tx['person_name'] or "Unknown"
                msg = (
                    f"🗑️ <b>Delete Transaction #{tx_id}</b>\n\n"
                    f"• <b>Type:</b> {html.escape(str(tx['transaction_type']))}\n"
                    f"• <b>Amount:</b> {html.escape(format_currency(tx['amount']))}\n"
                    f"• <b>Person:</b> {html.escape(str(person))}\n"
                    f"• <b>Date:</b> {html.escape(str(date_str))}\n\n"
                    "Are you sure you want to delete this transaction?"
                )
                await update.message.reply_text(msg, reply_markup=get_delete_confirm_keyboard(tx_id), parse_mode='HTML')
                return
            else:
                await update.message.reply_text("❌ Transaction not found. Please send a valid number (1-6) or ID:")
                return
                
    elif pending_action == 'waiting_edit_value':
        tx_id = context.user_data.get('edit_tx_id')
        field = context.user_data.get('edit_field')

        updates = {}
        needs_recalc = False

        if field == 'amount':
            try:
                from utils.validation import parse_decimal_amount
                new_amt = float(parse_decimal_amount(text, allow_zero=False))
            except ValueError as err:
                await update.message.reply_text(f"❌ Invalid amount: {err}. Please enter a valid positive number:")
                return
            updates['amount'] = new_amt
            needs_recalc = True
        elif field == 'person':
            from utils.validation import validate_name
            try:
                clean_name = validate_name(text.title(), max_length=120, field_name="Person name", required=True)
            except ValueError as err:
                await update.message.reply_text(f"❌ Invalid name: {err}")
                return
            updates['person_name'] = clean_name
            tx = get_transaction_by_id(tx_id)
            if tx and tx['transaction_type'] == 'SENT':
                updates['recipient_name'] = clean_name
            else:
                updates['sender_name'] = clean_name
        elif field == 'type':
            try:
                from utils.validation import validate_transaction_type
                new_type = validate_transaction_type(text)
            except ValueError as err:
                await update.message.reply_text(f"❌ Invalid type: {err}. Please enter <code>SENT</code> or <code>RECEIVED</code>:", parse_mode='HTML')
                return
            updates['transaction_type'] = new_type
            needs_recalc = True
        elif field == 'date':
            parsed_d = parse_date(text)
            if not parsed_d:
                await update.message.reply_text("❌ Invalid date. Example: <code>05/09/2026</code> or <code>yesterday</code>:", parse_mode='HTML')
                return
            updates['transaction_date'] = parsed_d
            needs_recalc = True
        elif field == 'ref':
            from utils.validation import validate_reference
            try:
                updates['reference_number'] = validate_reference(text, max_length=100)
            except ValueError as err:
                await update.message.reply_text(f"❌ Invalid reference: {err}")
                return

        tx = get_transaction_by_id(tx_id)
        if tx:
            from services.undo_service import record_edit_action
            record_edit_action(tx)

        success = update_transaction(tx_id, updates)
        context.user_data.pop('action', None)
        context.user_data.pop('edit_tx_id', None)
        context.user_data.pop('edit_field', None)

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
                bal_flow = f"\n💰 <b>Balance Flow:</b> {html.escape(format_currency(updated_tx['balance_before']))} ➔ <b>{html.escape(format_currency(updated_tx['balance_after']))}</b>"

            from bot.keyboards import get_undo_keyboard
            from services.task_manager import schedule_debounced_backup
            schedule_debounced_backup(context.bot)
            if is_gdrive_available():
                from config import DATA_DIR
                from services.task_manager import create_tracked_task
                bkp = DATA_DIR / 'backup_transactions.json'
                if os.path.exists(bkp):
                    create_tracked_task(asyncio.to_thread(upload_backup_to_drive, str(bkp)), name="gdrive_backup_upload")
            await update.message.reply_text(
                f"✅ <b>Transaction #{tx_id} Updated</b>\n\n"
                f"👤 <b>Person:</b> {html.escape(str(person))}\n"
                f"💵 <b>Amount:</b> <b>{html.escape(amt_s)}</b>{bal_flow}\n\n"
                f"💳 <b>Current Balance:</b> <b>{html.escape(format_currency(new_bal))}</b>",
                reply_markup=get_undo_keyboard(),
                parse_mode='HTML'
            )
            return
        else:
            await update.message.reply_text("❌ Failed to update transaction.")
            return

    elif pending_action == 'waiting_cafe_custom_amount':
        tx_id = context.user_data.pop('cafe_tx_id', None)
        item_type = context.user_data.pop('cafe_item', 'Custom')
        context.user_data.pop('action', None)
        
        try:
            from utils.validation import parse_decimal_amount
            parsed_amt = float(parse_decimal_amount(text, allow_zero=False))
        except ValueError:
            parsed_amt = 0.0
        custom_note = text.strip()
        
        if tx_id:
            tx = get_transaction_by_id(tx_id)
            if tx:
                if parsed_amt > 0:
                    tag_desc = f"{item_type} (₹{parsed_amt:.0f})" if item_type != 'Custom' else f"Custom Item (₹{parsed_amt:.0f})"
                else:
                    tag_desc = f"{item_type}: {custom_note}"
                    
                new_name = f"VIKRAMAN NAIR K (Cafeteria: {tag_desc})"
                update_transaction(tx_id, {'person_name': new_name, 'category': 'Food & Dining'})
                from services.task_manager import schedule_debounced_backup
                schedule_debounced_backup(context.bot)
                amt_s = format_currency(tx['amount'])
                bal_s = format_currency(tx['balance_after'])
                from bot.keyboards import get_cafeteria_tagged_keyboard
                await update.message.reply_text(
                    f"🍽️ <b>Cafeteria Order Tagged!</b>\n\n"
                    f"• <b>Custom Item:</b> 🍽️ <b>{html.escape(tag_desc)}</b>\n"
                    f"• <b>Merchant:</b> VIKRAMAN NAIR K\n"
                    f"• <b>Bill Amount:</b> <b>{html.escape(amt_s)}</b>\n"
                    f"• <b>Category:</b> 🍔 Food & Dining\n"
                    f"• <b>Balance:</b> {html.escape(bal_s)}\n\n"
                    f"✅ Successfully updated your cafeteria record!",
                    reply_markup=get_cafeteria_tagged_keyboard(tx_id),
                    parse_mode='HTML'
                )
                return
        await update.message.reply_text(f"🍽️ Tagged cafeteria order: <b>{html.escape(text)}</b>", parse_mode='HTML')
        return

    elif pending_action == 'waiting_add_menu_item':
        context.user_data.pop('action', None)
        from services.cafeteria_service import add_custom_menu_item
        from utils.validation import parse_decimal_amount
        full_arg = text.strip()
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
            tokens = full_arg.split()
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
                category = " ".join(cat_tokens) if cat_tokens else "Custom"
            else:
                name = full_arg

        if not name or price is None or price <= 0:
            await update.message.reply_text(
                "❌ <b>Invalid format!</b>\n\n"
                "Please send: <code>Item Name, Price, Category</code>\n"
                "Example: <code>Paneer Roll, 45, Snacks</code>",
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
                f"Use <code>/menu</code> to view updated menu!",
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text(f"❌ {html.escape(msg)}", parse_mode='HTML')
        return

    elif pending_action == 'waiting_del_menu_item':
        context.user_data.pop('action', None)
        from services.cafeteria_service import delete_custom_menu_item
        success, msg = delete_custom_menu_item(text.strip())
        if success:
            await update.message.reply_text(f"✅ {html.escape(msg)}", parse_mode='HTML')
        else:
            await update.message.reply_text(f"❌ {html.escape(msg)}", parse_mode='HTML')
        return

    elif pending_action == 'waiting_edit_pending_value':
        pending_id = context.user_data.pop('pending_id', None)
        field = context.user_data.pop('pending_field', None)
        context.user_data.pop('action', None)

        transaction = pending_transactions.get(pending_id)
        if not transaction:
            await update.message.reply_text("❌ Receipt has expired.", reply_markup=get_home_menu_keyboard(), parse_mode='HTML')
            return

        if field == 'amount':
            try:
                from utils.validation import parse_decimal_amount
                val = float(parse_decimal_amount(text, allow_zero=False))
                transaction.amount = val
            except ValueError as err:
                await update.message.reply_text(f"❌ Invalid amount: {err}. Please send a positive number:")
                context.user_data['pending_id'] = pending_id
                context.user_data['pending_field'] = field
                context.user_data['action'] = 'waiting_edit_pending_value'
                return
        elif field == 'person':
            from utils.validation import validate_name
            try:
                p_name = validate_name(text.strip(), max_length=120, field_name="Person name", required=True)
                transaction.person_name = p_name
                if transaction.transaction_type == 'SENT':
                    transaction.recipient_name = p_name
                else:
                    transaction.sender_name = p_name
            except ValueError as err:
                await update.message.reply_text(f"❌ Invalid name: {err}. Please enter a valid name:")
                context.user_data['pending_id'] = pending_id
                context.user_data['pending_field'] = field
                context.user_data['action'] = 'waiting_edit_pending_value'
                return
        elif field == 'type':
            try:
                from utils.validation import validate_transaction_type
                transaction.transaction_type = validate_transaction_type(text)
            except ValueError as err:
                await update.message.reply_text(f"❌ Invalid type: {err}. Must be SENT or RECEIVED:")
                context.user_data['pending_id'] = pending_id
                context.user_data['pending_field'] = field
                context.user_data['action'] = 'waiting_edit_pending_value'
                return
        elif field == 'date':
            d_val = parse_date(text)
            if d_val:
                transaction.transaction_date = d_val

        dup = find_potential_duplicate(
            amount=transaction.amount,
            reference_number=transaction.reference_number,
            person_name=transaction.person_name,
            tx_date=str(transaction.transaction_date) if transaction.transaction_date else None
        )
        dup_warning = f"⚠️ Similar to #{dup['id']} ({dup.get('match_reason', 'duplicate')}) — duplicate?" if dup else None
        card_text = format_receipt_card(transaction, dup_warning)
        await update.message.reply_text(
            f"✅ <b>Field Updated!</b>\n\n{card_text}",
            reply_markup=get_confirmation_card_keyboard(pending_id, duplicate_warning=bool(dup)),
            parse_mode='HTML'
        )
        return

    elif pending_action == 'waiting_payee_amount':
        payee = context.user_data.pop('quick_payee', 'Payee')
        tt = context.user_data.pop('quick_type', 'SENT')
        context.user_data.pop('action', None)
        try:
            from utils.validation import parse_decimal_amount
            val = float(parse_decimal_amount(text.strip(), allow_zero=False))
        except Exception as err:
            await update.message.reply_text(f"❌ Invalid amount: {err}. Please try again:")
            return

        cat = get_payee_category(payee) or "General"
        from database.models import Transaction
        t = Transaction(
            amount=val,
            transaction_type=tt,
            person_name=payee,
            category=cat,
            confidence=100
        )
        pid = uuid.uuid4().hex[:10]
        pending_transactions[pid] = t
        card_text = format_receipt_card(t)
        await update.message.reply_text(
            card_text,
            reply_markup=get_confirmation_card_keyboard(pid),
            parse_mode='HTML'
        )
        return

    elif pending_action == 'waiting_quick_text':
        context.user_data.pop('action', None)
        # Process natural text through process_transaction
        transaction, conf = process_transaction(text, "", "", "")
        if transaction and transaction.amount and transaction.amount > 0:
            pid = uuid.uuid4().hex[:10]
            pending_transactions[pid] = transaction
            card_text = format_receipt_card(transaction)
            await update.message.reply_text(
                card_text,
                reply_markup=get_confirmation_card_keyboard(pid),
                parse_mode='HTML'
            )
            return

    elif pending_action == 'waiting_rec_add':
        context.user_data.pop('action', None)
        raw_parts = [p.strip() for p in text.split(",")]
        if len(raw_parts) < 2:
            await update.message.reply_text("❌ Please provide at least Payee and Amount separated by comma (e.g. <code>Netflix, 649, Monthly</code>).", parse_mode='HTML')
            return
        payee = raw_parts[0]
        try:
            from utils.validation import parse_decimal_amount
            amt = float(parse_decimal_amount(raw_parts[1], allow_zero=False))
        except Exception as err:
            await update.message.reply_text(f"❌ Invalid amount: {err}")
            return
        freq = raw_parts[2].upper() if len(raw_parts) > 2 and raw_parts[2].upper() in ('DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY') else 'MONTHLY'
        from services.recurring_service import add_recurring_payment, get_upcoming_recurring
        from bot.commands import render_recurring_overview_text
        from bot.keyboards import get_recurring_menu_keyboard
        rec_id = add_recurring_payment(payee, amt, frequency=freq)
        upcoming = get_upcoming_recurring(30)
        await update.message.reply_text(
            f"✅ <b>Recurring Payment #{rec_id} Created!</b>\n"
            f"• <b>Payee:</b> {html.escape(payee)}\n"
            f"• <b>Amount:</b> {format_currency(amt)}\n"
            f"• <b>Frequency:</b> {freq.capitalize()}\n\n"
            f"{render_recurring_overview_text()}",
            reply_markup=get_recurring_menu_keyboard(upcoming_items=upcoming),
            parse_mode='HTML'
        )
        return

    # Check quick menu trigger
    if text.strip().lower() in ('menu', 'home', 'start'):
        await update.message.reply_text(render_home_menu_text(), reply_markup=get_home_menu_keyboard(), parse_mode='HTML')
        return

    # Check short text entry: e.g. "120 dosa", "+500 salary", "-45 tea", "coffee 15"
    short_parsed = parse_short_entry(text)
    if short_parsed:
        if not await require_admin(update):
            return
        amt, tx_type, name = short_parsed
        from database.models import Transaction
        now_dt = get_current_time_in_tz()
        
        # Category heuristics & payee memory
        cat = get_payee_category(name)
        if not cat:
            lower_name = name.lower()
            if any(w in lower_name for w in ('dosa', 'tea', 'coffee', 'meals', 'lunch', 'canteen', 'cafe', 'food', 'snack', 'breakfast', 'dinner', 'vada', 'poori', 'chapathi', 'juice', 'ice cream')):
                cat = "Food & Dining"
            elif any(w in lower_name for w in ('salary', 'dividend', 'bonus', 'freelance', 'interest')):
                cat = "Salary"
            elif any(w in lower_name for w in ('auto', 'uber', 'ola', 'metro', 'bus', 'fuel', 'petrol')):
                cat = "Transport"
            elif any(w in lower_name for w in ('grocery', 'groceries', 'milk', 'vegetables', 'fruits')):
                cat = "Groceries"
            else:
                cat = "General"

        tx = Transaction(
            transaction_type=tx_type,
            amount=amt,
            person_name=name.title(),
            category=cat,
            transaction_date=now_dt.date(),
            transaction_time=now_dt.strftime("%I:%M %p"),
            payment_app="Short Text Entry"
        )
        if tx_type == 'SENT':
            tx.recipient_name = name.title()
        else:
            tx.sender_name = name.title()

        success = commit_transaction(tx)
        if success:
            arrow = "←" if tx_type == "RECEIVED" else "→"
            await update.message.reply_text(
                f"✅ <b>Payment Saved! #{tx.id}</b>\n"
                "━━━━━━━━━━━━━━\n"
                f"💸 <b>{format_currency(amt)}</b> {arrow} <b>{html.escape(name.title())}</b>\n"
                f"🏷 {html.escape(cat)}   📅 Today, {now_dt.strftime('%I:%M %p')}\n"
                f"💼 <b>Balance:</b> <b>{format_currency(tx.balance_after)}</b>",
                reply_markup=get_quick_undo_keyboard(tx.id),
                parse_mode='HTML'
            )
            from services.task_manager import schedule_debounced_backup
            schedule_debounced_backup(context.bot)
            return

    # Try parsing text as a transaction
    try:
        transaction, confidence = process_transaction(text, "", message_id, chat_id)
        if transaction.amount and transaction.amount > 0 and transaction.transaction_type:
            if not await require_admin(update):
                return
            if confidence >= 80:
                from services.cafeteria_service import is_cafeteria_payment
                from bot.keyboards import get_cafeteria_selection_keyboard
                if is_cafeteria_payment(transaction.person_name, transaction.upi_id, transaction.ocr_text):
                    transaction.category = "Food & Dining"

                success = commit_transaction(transaction)
                if success:
                    response = format_success_message(transaction)
                    markup = None
                    if is_cafeteria_payment(transaction.person_name, transaction.upi_id, transaction.ocr_text):
                        markup = get_cafeteria_selection_keyboard(transaction.id, transaction.amount)
                        response += "\n\n🍽️ <b>Cafeteria Bill Detected!</b> Select your menu item below:"

                    await update.message.reply_text(response, reply_markup=markup, parse_mode='HTML')
                    from services.task_manager import schedule_debounced_backup
                    schedule_debounced_backup(context.bot)
                else:
                    await update.message.reply_text("⚠️ Transaction already recorded.")
                return
            elif confidence >= 30:
                tx_id = uuid.uuid4().hex[:10]
                pending_transactions[tx_id] = transaction
                card_text = format_receipt_card(transaction)
                await update.message.reply_text(card_text, reply_markup=get_confirmation_card_keyboard(tx_id), parse_mode='HTML')
                return
    except Exception as e:
        logger.error(f"Error parsing text message: {e}", exc_info=True)
    
    # Clean app menu response if message wasn't recognized
    await update.message.reply_text(
        "👋 <b>Payment Tracker Bot</b>\n\n"
        "• Send a <b>screenshot</b> of any payment receipt.\n"
        "• Type a short entry like <code>120 dosa</code> or <code>+500 salary</code>.\n"
        "• Or tap <b>Menu</b> below to open the interactive app:",
        reply_markup=get_home_menu_keyboard(),
        parse_mode='HTML'
    )
