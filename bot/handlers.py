from telegram import Update
from telegram.ext import ContextTypes
import os
import uuid
import json
import asyncio
from config import TELEGRAM_USER_ID, IMAGE_DIR, logger
from bot.commands import is_authorized
from bot.keyboards import get_confirmation_keyboard
from ocr.extractor import perform_ocr
from services.transaction_service import process_transaction, commit_transaction
from services.backup_service import backup_to_telegram
from utils.currency import format_currency

# In-memory store for pending transactions awaiting confirmation
pending_transactions = {}

async def deliver_response(status_msg, message, text: str, reply_markup=None, parse_mode=None):
    """Safely updates status_msg or sends a new reply if edit_text fails or is flood controlled."""
    try:
        await status_msg.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as err:
        logger.warning(f"Could not edit status message ({err}); falling back to new reply.")
        try:
            await message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception:
            # Fallback without parse_mode if formatting entity error occurred
            try:
                await message.reply_text(text, reply_markup=reply_markup)
            except Exception as final_err:
                logger.error(f"Failed to deliver message: {final_err}")

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles incoming images (screenshots) with non-blocking OCR and resilient responses."""
    if not await is_authorized(update): return
    
    message = update.message
    chat_id = str(message.chat_id)
    message_id = str(message.message_id)
    
    # Get the file (photo or document)
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document and message.document.mime_type.startswith('image/'):
        file_id = message.document.file_id
    else:
        return # Ignore non-images
        
    status_msg = await message.reply_text("🔍 Analyzing screenshot (OCR)...")
    
    try:
        # Download image with resilient timeout and retry
        ext = ".jpg" # Default
        if message.document:
            ext = os.path.splitext(message.document.file_name)[1] or ".jpg"
            
        filename = f"tx_{uuid.uuid4().hex[:8]}{ext}"
        image_path = IMAGE_DIR / filename
        
        for attempt in range(2):
            try:
                file = await context.bot.get_file(file_id, read_timeout=60.0, connect_timeout=30.0)
                await file.download_to_drive(custom_path=image_path, read_timeout=60.0, connect_timeout=30.0)
                break
            except Exception as dl_err:
                if attempt == 1:
                    raise dl_err
                await asyncio.sleep(1)
        
        # Send typing action to keep Telegram active without triggering edit flood control
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass

        # Perform OCR in background thread with a timeout so it can't hang forever
        try:
            raw_text = await asyncio.wait_for(
                asyncio.to_thread(perform_ocr, str(image_path)),
                timeout=60.0  # Hard 60-second limit
            )
        except asyncio.TimeoutError:
            logger.error(f"OCR timed out after 60s on {image_path}")
            await deliver_response(status_msg, message, "⏱️ OCR took too long. The image might be too large or complex. Please try a clearer/smaller screenshot.")
            # Clean up image on timeout too
            try:
                if os.path.exists(image_path):
                    os.remove(image_path)
            except OSError:
                pass
            return

        # Delete the image immediately after OCR — text is extracted, the file
        # is no longer needed.  This saves disk space on Render's free plan.
        try:
            if os.path.exists(image_path):
                os.remove(image_path)
                logger.info(f"Cleaned up image after OCR: {image_path}")
        except OSError as cleanup_err:
            logger.warning(f"Could not delete image {image_path}: {cleanup_err}")

        if not raw_text or not raw_text.strip():
            await deliver_response(status_msg, message, "❌ Could not extract any readable text from the image. Please upload a clearer screenshot.")
            return
            
        # Process Transaction
        caption = message.caption or ""
        transaction, confidence = process_transaction(raw_text, "", message_id, chat_id, caption=caption)
        
        # Basic validation
        if not transaction.amount or transaction.amount <= 0:
            await deliver_response(status_msg, message, "⚠️ Could not detect a valid amount. Please provide a clearer screenshot or enter manually.")
            return
            
        if not transaction.transaction_type:
            await deliver_response(status_msg, message, f"⚠️ Transaction type (SENT/RECEIVED) could not be determined reliably.\nAmount found: {format_currency(transaction.amount)}")
            return
            
        # Check if already recorded (duplicate prevention with friendly informative response)
        from database.queries import get_transaction_by_reference
        if transaction.reference_number:
            existing = get_transaction_by_reference(transaction.reference_number)
            if existing:
                date_str = format_display_date(existing['transaction_date'])
                person = existing['person_name'] or "Unknown"
                amt_str = format_currency(existing['amount'])
                notice = ""
                dup_markup = None
                if abs(float(existing['amount']) - float(transaction.amount)) > 0.01:
                    notice = f"\n\n⚠️ *Note:* Receipt shows *{format_currency(transaction.amount)}*, but existing record has *{amt_str}*."
                    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                    dup_markup = InlineKeyboardMarkup([
                        [InlineKeyboardButton(f"🔄 Correct to {format_currency(transaction.amount)}", callback_data=f"correct_amount:{existing['id']}:{transaction.amount}")],
                        [InlineKeyboardButton("🗑️ Delete Old Record", callback_data=f"delete_confirm:{existing['id']}")]
                    ])
                
                dup_text = (
                    "ℹ️ *Transaction Already Recorded*\n\n"
                    f"• Type: {existing['transaction_type']}\n"
                    f"• Person: {person}\n"
                    f"• Amount: {amt_str}\n"
                    f"• Date: {date_str} {existing['transaction_time'] or ''}\n"
                    f"• Reference: `{existing['reference_number']}`\n"
                    f"• Balance After: {format_currency(existing['balance_after'])}{notice}\n\n"
                    f"💡 To update or edit this transaction, send `/edit #{existing['id']}`."
                )
                await deliver_response(status_msg, message, dup_text, reply_markup=dup_markup, parse_mode='Markdown')
                return

        # Confidence check
        if confidence >= 80:
            # High confidence, save automatically
            success = commit_transaction(transaction)
            if success:
                response = format_success_message(transaction)
                await deliver_response(status_msg, message, response, parse_mode=None)
                try:
                    asyncio.create_task(backup_to_telegram(context.bot))
                except Exception:
                    pass
            else:
                await deliver_response(status_msg, message, "⚠️ Transaction already recorded.")
        elif confidence >= 40:
            # Medium confidence, ask for confirmation
            tx_id = uuid.uuid4().hex
            pending_transactions[tx_id] = transaction
            
            date_display = transaction.transaction_date.strftime("%d %b %Y") if hasattr(transaction.transaction_date, 'strftime') else (str(transaction.transaction_date) if transaction.transaction_date else "Today")
            confirm_text = (
                "⚠️ *Please confirm this transaction*\n\n"
                f"Type: {transaction.transaction_type}\n"
                f"Amount: {format_currency(transaction.amount)}\n"
                f"Person: {transaction.person_name or 'Unknown'}\n"
                f"Date: {date_display}\n"
                f"Reference: {transaction.reference_number or 'N/A'}\n\n"
                "Save this transaction?"
            )
            keyboard = get_confirmation_keyboard(tx_id)
            await deliver_response(status_msg, message, confirm_text, reply_markup=keyboard, parse_mode='Markdown')
        else:
            await deliver_response(status_msg, message, "❌ Confidence too low to process automatically. Please check the image.")

    except Exception as e:
        logger.error(f"Error handling image: {e}", exc_info=True)
        await deliver_response(status_msg, message, f"❌ Error processing image: {e}")

from database.queries import (
    get_transaction_by_id, update_transaction, delete_transaction,
    search_transactions, get_monthly_summary, get_recent_transactions
)
from services.balance_service import recalculate_all_balances
from bot.keyboards import (
    get_confirmation_keyboard, get_edit_fields_keyboard, get_delete_confirm_keyboard,
    get_filter_keyboard, get_sort_keyboard
)
from utils.dates import parse_date, get_current_time_in_tz, format_display_date
from utils.currency import parse_amount, format_currency
from datetime import timedelta


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles button presses from inline keyboards."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if ":" not in data:
        return
        
    parts = data.split(":")
    action = parts[0]
    
    # 1. OCR Confirmation / Cancellation
    if action in ("confirm_tx", "cancel_tx"):
        tx_id = parts[1]
        transaction = pending_transactions.get(tx_id)
        
        if not transaction:
            await query.edit_message_text("❌ Transaction expired or no longer available.")
            return
            
        if action == "confirm_tx":
            success = commit_transaction(transaction)
            if success:
                response = format_success_message(transaction)
                await query.edit_message_text(response, parse_mode='Markdown')
                try:
                    asyncio.create_task(backup_to_telegram(context.bot))
                except Exception:
                    pass
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
            f"✏️ *Editing Transaction*\n\n"
            f"Type: {tx['transaction_type']}\n"
            f"Amount: {format_currency(tx['amount'])}\n"
            f"Person: {person}\n"
            f"Date: {date_str}\n\n"
            "Select what you would like to edit:"
        )
        await query.edit_message_text(text, reply_markup=get_edit_fields_keyboard(tx_id), parse_mode='Markdown')
        
    elif action == "select_delete":
        tx_id = int(parts[1])
        tx = get_transaction_by_id(tx_id)
        if not tx:
            await query.edit_message_text("❌ Transaction not found.")
            return
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
        await query.edit_message_text(text, reply_markup=get_delete_confirm_keyboard(tx_id), parse_mode='Markdown')
        
    elif action in ("select_edit_cancel", "select_delete_cancel"):
        context.user_data.pop('action', None)
        await query.edit_message_text("❌ Action cancelled.")

    # 3. Edit Field Selection
    elif action == "edit_field":
        tx_id = int(parts[1])
        field = parts[2]
        
        context.user_data['action'] = 'waiting_edit_value'
        context.user_data['edit_tx_id'] = tx_id
        context.user_data['edit_field'] = field
        
        field_prompts = {
            'amount': 'new amount (e.g. `4500`)',
            'person': 'new recipient or sender name (e.g. `Balaji`)',
            'date': 'new date (e.g. `05/09/2026`, `05 Sep 2026`, or `yesterday`)',
            'type': 'new transaction type (`SENT` or `RECEIVED`)',
            'ref': 'new reference number or UTR'
        }
        prompt = field_prompts.get(field, f"new value for `{field}`")
        await query.edit_message_text(
            f"✏️ *Editing Transaction*\n\n"
            f"Please reply with the {prompt}:",
            parse_mode='Markdown'
        )
        
    elif action == "edit_cancel":
        context.user_data.pop('action', None)
        await query.edit_message_text("❌ Edit cancelled.")
        
    # 4. Delete Confirmation
    elif action == "delete_confirm":
        tx_id = int(parts[1])
        tx = get_transaction_by_id(tx_id)
        if not tx:
            await query.edit_message_text("❌ Transaction not found.")
            return
            
        success = delete_transaction(tx_id)
        if success:
            new_bal = recalculate_all_balances()
            try:
                asyncio.create_task(backup_to_telegram(context.bot))
            except Exception:
                pass
            await query.edit_message_text(
                f"🗑️ *Transaction deleted successfully.*\n\n"
                f"💰 *Updated Balance:* `{format_currency(new_bal)}`",
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text("❌ Failed to delete transaction.")
            
    elif action == "delete_cancel":
        context.user_data.pop('action', None)
        await query.edit_message_text("❌ Deletion cancelled.")

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
            try:
                asyncio.create_task(backup_to_telegram(context.bot))
            except Exception:
                pass
            await query.edit_message_text(
                f"✅ *Transaction updated successfully!*\n\n"
                f"• Amount corrected to: *{format_currency(new_amt)}*\n"
                f"💰 *Updated Current Balance:* `{format_currency(new_bal)}`",
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text("❌ Failed to update transaction.")

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
                f"📊 *Monthly Analytics - {now.strftime('%B %Y')}*\n\n"
                f"🔴 Total Sent: {format_currency(stats['total_sent'])}\n"
                f"🟢 Total Received: {format_currency(stats['total_received'])}\n"
                f"📈 Net Savings: {format_currency(stats['net_savings'])}\n\n"
                f"🔢 Total Transactions: {stats['tx_count']}\n"
                f"🏆 Top Recipient: {top_p}"
            )
            await query.edit_message_text(text, reply_markup=get_filter_keyboard(), parse_mode='Markdown')
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
            text = "🔍 *Transactions (With IDs & Ref)*\n\n"
            for t in txs:
                text += (
                    f"🆔 *ID: #{t['id']}* | {t['transaction_type']}\n"
                    f"📅 {t['transaction_date']} | 👤 {t['person_name'] or 'N/A'}\n"
                    f"💵 {format_currency(t['amount'])} | 🔢 Ref: `{t['reference_number'] or 'N/A'}`\n\n"
                )
            await query.edit_message_text(text, reply_markup=get_filter_keyboard(), parse_mode='Markdown')
            return
        elif filter_type == "open_sort":
            await query.edit_message_text("🔀 *Choose Sorting Order:*", reply_markup=get_sort_keyboard(), parse_mode='Markdown')
            return
        else:
            txs = []
            title = "Filtered Transactions"
            
        if not txs:
            await query.edit_message_text(f"No transactions found for *{title}*.", reply_markup=get_filter_keyboard(), parse_mode='Markdown')
            return
            
        total_amt = sum(t['amount'] for t in txs)
        text = f"📜 *{title}* (Total: {format_currency(total_amt)})\n\n"
        for t in txs[:10]:
            person = t['person_name'] or "Unknown"
            date_s = format_display_date(t['transaction_date'])
            time_str = f" | ⏰ {t['transaction_time']}" if t['transaction_time'] and t['transaction_time'] != 'Unknown Time' else ""
            type_badge = "🔴 SENT" if t['transaction_type'] == 'SENT' else "🟢 RECEIVED"
            text += f"• *{date_s}*{time_str} | {type_badge}\n👤 {person} | 💵 {format_currency(t['amount'])}\n\n"
        await query.edit_message_text(text, reply_markup=get_filter_keyboard(), parse_mode='Markdown')

    # 6. Sorting Callbacks
    elif action == "sort":
        sort_by = parts[1]
        if sort_by == "back_filters":
            await query.edit_message_text("🎛️ *Filter & Sort Transactions:*\n\nChoose an option below:", reply_markup=get_filter_keyboard(), parse_mode='Markdown')
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
            
        text = f"🔀 *Sorted by: {sort_names.get(sort_by, sort_by)}*\n\n"
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
        await query.edit_message_text(text, reply_markup=get_sort_keyboard(), parse_mode='Markdown')


def format_success_message(t) -> str:
    icon = "✅ Payment Recorded" if t.transaction_type == 'SENT' else "✅ Payment Received"
    person_label = "Recipient" if t.transaction_type == 'SENT' else "Sender"
    person_name = t.person_name or "Unknown"
    if hasattr(t.transaction_date, 'strftime'):
        date_str = t.transaction_date.strftime("%d %b %Y")
    elif t.transaction_date:
        date_str = str(t.transaction_date)
    else:
        date_str = "Unknown"
    
    return (
        f"{icon}\n\n"
        f"Type: {t.transaction_type}\n"
        f"{person_label}: {person_name}\n"
        f"Amount: {format_currency(t.amount)}\n"
        f"Date: {date_str}\n"
        f"Time: {t.transaction_time or 'N/A'}\n"
        f"Reference: {t.reference_number or 'N/A'}\n\n"
        f"Balance Before: {format_currency(t.balance_before)}\n"
        f"Balance After: {format_currency(t.balance_after)}"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles incoming text messages, interactive steps, and payment parsing."""
    if not await is_authorized(update): return
    if not update.message or not update.message.text: return
    
    text = update.message.text.strip()
    chat_id = str(update.message.chat_id)
    message_id = str(update.message.message_id)
    
    # Normalize commands e.g. \date, /search, /monthly, /filter, /sort, /details
    from bot.commands import (
        edit_command, delete_command, history_command, balance_command,
        date_command, search_command, monthly_command, filter_command, sort_command, details_command, amount_command
    )
    cmd_lower = text.lower()
    
    if cmd_lower in (r'\edit', 'edit', '/edit'):
        await edit_command(update, context)
        return
    elif cmd_lower in (r'\delete', 'delete', '/delete'):
        await delete_command(update, context)
        return
    elif cmd_lower in (r'\history', 'history', '/history'):
        await history_command(update, context)
        return
    elif cmd_lower in (r'\balance', 'balance', '/balance'):
        await balance_command(update, context)
        return
    elif cmd_lower in (r'\date', 'date', '/date'):
        await date_command(update, context)
        return
    elif cmd_lower in (r'\search', 'search', '/search', 'find', '/find'):
        await search_command(update, context)
        return
    elif cmd_lower in (r'\amount', 'amount', '/amount', r'\amt', 'amt', '/amt'):
        await amount_command(update, context)
        return
    elif cmd_lower in (r'\filter', 'filter', '/filter'):
        await filter_command(update, context)
        return
    elif cmd_lower in (r'\sort', 'sort', '/sort'):
        await sort_command(update, context)
        return
    elif cmd_lower in (r'\monthly', 'monthly', '/monthly', r'\stats', 'stats', '/stats'):
        await monthly_command(update, context)
        return
    elif cmd_lower in (r'\details', 'details', '/details', r'\ids', 'ids', '/ids'):
        await details_command(update, context)
        return
    elif text.startswith(r'\edit ') or text.startswith('edit '):
        context.args = text.split()[1:]
        await edit_command(update, context)
        return
    elif text.startswith(r'\delete ') or text.startswith('delete '):
        context.args = text.split()[1:]
        await delete_command(update, context)
        return
    elif text.startswith(r'\date ') or text.startswith('date '):
        context.args = text.split()[1:]
        await date_command(update, context)
        return
    elif text.startswith(r'\search ') or text.startswith('search '):
        context.args = text.split()[1:]
        await search_command(update, context)
        return
    elif text.startswith(r'\amount ') or text.startswith('amount ') or text.startswith(r'\amt ') or text.startswith('amt '):
        context.args = text.split()[1:]
        await amount_command(update, context)
        return
    elif text.startswith(r'\monthly ') or text.startswith('monthly '):
        context.args = text.split()[1:]
        await monthly_command(update, context)
        return

    # If the message is just a standalone date (e.g. 05/09/2026 or 05-09-2026) without payment keywords
    if not any(w in cmd_lower for w in ('paid', 'received', 'sent', 'transfer', 'credit', 'debit', 'rs', 'inr', '₹')):
        pure_d = parse_date(text)
        if pure_d and ('/' in text or '-' in text or len(text.split()) >= 2):
            context.args = [text]
            await date_command(update, context)
            return

    # If the message is just a standalone number / amount (e.g. 500, 5000, ₹6200) without action keywords
    import re
    clean_amt_str = re.sub(r'[₹,\s]', '', cmd_lower.replace('rs', '').replace('inr', '')).strip()
    if re.match(r'^\d+(\.\d+)?$', clean_amt_str) and not any(w in cmd_lower for w in ('paid', 'received', 'sent', 'to', 'from')) and not context.user_data.get('action'):
        context.args = [clean_amt_str]
        await amount_command(update, context)
        return


    # Check active conversational state
    pending_action = context.user_data.get('action')
    
    if pending_action == 'waiting_edit_id':
        clean_id_str = text.replace('#', '').strip()
        if clean_id_str.isdigit():
            num = int(clean_id_str)
            tx = get_transaction_by_id(num)
            if not tx:
                recent_ids = context.user_data.get('recent_edit_ids') or [t['id'] for t in get_recent_transactions(limit=10)]
                if 1 <= num <= len(recent_ids):
                    tx = get_transaction_by_id(recent_ids[num - 1])
            if tx:
                tx_id = tx['id']
                context.user_data.pop('action', None)
                context.user_data.pop('recent_edit_ids', None)
                date_str = tx['transaction_date'] or "Today"
                person = tx['person_name'] or "Unknown"
                msg = (
                    f"✏️ *Editing Transaction*\n\n"
                    f"Type: {tx['transaction_type']}\n"
                    f"Amount: {format_currency(tx['amount'])}\n"
                    f"Person: {person}\n"
                    f"Date: {date_str}\n\n"
                    "Select what you would like to edit:"
                )
                await update.message.reply_text(msg, reply_markup=get_edit_fields_keyboard(tx_id), parse_mode='Markdown')
                return
            else:
                await update.message.reply_text("❌ Transaction not found. Please send a valid number or ID:")
                return
                
    elif pending_action == 'waiting_delete_id':
        clean_id_str = text.replace('#', '').strip()
        if clean_id_str.isdigit():
            num = int(clean_id_str)
            tx = get_transaction_by_id(num)
            if not tx:
                recent_ids = context.user_data.get('recent_delete_ids') or [t['id'] for t in get_recent_transactions(limit=10)]
                if 1 <= num <= len(recent_ids):
                    tx = get_transaction_by_id(recent_ids[num - 1])
            if tx:
                tx_id = tx['id']
                context.user_data.pop('action', None)
                context.user_data.pop('recent_delete_ids', None)
                date_str = tx['transaction_date'] or "Today"
                person = tx['person_name'] or "Unknown"
                msg = (
                    f"🗑️ *Delete Transaction*\n\n"
                    f"Type: {tx['transaction_type']}\n"
                    f"Amount: {format_currency(tx['amount'])}\n"
                    f"Person: {person}\n"
                    f"Date: {date_str}\n\n"
                    "Are you sure you want to delete this transaction?"
                )
                await update.message.reply_text(msg, reply_markup=get_delete_confirm_keyboard(tx_id), parse_mode='Markdown')
                return
            else:
                await update.message.reply_text("❌ Transaction not found. Please send a valid number or ID:")
                return
                
    elif pending_action == 'waiting_edit_value':
        tx_id = context.user_data.get('edit_tx_id')
        field = context.user_data.get('edit_field')
        
        updates = {}
        needs_recalc = False
        
        if field == 'amount':
            new_amt = parse_amount(text)
            if new_amt <= 0:
                await update.message.reply_text("❌ Invalid amount. Please try again:")
                return
            updates['amount'] = new_amt
            needs_recalc = True
        elif field == 'person':
            updates['person_name'] = text.title()
            tx = get_transaction_by_id(tx_id)
            if tx and tx['transaction_type'] == 'SENT':
                updates['recipient_name'] = text.title()
            else:
                updates['sender_name'] = text.title()
        elif field == 'type':
            new_type = text.upper()
            if new_type not in ('SENT', 'RECEIVED'):
                await update.message.reply_text("❌ Please enter `SENT` or `RECEIVED`:")
                return
            updates['transaction_type'] = new_type
            needs_recalc = True
        elif field == 'date':
            parsed_d = parse_date(text)
            if not parsed_d:
                await update.message.reply_text("❌ Invalid date. Example: `05/09/2026` or `yesterday`:")
                return
            updates['transaction_date'] = parsed_d
        elif field == 'ref':
            updates['reference_number'] = text
            
        success = update_transaction(tx_id, updates)
        context.user_data.pop('action', None)
        context.user_data.pop('edit_tx_id', None)
        context.user_data.pop('edit_field', None)
        
        if success:
            bal_msg = ""
            if needs_recalc:
                new_bal = recalculate_all_balances()
                bal_msg = f"\n💰 Updated Current Balance: {format_currency(new_bal)}"
            await update.message.reply_text(f"✅ Transaction updated successfully!{bal_msg}", parse_mode='Markdown')
            return
        else:
            await update.message.reply_text("❌ Failed to update transaction.")
            return

    # Try parsing text as a transaction
    try:
        transaction, confidence = process_transaction(text, "", message_id, chat_id)
        if transaction.amount and transaction.amount > 0 and transaction.transaction_type:
            if confidence >= 80:
                success = commit_transaction(transaction)
                if success:
                    response = format_success_message(transaction)
                    await update.message.reply_text(response, parse_mode='Markdown')
                    try:
                        asyncio.create_task(backup_to_telegram(context.bot))
                    except Exception:
                        pass
                else:
                    await update.message.reply_text("⚠️ Transaction already recorded.")
                return
            elif confidence >= 30:
                tx_id = uuid.uuid4().hex
                pending_transactions[tx_id] = transaction
                date_display = transaction.transaction_date.strftime("%d %b %Y") if hasattr(transaction.transaction_date, 'strftime') else (str(transaction.transaction_date) if transaction.transaction_date else "Today")
                confirm_text = (
                    "⚠️ *Please confirm this transaction*\n\n"
                    f"Type: {transaction.transaction_type}\n"
                    f"Amount: {format_currency(transaction.amount)}\n"
                    f"Person: {transaction.person_name or 'Unknown'}\n"
                    f"Date: {date_display}\n\n"
                    "Save this transaction?"
                )
                keyboard = get_confirmation_keyboard(tx_id)
                await update.message.reply_text(confirm_text, reply_markup=keyboard, parse_mode='Markdown')
                return
    except Exception as e:
        logger.error(f"Error parsing text message: {e}", exc_info=True)
    
    # General help message if no payment detected
    await update.message.reply_text(
        "👋 You can send me:\n"
        "1. A **screenshot** of your payment receipt.\n"
        "2. A **text message** describing a payment (e.g. `Paid 500 to Ramesh` or `Received 6200 from Johnson`).\n\n"
        "Commands:\n"
        "/balance - View balance\n"
        "/history - View transactions\n"
        "/edit - Edit a transaction\n"
        "/delete - Delete a transaction",
        parse_mode='Markdown'
    )
